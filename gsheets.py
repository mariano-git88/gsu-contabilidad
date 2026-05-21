"""
gsheets.py — Persistencia del histórico de costos internos en Google Sheets.

Encapsula la integración con `gspread`. Funciones puras: no importan
streamlit, reciben los secrets como dict.

Estructura del Sheet:
  - Tab "costos_historico": append-only. Una fila por (sku, fecha_vigencia).
    Cada carga agrega un lote de filas, nunca pisa las anteriores.

Schema de "costos_historico":
  sku | costo | fecha_vigencia_desde | fecha_carga | usuario | nota

  - sku: string, SKU canónico (mayúsculas, sin espacios).
  - costo: float, neto sin IVA (mismas unidades que `PrecioFinal/1.22` de
    Contabilium para que cuadre con `monto` neto de las ventas).
  - fecha_vigencia_desde: ISO "YYYY-MM-DD". El costo aplica desde esa
    fecha en adelante hasta que aparezca otro costo más nuevo.
  - fecha_carga: ISO "YYYY-MM-DD HH:MM". Cuándo se subió el archivo.
  - usuario: string (lo setea la app, por ahora "mariano" fijo).
  - nota: opcional, contexto sobre la carga (ej. "ajuste por dólar abril").

Credenciales del Service Account: dos formas:
  A. Local: `service_account_json_path` apuntando a un .json en disco.
  B. Producción (Streamlit Cloud): `service_account` como dict embebido.
Si ambas están, gana B (la del dict).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import gspread
import pandas as pd


class GsheetsError(Exception):
    """Error genérico al integrar con Google Sheets."""


class CredencialesError(GsheetsError):
    """Faltan credenciales o están mal configuradas."""


TAB_COSTOS = "costos_historico"

COSTOS_COLUMNS = [
    "sku",
    "costo",
    "fecha_vigencia_desde",
    "fecha_carga",
    "usuario",
    "nota",
]


def _resolver_credenciales(
    gsheets_section: dict, repo_root: Path | None = None
) -> dict:
    sa = gsheets_section.get("service_account")
    if sa:
        return dict(sa)

    path_str = gsheets_section.get("service_account_json_path")
    if not path_str:
        raise CredencialesError(
            "Faltan credenciales del Service Account. Configurá una de:\n"
            "  - gsheets.service_account_json_path = '.gsheets/sa.json'\n"
            "  - [gsheets.service_account] con el contenido del JSON."
        )

    p = Path(path_str)
    if not p.is_absolute():
        if repo_root is None:
            repo_root = Path(__file__).resolve().parent
        p = repo_root / p

    if not p.exists():
        raise CredencialesError(
            f"No existe el archivo de credenciales: {p}\n"
            f"Verificá la ruta en gsheets.service_account_json_path."
        )

    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise CredencialesError(f"El archivo {p} no es JSON válido: {e}") from e


def _get_client(
    gsheets_section: dict, repo_root: Path | None = None
) -> gspread.Client:
    sa_dict = _resolver_credenciales(gsheets_section, repo_root=repo_root)
    return gspread.service_account_from_dict(sa_dict)


def _open_sheet(gsheets_section: dict, repo_root: Path | None = None):
    spreadsheet_id = gsheets_section.get("spreadsheet_id")
    if not spreadsheet_id:
        raise CredencialesError("Falta gsheets.spreadsheet_id en secrets.")
    client = _get_client(gsheets_section, repo_root=repo_root)
    try:
        return client.open_by_key(spreadsheet_id)
    except gspread.exceptions.SpreadsheetNotFound as e:
        raise GsheetsError(
            f"Sheet no encontrado (id={spreadsheet_id}). Verificá el ID."
        ) from e
    except PermissionError as e:
        raise GsheetsError(
            "Sin permisos para abrir el Sheet. Compartilo con el "
            "client_email del Service Account como Editor."
        ) from e


def _ensure_worksheet(sh, title: str, rows: int = 5000, cols: int = 10):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def read_costos(gsheets_section: dict) -> pd.DataFrame:
    """Lee el histórico completo de costos. DF vacío con schema correcto
    si la tab no existe o está vacía."""
    sh = _open_sheet(gsheets_section)
    ws = _ensure_worksheet(sh, TAB_COSTOS, cols=len(COSTOS_COLUMNS))
    rows = ws.get_all_values()

    if not rows:
        ws.update("A1", [COSTOS_COLUMNS])
        return pd.DataFrame(columns=COSTOS_COLUMNS)

    headers = rows[0]
    if headers != COSTOS_COLUMNS:
        if all(not c for c in headers):
            ws.update("A1", [COSTOS_COLUMNS])
            return pd.DataFrame(columns=COSTOS_COLUMNS)
        raise GsheetsError(
            f"Encabezados inesperados en tab '{TAB_COSTOS}'. "
            f"Esperaba {COSTOS_COLUMNS}, encontró {headers}."
        )

    if len(rows) < 2:
        return pd.DataFrame(columns=COSTOS_COLUMNS)

    df = pd.DataFrame(rows[1:], columns=COSTOS_COLUMNS)
    df["costo"] = pd.to_numeric(df["costo"], errors="coerce").fillna(0.0)
    df["sku"] = df["sku"].astype(str).str.strip().str.upper()
    return df


def append_costos(
    gsheets_section: dict,
    filas: list[dict],
) -> int:
    """Append-only de un lote de costos. Nunca pisa filas existentes.

    Cada fila del lote debe tener: sku, costo, fecha_vigencia_desde.
    Opcionalmente: usuario, nota. `fecha_carga` la setea esta función
    al timestamp actual.

    Devuelve cantidad de filas escritas. Si `filas` viene vacío, 0
    y no toca el Sheet.
    """
    if not filas:
        return 0

    sh = _open_sheet(gsheets_section)
    ws = _ensure_worksheet(sh, TAB_COSTOS, cols=len(COSTOS_COLUMNS))

    existing_header = ws.row_values(1)
    if not existing_header or existing_header[: len(COSTOS_COLUMNS)] != COSTOS_COLUMNS:
        ws.update("A1", [COSTOS_COLUMNS], value_input_option="RAW")

    fecha_carga = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows_data = []
    for f in filas:
        sku = str(f.get("sku") or "").strip().upper()
        if not sku:
            continue
        rows_data.append(
            [
                sku,
                float(f.get("costo", 0.0)),
                str(f.get("fecha_vigencia_desde") or ""),
                fecha_carga,
                str(f.get("usuario") or "mariano"),
                str(f.get("nota") or ""),
            ]
        )

    if not rows_data:
        return 0

    ws.append_rows(rows_data, value_input_option="USER_ENTERED")
    return len(rows_data)


def costo_vigente_por_sku(
    df_costos: pd.DataFrame, sku: str, fecha: str
) -> float | None:
    """Devuelve el costo vigente del SKU a la `fecha` (string ISO YYYY-MM-DD).

    Lógica: el último costo cargado con `fecha_vigencia_desde <= fecha`.
    Si no hay ninguno (SKU sin historial o vigencia posterior a `fecha`),
    devuelve None — el caller decide qué hacer (excluir, warning, etc.).

    Trabaja sobre un DataFrame ya leído (no abre Sheet por SKU). El
    caller debe leer `read_costos` una sola vez y pasar el DF.
    """
    if df_costos.empty:
        return None
    sku = (sku or "").strip().upper()
    if not sku:
        return None
    candidatos = df_costos[
        (df_costos["sku"] == sku)
        & (df_costos["fecha_vigencia_desde"] <= fecha)
    ]
    if candidatos.empty:
        return None
    # Última fila por fecha_vigencia (ordenamiento lexicográfico funciona
    # bien con ISO YYYY-MM-DD). Si hubiera empate, gana la última cargada.
    ultima = candidatos.sort_values(
        ["fecha_vigencia_desde", "fecha_carga"], ascending=True
    ).iloc[-1]
    return float(ultima["costo"])
