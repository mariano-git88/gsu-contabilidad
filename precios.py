"""
precios.py — Tab "Precios": ¿a qué SKU se le puede subir el precio?

Cruza 12 meses de facturación con el costo vigente y clasifica cada SKU
con un semáforo. La lógica vive en `analisis_precios.py` (sin Streamlit,
para poder testearla); acá está solo la pantalla.

Secciones (con `segmented_control`, no `st.tabs`: las tabs anidadas
derraman contenido entre pestañas):
  - Candidatos: la tabla del semáforo + la plata en juego.
  - Detalle por SKU: serie mensual de precio/volumen + simulador de suba.
  - Decisiones: registro de las subas aplicadas y su medición posterior.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime

import pandas as pd
import streamlit as st

import analisis_precios as ap
import api_loader
import gsheets

MESES_DEFAULT = 12


@st.cache_data(ttl=86400, show_spinner=False)
def _pull_mes(client_id: str, client_secret: str, anio: int, mes: int) -> pd.DataFrame:
    """Facturación de UN mes. Cache de 24h: los meses cerrados no cambian.

    Se pullea mes a mes (y no todo el rango de una) para que el cache
    sirva entre corridas: al mes siguiente solo se paga el mes nuevo.
    """
    ultimo = calendar.monthrange(anio, mes)[1]
    session = api_loader.obtener_token(client_id, client_secret)
    session, df, _err = api_loader.load_fc_api(
        session,
        fecha_desde=f"{anio:04d}-{mes:02d}-01",
        fecha_hasta=f"{anio:04d}-{mes:02d}-{ultimo:02d}",
    )
    return df


def _meses_hacia_atras(n: int) -> list[tuple[int, int]]:
    """[(año, mes)] de los últimos n meses, del más viejo al más nuevo."""
    hoy = date.today()
    out = []
    a, m = hoy.year, hoy.month
    for _ in range(n):
        out.append((a, m))
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    return list(reversed(out))


def _cargar(client_id: str, client_secret: str, n_meses: int) -> pd.DataFrame:
    partes, barra = [], st.progress(0.0, text="Trayendo facturación…")
    meses = _meses_hacia_atras(n_meses)
    for i, (a, m) in enumerate(meses, start=1):
        barra.progress(i / len(meses), text=f"Trayendo {a}-{m:02d} ({i}/{len(meses)})…")
        try:
            partes.append(_pull_mes(client_id, client_secret, a, m))
        except api_loader.ApiError as e:
            st.warning(f"No se pudo traer {a}-{m:02d}: {e}")
    barra.empty()
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


def _fmt_money(v) -> str:
    """Formatea plata escapando el peso: Streamlit toma $...$ como LaTeX."""
    return f"\\$ {v:,.0f}" if pd.notna(v) else "—"


def render() -> None:
    st.markdown("## Precios y elasticidad")
    st.caption(
        "Qué códigos aguantarían una suba de precio sin perder unidades. "
        "Cruza la facturación de los últimos meses con el costo vigente del "
        "Sheet y clasifica cada SKU con un semáforo."
    )

    client_id = st.secrets.get("contabilium_client_id")
    client_secret = st.secrets.get("contabilium_client_secret")
    gs = st.secrets.get("gsheets")
    if not (client_id and client_secret and gs):
        st.error(
            "Faltan secrets. Verificá `contabilium_client_id`, "
            "`contabilium_client_secret` y la sección `[gsheets]`."
        )
        return
    gs = dict(gs)

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        n_meses = st.number_input("Meses a analizar", 6, 24, MESES_DEFAULT, 1)
    with col2:
        min_u = st.number_input("Mínimo de unidades", 0, 5000, 150, 50)
    with col3:
        st.caption(
            "El período se parte al medio y se compara precio contra volumen "
            "entre las dos mitades. Con menos de 8 meses la lectura es débil."
        )

    if st.button("Analizar precios", type="primary"):
        st.session_state["precios_run"] = True

    if not st.session_state.get("precios_run"):
        st.info(
            "Apretá **Analizar precios**. La primera corrida trae mes a mes "
            "desde Contabilium y puede tardar varios minutos; después queda "
            "cacheada 24 horas."
        )
        return

    with st.spinner("Leyendo costos del Sheet…"):
        try:
            df_costos = gsheets.read_costos(gs)
        except gsheets.GsheetsError as e:
            st.error(f"No se pudo leer el Sheet de costos: {e}")
            return
    if df_costos.empty:
        st.error("El Sheet de costos está vacío. Cargá costos antes de analizar precios.")
        return

    df_fc = _cargar(client_id, client_secret, int(n_meses))
    if df_fc.empty:
        st.warning("La API no devolvió facturación para el período.")
        return

    lineas = ap.preparar_lineas(df_fc)
    if lineas.empty:
        st.warning("No hay líneas de venta en UYU con SKU en el período.")
        return

    hoy_iso = date.today().isoformat()
    costos = gsheets.costos_vigentes_map(df_costos, hoy_iso)
    t = ap.analizar(lineas, costos, min_unidades=int(min_u))
    resumen = ap.resumen_plata(t)

    st.divider()
    f1 = st.columns(2)
    f1[0].metric("🟢 Subir — evidencia directa",
                 _fmt_money(resumen.get("verde_uyu", 0)),
                 f"{resumen.get('verde_skus', 0)} SKU")
    f1[1].metric("🟡 Revisar — bajo el piso de marca",
                 _fmt_money(resumen.get("amarillo_uyu", 0)),
                 f"{resumen.get('amarillo_skus', 0)} SKU")
    f2 = st.columns(2)
    f2[0].metric("🩸 Se venden bajo costo",
                 _fmt_money(resumen.get("bajo_costo_uyu", 0)),
                 f"{resumen.get('bajo_costo_skus', 0)} SKU — pérdida anual",
                 delta_color="inverse")
    f2[1].metric("🔴 No tocar", f"{resumen.get('no_tocar_skus', 0)} SKU",
                 "la demanda sí responde al precio")

    st.caption(
        f"Período: {lineas['fecha'].min():%d-%m-%Y} → {lineas['fecha'].max():%d-%m-%Y} · "
        f"{len(lineas):,} líneas · {t.index.nunique()} SKU con venta"
    )

    seccion = st.segmented_control(
        "Sección", ["Candidatos", "Detalle por SKU", "Decisiones"],
        default="Candidatos", label_visibility="collapsed",
    )

    if seccion == "Candidatos":
        _seccion_candidatos(t)
    elif seccion == "Detalle por SKU":
        _seccion_detalle(t, lineas, gs)
    else:
        _seccion_decisiones(t, lineas, gs)


# ---------------------------------------------------------------- secciones

def _seccion_candidatos(t: pd.DataFrame) -> None:
    filtro = st.multiselect(
        "Mostrar", sorted(t["semaforo"].unique()),
        default=[s for s in sorted(t["semaforo"].unique()) if s[0] in "🟢🟡🩸"],
    )
    d = t[t["semaforo"].isin(filtro)] if filtro else t
    cols = ["semaforo", "producto", "unidades", "clientes", "precio_1", "precio_2",
            "d_precio_pct", "d_unidades_pct", "costo", "margen_pct", "piso_marca",
            "suba_sugerida", "suba_pct", "margen_extra_anual", "motivo"]
    st.dataframe(
        d[cols].reset_index(),
        width="stretch", hide_index=True, height=460,
        column_config={
            "sku": st.column_config.TextColumn("SKU", width="small"),
            "semaforo": st.column_config.TextColumn("", width="small"),
            "producto": st.column_config.TextColumn("Producto", width="medium"),
            "unidades": st.column_config.NumberColumn("Unid. período", format="%.0f"),
            "clientes": st.column_config.NumberColumn("Clientes", format="%.0f"),
            "precio_1": st.column_config.NumberColumn("Precio 1ª mitad", format="%.2f"),
            "precio_2": st.column_config.NumberColumn("Precio 2ª mitad", format="%.2f"),
            # Los % ya vienen en escala 0-100: NO usar ProgressColumn ni
            # format porcentual, que multiplica por 100 otra vez.
            "d_precio_pct": st.column_config.NumberColumn("Δ precio %", format="%.1f"),
            "d_unidades_pct": st.column_config.NumberColumn("Δ unid. %", format="%.1f"),
            "costo": st.column_config.NumberColumn("Costo", format="%.2f"),
            "margen_pct": st.column_config.NumberColumn("Margen %", format="%.1f"),
            "piso_marca": st.column_config.NumberColumn("Piso marca", format="%.2f"),
            "suba_sugerida": st.column_config.NumberColumn("Subir \\$", format="%.2f"),
            "suba_pct": st.column_config.NumberColumn("Subir %", format="%.1f"),
            "margen_extra_anual": st.column_config.NumberColumn("Margen extra/año", format="%.0f"),
            "motivo": st.column_config.TextColumn("Por qué", width="large"),
        },
    )
    st.download_button(
        "Descargar análisis completo (CSV)",
        t.reset_index().to_csv(index=False).encode("utf-8-sig"),
        file_name=f"precios_{date.today().isoformat()}.csv",
        mime="text/csv",
    )
    with st.expander("Cómo leer el semáforo"):
        st.markdown(
            "- **🟢 subir** — hay evidencia directa en los datos: o bajaron el "
            "precio y el volumen no subió, o lo subieron y el volumen no cayó.\n"
            "- **🟡 revisar** — está bajo el piso de marca (costo × 1,85) y el "
            "volumen nunca respondió a las bajas. Es una regla de política, no "
            "evidencia de mercado.\n"
            "- **🩸 bajo costo / revisar costo** — se vende por debajo del costo, "
            "o llegar al piso exigiría subir más de 20%. Antes de tocar el precio "
            "hay que verificar que el costo esté bien cargado.\n"
            "- **🔴 no tocar** — bajaron el precio y el volumen respondió fuerte. "
            "La demanda sí es sensible acá.\n"
            "- **⚫ sin señal / estacional / posible quiebre / sin costo** — no se "
            "puede concluir. Un SKU estacional no se puede partir en dos mitades, "
            "y uno que se quedó sin stock vendió menos por falta de mercadería, "
            "no de demanda."
        )


def _seccion_detalle(t: pd.DataFrame, lineas: pd.DataFrame, gs: dict) -> None:
    orden = t.sort_values("margen_extra_anual", ascending=False).index.tolist()
    sku = st.selectbox("SKU", orden,
                       format_func=lambda s: f"{s} — {t.loc[s, 'producto']}")
    r = t.loc[sku]
    st.markdown(f"### {r['semaforo']} · {r['producto']}")
    st.info(r["motivo"])

    c = st.columns(3)
    c[0].metric("Precio promedio", f"{r['precio_prom']:,.2f}")
    c[1].metric("Costo vigente", f"{r['costo']:,.2f}" if pd.notna(r["costo"]) else "—")
    c[2].metric("Margen", f"{r['margen_pct']:.1f}%" if pd.notna(r["margen_pct"]) else "—")

    serie = ap.serie_mensual(lineas[lineas["sku"] == sku])
    serie["mes"] = serie["mes"].astype(str)
    g1, g2 = st.columns(2)
    with g1:
        st.caption("Precio promedio por mes")
        st.line_chart(serie.set_index("mes")["precio"], height=220)
    with g2:
        st.caption("Unidades por mes")
        st.bar_chart(serie.set_index("mes")["unidades"], height=220)

    st.markdown("#### Simulador")
    suba = st.slider("Suba de precio (%)", 0.0, 30.0,
                     float(r["suba_pct"]) if pd.notna(r["suba_pct"]) and r["suba_pct"] > 0 else 5.0,
                     0.5)
    if pd.notna(r["costo"]) and r["costo"] > 0:
        sim = ap.simular(float(r["precio_prom"]), float(r["costo"]), suba)
        s = st.columns(3)
        s[0].metric("Precio nuevo", f"{sim['precio_nuevo']:,.2f}")
        s[1].metric("Margen nuevo", f"{sim['margen_nuevo']:,.2f}",
                    f"{sim['margen_nuevo'] - sim['margen_actual']:+,.2f} por unidad")
        s[2].metric("Volumen que podés perder", f"{sim['volumen_tolerable_pct']:.1f}%",
                    "y seguir ganando lo mismo")
        st.caption(
            f"A {r['unidades']:,.0f} unidades del período, la suba deja "
            f"**{(sim['margen_nuevo'] - sim['margen_actual']) * r['unidades']:,.0f}** "
            f"de margen extra si el volumen no se mueve. El número que importa es el "
            f"tercero: recién perdiendo más del {sim['volumen_tolerable_pct']:.1f}% de "
            f"las unidades la suba deja de convenir."
        )
        _form_decision(sku, r, sim, suba, gs)
    else:
        st.warning("Sin costo cargado: no se puede simular margen.")


def _form_decision(sku, r, sim, suba, gs) -> None:
    with st.form(f"dec_{sku}"):
        nota = st.text_input("Nota (opcional)", placeholder="ej: se aplica desde la lista de septiembre")
        if st.form_submit_button("Registrar esta decisión"):
            try:
                n = gsheets.append_decisiones(gs, [{
                    "sku": sku,
                    "fecha_decision": date.today().isoformat(),
                    "precio_antes": float(r["precio_prom"]),
                    "precio_objetivo": float(sim["precio_nuevo"]),
                    "suba_pct": float(suba),
                    "unidades_12m_antes": float(r["unidades"]),
                    "motivo": str(r["motivo"])[:250],
                    "nota": nota,
                }])
                st.success(f"Registrada ({n} fila). Se puede medir a los 60-90 días.")
            except gsheets.GsheetsError as e:
                st.error(f"No se pudo escribir en el Sheet: {e}")


def _seccion_decisiones(t: pd.DataFrame, lineas: pd.DataFrame, gs: dict) -> None:
    st.caption(
        "Cada suba registrada se convierte en un experimento con fecha. Acá se "
        "compara el volumen posterior contra el de los 90 días previos."
    )
    try:
        dec = gsheets.read_decisiones(gs)
    except gsheets.GsheetsError as e:
        st.error(f"No se pudo leer el log: {e}")
        return
    if dec.empty:
        st.info("Todavía no hay decisiones registradas. Se cargan desde **Detalle por SKU**.")
        return

    filas = []
    for _, d in dec.iterrows():
        ln = lineas[lineas["sku"] == d["sku"]]
        antes = ln[(ln["fecha"] < d["fecha_decision"]) &
                   (ln["fecha"] >= d["fecha_decision"] - pd.Timedelta(days=90))]
        desp = ln[ln["fecha"] >= d["fecha_decision"]]
        dias = (lineas["fecha"].max() - d["fecha_decision"]).days
        u_antes = antes["unidades"].sum()
        u_desp = desp["unidades"].sum()
        # Normalizado a 90 días para que la comparación sea justa aunque la
        # decisión sea reciente.
        u_desp_norm = u_desp * 90 / dias if dias > 0 else float("nan")
        filas.append({
            "sku": d["sku"], "fecha": d["fecha_decision"].date(),
            "días transcurridos": dias,
            "precio antes": d["precio_antes"], "precio objetivo": d["precio_objetivo"],
            "precio real después": (desp["monto"].sum() / u_desp) if u_desp else float("nan"),
            "unid. 90d antes": u_antes,
            "unid. 90d después (proy.)": u_desp_norm,
            "Δ volumen %": (100 * (u_desp_norm / u_antes - 1)) if u_antes else float("nan"),
        })
    res = pd.DataFrame(filas)
    st.dataframe(res, width="stretch", hide_index=True)
    st.caption(
        "**Ojo con leerlo antes de tiempo:** con menos de 60 días la proyección "
        "es ruido. Y si el SKU es estacional, comparar contra los 90 días previos "
        "mezcla precio con temporada."
    )
