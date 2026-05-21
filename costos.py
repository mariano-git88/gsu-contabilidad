"""
costos.py — Tab "Carga de costos": upload CSV/XLSX → Google Sheet histórico.

Flujo:
  1. Usuario sube un archivo con columnas `sku` y `costo`.
  2. Elige una `fecha_vigencia_desde` (default: hoy). TODOS los SKUs del
     archivo heredan esa fecha — la decisión la toma el usuario por carga,
     no por fila.
  3. La app valida contra el catálogo de Contabilium:
       - SKUs encontrados (OK).
       - SKUs no encontrados (warning, el usuario decide si seguir).
       - Diff vs el costo anterior cargado en el Sheet.
  4. Si el usuario confirma, escribe en `costos_historico` (append-only).

El Sheet nunca pisa filas: cada carga agrega un lote nuevo. El cálculo
de COGS (en `cogs.py`) usa el último costo con `fecha_vigencia_desde
<= fecha_factura`.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st

import api_loader
import gsheets
import productos as productos_mod


def _parse_uploaded(file) -> pd.DataFrame:
    """Lee CSV o XLSX, normaliza columnas y tipos. Levanta ValueError
    con mensaje user-friendly si el formato está mal.

    Espera columnas (case-insensitive):
      - sku    (obligatoria)
      - costo  (obligatoria, numérica)
      - nota   (opcional, string)
    """
    nombre = file.name.lower()
    if nombre.endswith(".csv"):
        df = pd.read_csv(file)
    elif nombre.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file)
    else:
        raise ValueError(
            "Formato no soportado. Subí un .csv o .xlsx."
        )

    df.columns = [c.strip().lower() for c in df.columns]

    if "sku" not in df.columns:
        raise ValueError("Falta la columna `sku` en el archivo.")
    if "costo" not in df.columns:
        raise ValueError("Falta la columna `costo` en el archivo.")

    df["sku"] = df["sku"].astype(str).str.strip().str.upper()
    df["costo"] = pd.to_numeric(df["costo"], errors="coerce")

    if "nota" not in df.columns:
        df["nota"] = ""
    else:
        df["nota"] = df["nota"].astype(str).fillna("")

    df = df[["sku", "costo", "nota"]]
    df = df[df["sku"].str.len() > 0].reset_index(drop=True)
    return df


def _build_diff(
    df_subido: pd.DataFrame,
    df_costos_actual: pd.DataFrame,
    df_catalogo: pd.DataFrame,
    fecha_vigencia: str,
) -> pd.DataFrame:
    """Construye una tabla de preview con: SKU, nombre (si está en
    catálogo), costo_anterior (último vigente al día de hoy en el Sheet),
    costo_nuevo (del archivo), delta_pct, en_catalogo (bool)."""
    catalogo_idx = df_catalogo.set_index("sku")["nombre"].to_dict() if not df_catalogo.empty else {}

    rows = []
    for _, r in df_subido.iterrows():
        sku = r["sku"]
        costo_nuevo = float(r["costo"]) if pd.notna(r["costo"]) else None
        costo_ant = gsheets.costo_vigente_por_sku(
            df_costos_actual, sku, fecha_vigencia
        )
        if costo_ant is None or costo_ant == 0:
            delta_pct = None
        elif costo_nuevo is None:
            delta_pct = None
        else:
            delta_pct = (costo_nuevo - costo_ant) / costo_ant * 100.0
        rows.append(
            {
                "sku": sku,
                "nombre": catalogo_idx.get(sku, ""),
                "en_catalogo": sku in catalogo_idx,
                "costo_anterior": costo_ant,
                "costo_nuevo": costo_nuevo,
                "delta_pct": delta_pct,
                "nota": r["nota"],
            }
        )
    return pd.DataFrame(rows)


def render() -> None:
    st.markdown("## Carga de costos")
    st.caption(
        "Subí una planilla con `sku` y `costo`. Elegí desde qué fecha "
        "aplican esos costos. La carga se agrega al histórico (nunca "
        "pisa filas anteriores). Los SKUs no incluidos en esta carga "
        "mantienen su último costo vigente."
    )

    client_id = st.secrets.get("contabilium_client_id")
    client_secret = st.secrets.get("contabilium_client_secret")
    gsheets_section = st.secrets.get("gsheets")
    if not (client_id and client_secret and gsheets_section):
        st.error(
            "Faltan secrets. Verificá `contabilium_client_id`, "
            "`contabilium_client_secret` y la sección `[gsheets]` en "
            "`.streamlit/secrets.toml`."
        )
        return

    # Convertir AttrDict de secrets a dict puro
    gsheets_section = dict(gsheets_section)

    st.markdown("### 1. Subir archivo")
    file = st.file_uploader(
        "Planilla de costos (.csv o .xlsx). Columnas requeridas: `sku`, `costo`. Opcional: `nota`.",
        type=["csv", "xlsx"],
    )
    if not file:
        st.info(
            "Tip: en la pestaña **Productos** podés descargar una plantilla "
            "pre-llenada con todos los SKUs activos."
        )
        _render_historial(gsheets_section)
        return

    try:
        df_subido = _parse_uploaded(file)
    except ValueError as e:
        st.error(str(e))
        return

    if df_subido.empty:
        st.warning("El archivo no tiene filas válidas.")
        return

    n_sin_costo = df_subido["costo"].isna().sum()
    if n_sin_costo:
        st.warning(
            f"{n_sin_costo} fila(s) tienen costo vacío o no numérico. "
            "Se van a ignorar al guardar."
        )

    st.success(f"Archivo leído: {len(df_subido)} filas válidas.")

    st.markdown("### 2. Fecha de vigencia y nota")
    col1, col2 = st.columns([1, 2])
    with col1:
        fecha_vig = st.date_input(
            "Vigente desde",
            value=date.today(),
            help="Todos los SKUs de esta carga aplican desde esta fecha.",
        )
    with col2:
        nota_global = st.text_input(
            "Nota (opcional)",
            placeholder="ej. ajuste por dólar abril 2026",
        )
    fecha_vig_iso = fecha_vig.isoformat()

    st.markdown("### 3. Previsualizar")

    with st.spinner("Leyendo catálogo de Contabilium y costos previos…"):
        try:
            df_catalogo = productos_mod._sync_catalogo(client_id, client_secret)
        except (api_loader.AuthError, api_loader.ApiError) as e:
            st.error(f"No se pudo leer el catálogo: {e}")
            return
        try:
            df_costos_actual = gsheets.read_costos(gsheets_section)
        except gsheets.GsheetsError as e:
            st.error(f"No se pudo leer el Google Sheet: {e}")
            return

    df_diff = _build_diff(
        df_subido[df_subido["costo"].notna()],
        df_costos_actual,
        df_catalogo,
        fecha_vig_iso,
    )

    n_fuera_catalogo = (~df_diff["en_catalogo"]).sum()
    n_nuevos = df_diff["costo_anterior"].isna().sum()
    n_cambiados = df_diff[df_diff["costo_anterior"].notna() & (df_diff["delta_pct"].abs() > 0.01)].shape[0] if "delta_pct" in df_diff else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Total a cargar", f"{len(df_diff):,}")
    c2.metric("SKUs nuevos (sin costo previo)", f"{n_nuevos:,}")
    c3.metric("SKUs no encontrados en catálogo", f"{n_fuera_catalogo:,}")

    if n_fuera_catalogo:
        st.warning(
            f"{n_fuera_catalogo} SKU(s) no figuran en el catálogo activo de "
            "Contabilium. Se pueden cargar igual, pero verificá que no sean "
            "errores de tipeo."
        )

    st.dataframe(
        df_diff,
        use_container_width=True,
        hide_index=True,
        column_config={
            "sku": st.column_config.TextColumn("SKU"),
            "nombre": st.column_config.TextColumn("Nombre"),
            "en_catalogo": st.column_config.CheckboxColumn("En catálogo"),
            "costo_anterior": st.column_config.NumberColumn(
                "Costo anterior", format="$ %.2f"
            ),
            "costo_nuevo": st.column_config.NumberColumn(
                "Costo nuevo", format="$ %.2f"
            ),
            "delta_pct": st.column_config.NumberColumn(
                "Δ %", format="%.1f%%"
            ),
        },
    )

    st.markdown("### 4. Guardar")
    if st.button("Confirmar y guardar en Google Sheet", type="primary"):
        filas = []
        for _, r in df_diff.iterrows():
            filas.append(
                {
                    "sku": r["sku"],
                    "costo": r["costo_nuevo"],
                    "fecha_vigencia_desde": fecha_vig_iso,
                    "usuario": "mariano",
                    "nota": r["nota"] if r["nota"] else nota_global,
                }
            )
        try:
            n = gsheets.append_costos(gsheets_section, filas)
        except gsheets.GsheetsError as e:
            st.error(f"Falló la escritura: {e}")
            return
        st.success(f"Listo. Se agregaron {n} filas al histórico.")
        st.cache_data.clear()  # invalidar reads cacheados

    _render_historial(gsheets_section)


def _render_historial(gsheets_section: dict) -> None:
    """Tabla resumen de cargas pasadas, para ver qué hay en el Sheet."""
    st.markdown("### Histórico de cargas en el Sheet")
    try:
        df = gsheets.read_costos(gsheets_section)
    except gsheets.GsheetsError as e:
        st.error(f"No se pudo leer el Google Sheet: {e}")
        return

    if df.empty:
        st.info("El Sheet de costos está vacío. Subí tu primera carga arriba.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Filas totales", f"{len(df):,}")
    c2.metric("SKUs únicos con costo", f"{df['sku'].nunique():,}")
    c3.metric("Cargas (fechas distintas)", f"{df['fecha_carga'].nunique():,}")

    st.dataframe(
        df.sort_values(["fecha_carga", "sku"], ascending=[False, True]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "sku": st.column_config.TextColumn("SKU"),
            "costo": st.column_config.NumberColumn("Costo", format="$ %.2f"),
            "fecha_vigencia_desde": st.column_config.TextColumn("Vigente desde"),
            "fecha_carga": st.column_config.TextColumn("Cargado"),
            "usuario": st.column_config.TextColumn("Usuario", width="small"),
            "nota": st.column_config.TextColumn("Nota"),
        },
    )
