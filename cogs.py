"""
cogs.py — Tab "COGS mensual": costo de mercadería vendida por período.

Cruce mensual:
  - Ventas del rango (load_fc_api) — FAC + NDF suman, NCF resta.
  - Costo de cada SKU vigente A LA FECHA de la factura (lookup en el
    Google Sheet histórico).
  - COGS por línea = unidades_con_signo × costo_vigente.
  - Total COGS = suma de COGS por línea (las NCF restan, igual que ventas).
  - Margen bruto = venta_neta - COGS.

SKUs vendidos en el mes sin costo cargado en el Sheet quedan listados
en un panel de salud — se EXCLUYEN del cálculo (no hay fallback al
CostoInterno de Contabilium porque ese dato no es la verdad de Suprabond).
"""

from __future__ import annotations

import calendar
from datetime import date

import pandas as pd
import streamlit as st

import api_loader
import gsheets


@st.cache_data(ttl=1800, show_spinner=False)
def _pull_facturacion(
    client_id: str,
    client_secret: str,
    fecha_desde: str,
    fecha_hasta: str,
) -> pd.DataFrame:
    """Wrapper cacheado de api_loader.load_fc_api. Cache 30min."""
    session = api_loader.obtener_token(client_id, client_secret)
    session, df, _errors = api_loader.load_fc_api(
        session, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta
    )
    return df


def _rango_mes(anio: int, mes: int) -> tuple[str, str]:
    """Devuelve ("YYYY-MM-01", "YYYY-MM-último") para el mes dado."""
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return f"{anio:04d}-{mes:02d}-01", f"{anio:04d}-{mes:02d}-{ultimo_dia:02d}"


def render() -> None:
    st.markdown("## COGS mensual")
    st.caption(
        "Costo de mercadería vendida por mes. Cruza ventas (FAC + NDF − NCF) "
        "con el costo del SKU vigente a la fecha de cada factura. La fuente "
        "de costos es el Google Sheet de la sección **Carga de costos**."
    )

    client_id = st.secrets.get("contabilium_client_id")
    client_secret = st.secrets.get("contabilium_client_secret")
    gsheets_section = st.secrets.get("gsheets")
    if not (client_id and client_secret and gsheets_section):
        st.error(
            "Faltan secrets. Verificá `contabilium_client_id`, "
            "`contabilium_client_secret` y la sección `[gsheets]`."
        )
        return
    gsheets_section = dict(gsheets_section)

    # ---- Selección de período ----
    hoy = date.today()
    mes_default = hoy.month - 1 if hoy.month > 1 else 12
    anio_default = hoy.year if hoy.month > 1 else hoy.year - 1

    col1, col2, _ = st.columns([1, 1, 3])
    with col1:
        anio = st.selectbox(
            "Año",
            options=list(range(hoy.year - 3, hoy.year + 1)),
            index=list(range(hoy.year - 3, hoy.year + 1)).index(anio_default),
        )
    with col2:
        mes = st.selectbox(
            "Mes",
            options=list(range(1, 13)),
            index=mes_default - 1,
            format_func=lambda m: f"{m:02d} - {calendar.month_name[m]}",
        )

    fecha_desde, fecha_hasta = _rango_mes(anio, mes)
    st.caption(f"Rango: {fecha_desde} → {fecha_hasta}")

    if not st.button("Calcular COGS del período", type="primary"):
        return

    # ---- Pull ventas + costos ----
    with st.spinner("Trayendo facturación del mes desde Contabilium…"):
        try:
            df_fc = _pull_facturacion(
                client_id, client_secret, fecha_desde, fecha_hasta
            )
        except api_loader.AuthError as e:
            st.error(f"Auth con Contabilium falló: {e}")
            return
        except api_loader.ApiError as e:
            st.error(f"Error de API: {e}")
            return

    if df_fc.empty:
        st.warning("La API no devolvió comprobantes para ese período.")
        return

    # df_uyu = TODA la facturación UYU del mes. Es la base de la VENTA
    # NETA: incluye las NCF de descuento comercial sin SKU, que son
    # negativas y deben restar (es como lo computa Contabilium).
    df_uyu = df_fc[df_fc["moneda"] == "UYU"].copy()
    # df_fc = solo las líneas con SKU. Es la base del cruce de COGS:
    # a una línea sin SKU no se le puede buscar un costo.
    df_fc = df_uyu[df_uyu["sku"].astype(str).str.len() > 0].reset_index(
        drop=True
    )

    with st.spinner("Leyendo costos del Google Sheet…"):
        try:
            df_costos = gsheets.read_costos(gsheets_section)
        except gsheets.GsheetsError as e:
            st.error(f"No se pudo leer el Sheet: {e}")
            return

    if df_costos.empty:
        st.error(
            "El Google Sheet de costos está vacío. Subí al menos una "
            "carga desde la pestaña **Carga de costos** antes de calcular COGS."
        )
        return

    # ---- Lookup de costo vigente por línea ----
    # Normalizar SKU y fecha a strings ISO para el lookup
    df_fc["sku"] = df_fc["sku"].astype(str).str.strip().str.upper()
    df_fc["fecha_iso"] = pd.to_datetime(df_fc["fecha"]).dt.strftime("%Y-%m-%d")

    costos_lookup_cache: dict[tuple[str, str], float | None] = {}

    def _lookup(sku: str, fecha_iso: str) -> float | None:
        key = (sku, fecha_iso)
        if key not in costos_lookup_cache:
            costos_lookup_cache[key] = gsheets.costo_vigente_por_sku(
                df_costos, sku, fecha_iso
            )
        return costos_lookup_cache[key]

    df_fc["costo_unitario"] = [
        _lookup(s, f) for s, f in zip(df_fc["sku"], df_fc["fecha_iso"])
    ]

    # Línea con costo NULL = SKU sin costo vigente → se EXCLUYE de COGS
    df_con_costo = df_fc[df_fc["costo_unitario"].notna()].copy()
    df_sin_costo = df_fc[df_fc["costo_unitario"].isna()].copy()

    df_con_costo["cogs_linea"] = (
        df_con_costo["unidades"] * df_con_costo["costo_unitario"]
    )

    # ---- KPIs ----
    # Venta neta = TODA la facturación UYU del mes (con y sin SKU). Las
    # NCF de descuento comercial sin SKU restan acá. El cruce de COGS de
    # arriba SÍ usa solo las líneas con SKU (df_fc), que es correcto.
    venta_neta = df_uyu["monto"].sum()
    cogs_total = df_con_costo["cogs_linea"].sum()
    margen_bruto = venta_neta - cogs_total
    margen_pct = (margen_bruto / venta_neta * 100) if venta_neta else 0.0

    # Métricas en 2x2: cuatro en una fila cortan los montos grandes.
    fila1 = st.columns(2)
    fila1[0].metric("Venta neta (UYU)", f"$ {venta_neta:,.0f}")
    fila1[1].metric("COGS (UYU)", f"$ {cogs_total:,.0f}")
    fila2 = st.columns(2)
    fila2[0].metric("Margen bruto (UYU)", f"$ {margen_bruto:,.0f}")
    fila2[1].metric("Margen %", f"{margen_pct:.1f}%")

    # ---- Panel de salud ----
    if not df_sin_costo.empty:
        skus_sin = (
            df_sin_costo.groupby("sku")
            .agg(
                producto=("producto", "first"),
                unidades=("unidades", "sum"),
                venta_neta=("monto", "sum"),
            )
            .reset_index()
            .sort_values("venta_neta", ascending=False)
        )
        with st.expander(
            f"⚠ Salud: {len(skus_sin)} SKU(s) vendidos en el mes sin costo cargado — "
            f"${skus_sin['venta_neta'].sum():,.0f} de venta excluida del COGS",
            expanded=False,
        ):
            st.caption(
                "Estos SKUs aparecen en la facturación del mes pero no tienen "
                "costo cargado en el Sheet con vigencia anterior o igual a la "
                "fecha de la factura. Subí los costos correspondientes y "
                "recalculá."
            )
            st.dataframe(
                skus_sin,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "sku": st.column_config.TextColumn("SKU"),
                    "producto": st.column_config.TextColumn("Producto"),
                    "unidades": st.column_config.NumberColumn(
                        "Unidades", format="%.0f"
                    ),
                    "venta_neta": st.column_config.NumberColumn(
                        "Venta excluida", format="$ %,.0f"
                    ),
                },
            )

    # ---- COGS por producto ----
    st.markdown("### COGS por producto")
    por_sku = (
        df_con_costo.groupby(["sku", "producto"])
        .agg(
            unidades=("unidades", "sum"),
            venta=("monto", "sum"),
            cogs=("cogs_linea", "sum"),
        )
        .reset_index()
    )
    por_sku["margen"] = por_sku["venta"] - por_sku["cogs"]
    por_sku["margen_pct"] = (
        (por_sku["margen"] / por_sku["venta"].replace(0, pd.NA)) * 100
    ).fillna(0)
    por_sku = por_sku.sort_values("cogs", ascending=False)

    st.dataframe(
        por_sku,
        use_container_width=True,
        hide_index=True,
        column_config={
            "sku": st.column_config.TextColumn("SKU"),
            "producto": st.column_config.TextColumn("Producto"),
            "unidades": st.column_config.NumberColumn("Unidades", format="%.0f"),
            "venta": st.column_config.NumberColumn("Venta", format="$ %,.0f"),
            "cogs": st.column_config.NumberColumn("COGS", format="$ %,.0f"),
            "margen": st.column_config.NumberColumn("Margen", format="$ %,.0f"),
            "margen_pct": st.column_config.NumberColumn("Margen %", format="%.1f%%"),
        },
    )

    # ---- Export ----
    st.download_button(
        "Descargar detalle (CSV)",
        data=por_sku.to_csv(index=False).encode("utf-8"),
        file_name=f"cogs_{anio}-{mes:02d}.csv",
        mime="text/csv",
    )
