"""
app.py — Webapp Contabilidad Suprabond.

Entry point para `contabilidad.streamlit.app`. Tres secciones:
  1. Productos: catálogo desde API Contabilium (SKU, precio, stock, etc).
  2. Carga de costos: upload CSV/XLSX → Google Sheet (histórico fechado).
  3. COGS mensual: cruce ventas del mes × costo vigente por SKU.

Auth single-user (password compartida). Misma estética Dieter Rams /
Vitsoe del dashboard GSU.
"""

from __future__ import annotations

import streamlit as st

import auth
import cogs
import costos
import productos
import tutorial
from theme import apply_theme


def main() -> None:
    st.set_page_config(
        page_title="Contabilidad Suprabond",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()

    if not auth.check_password():
        st.stop()

    auth.logout_button()

    with st.sidebar:
        st.markdown("## Contabilidad")
        st.caption("Costos internos por SKU + COGS mensual")

    st.markdown("# Contabilidad")
    st.caption(
        "Visibilidad de costos internos y cálculo de costo de mercadería "
        "vendida (COGS) por período."
    )

    tab_prod, tab_costos, tab_cogs, tab_tutorial = st.tabs(
        ["Productos", "Carga de costos", "COGS mensual", "Tutorial"]
    )

    with tab_prod:
        productos.render()

    with tab_costos:
        costos.render()

    with tab_cogs:
        cogs.render()

    with tab_tutorial:
        tutorial.render()


if __name__ == "__main__":
    main()
