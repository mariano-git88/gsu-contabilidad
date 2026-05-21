"""
productos.py — Tab "Productos": catálogo en vivo desde la API de Contabilium.

Lectura de `/api/conceptos/search` (Tipo == "Producto") con cache de 1h.
Muestra SKU, nombre, costo interno cargado en Contabilium (informativo,
NO es la fuente de verdad — esa vive en el Google Sheet de costos),
precio neto, precio con IVA, stock, rubro, sub_rubro.

Sirve además como generador de plantillas para la sección de carga de
costos: export CSV con todas las SKUs activas.
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd
import streamlit as st

import api_loader


@st.cache_data(ttl=3600, show_spinner=False)
def _sync_catalogo(client_id: str, client_secret: str) -> pd.DataFrame:
    """Pullea el catálogo completo desde Contabilium, una sola pasada
    sobre `/api/conceptos/search`. Cache 1h para no martillar la API.

    El cache key incluye las credenciales (Streamlit las hashea); si
    cambian, se invalida solo.
    """
    session = api_loader.obtener_token(client_id, client_secret)
    session, conceptos = api_loader._fetch_all_conceptos(session)

    rows = []
    for c in conceptos:
        if c.get("Tipo") != "Producto":
            continue
        precio_neto = api_loader._precio_neto(c.get("PrecioFinal"))
        precio_final = api_loader._safe_float(c.get("PrecioFinal"))
        rows.append(
            {
                "sku": str(c.get("Codigo") or "").strip().upper(),
                "nombre": str(c.get("Nombre") or "").strip(),
                "costo_interno_cbm": api_loader._safe_float(c.get("CostoInterno")),
                "precio_neto": precio_neto,
                "precio_final": precio_final,
                "rentabilidad_cbm": api_loader._safe_float(c.get("Rentabilidad")),
                "stock": api_loader._safe_float(c.get("Stock")),
                "stock_minimo": api_loader._safe_float(c.get("StockMinimo")),
                "estado": str(c.get("Estado") or "").strip(),
                "id_rubro": str(c.get("IdRubro") or "").strip(),
                "id_subrubro": str(c.get("IdSubrubro") or "").strip(),
            }
        )
    df = pd.DataFrame(rows)
    df = df.sort_values("sku").reset_index(drop=True)
    return df


def render() -> None:
    st.markdown("## Productos")
    st.caption(
        "Catálogo activo en Contabilium. El campo `costo_interno_cbm` es "
        "el cargado en el ERP (informativo). La fuente de verdad de costos "
        "para cálculos de COGS es el Google Sheet de la sección "
        "**Carga de costos**."
    )

    client_id = st.secrets.get("contabilium_client_id")
    client_secret = st.secrets.get("contabilium_client_secret")
    if not client_id or not client_secret:
        st.error(
            "Faltan `contabilium_client_id` o `contabilium_client_secret` "
            "en secrets. Ver `.streamlit/secrets.toml.example`."
        )
        return

    col1, col2, _ = st.columns([1, 1, 4])
    with col1:
        if st.button("Sincronizar ahora"):
            _sync_catalogo.clear()
            st.rerun()
    with col2:
        solo_activos = st.checkbox("Solo activos", value=True)

    try:
        with st.spinner("Leyendo catálogo de Contabilium…"):
            df = _sync_catalogo(client_id, client_secret)
    except api_loader.AuthError as e:
        st.error(f"Auth con Contabilium falló: {e}")
        return
    except api_loader.ApiError as e:
        st.error(f"Error de API: {e}")
        return

    if df.empty:
        st.warning("La API devolvió 0 productos.")
        return

    if solo_activos:
        df = df[df["estado"].str.lower() == "activo"].reset_index(drop=True)

    busqueda = st.text_input(
        "Buscar (SKU o nombre, separá con coma para varios)",
        placeholder="ej. ROD-001, BASE",
    )
    if busqueda:
        terms = [t.strip().upper() for t in busqueda.split(",") if t.strip()]
        mask = False
        for t in terms:
            mask = mask | df["sku"].str.contains(t, na=False, regex=False) | \
                df["nombre"].str.upper().str.contains(t, na=False, regex=False)
        df = df[mask].reset_index(drop=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Productos", f"{len(df):,}")
    c2.metric("Stock total (uds)", f"{df['stock'].sum():,.0f}")
    c3.metric(
        "Valor de stock (precio venta neto)",
        f"$ {(df['stock'] * df['precio_neto']).sum():,.0f}",
    )
    c4.metric(
        "Valor de stock (costo Contabilium)",
        f"$ {(df['stock'] * df['costo_interno_cbm']).sum():,.0f}",
    )

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "sku": st.column_config.TextColumn("SKU", width="small"),
            "nombre": st.column_config.TextColumn("Nombre", width="large"),
            "costo_interno_cbm": st.column_config.NumberColumn(
                "Costo Contabilium", format="$ %.2f"
            ),
            "precio_neto": st.column_config.NumberColumn(
                "Precio neto", format="$ %.2f"
            ),
            "precio_final": st.column_config.NumberColumn(
                "Precio con IVA", format="$ %.2f"
            ),
            "rentabilidad_cbm": st.column_config.NumberColumn(
                "Rent. %", format="%.1f%%"
            ),
            "stock": st.column_config.NumberColumn("Stock", format="%.0f"),
            "stock_minimo": st.column_config.NumberColumn("Stock mín.", format="%.0f"),
            "estado": st.column_config.TextColumn("Estado", width="small"),
            "id_rubro": None,  # ocultar (es solo el ID, no nombre)
            "id_subrubro": None,
        },
    )

    st.markdown("### Exportar plantilla de costos")
    st.caption(
        "Descargá un CSV/XLSX con SKU + nombre + costo actual de "
        "Contabilium pre-cargado en la columna `costo`, listo para "
        "editar y volver a subir desde la pestaña **Carga de costos**."
    )

    plantilla = df[["sku", "nombre", "costo_interno_cbm"]].rename(
        columns={"costo_interno_cbm": "costo"}
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            "Descargar CSV",
            data=plantilla.to_csv(index=False).encode("utf-8"),
            file_name="plantilla_costos.csv",
            mime="text/csv",
        )
    with col_b:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            plantilla.to_excel(w, index=False, sheet_name="costos")
        st.download_button(
            "Descargar XLSX",
            data=buf.getvalue(),
            file_name="plantilla_costos.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
