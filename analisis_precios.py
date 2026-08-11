"""
analisis_precios.py — lógica pura del análisis de precios y elasticidad.

Sin Streamlit adentro a propósito: todo lo de acá es testeable con un
DataFrame de facturación y un diccionario de costos.

QUÉ RESUELVE
------------
La pregunta es "¿a qué SKU le puedo subir el precio sin perder unidades?".
El camino obvio —correlacionar precio contra volumen— NO sirve en venta
mayorista: el descuento lo genera el volumen, no al revés. Al que compra
mucho se le hace precio, así que la correlación siempre da negativa y no
mide elasticidad, mide la política de bonificación.

Por eso el análisis usa tres lentes que no dependen de esa correlación:

  A. EXPERIMENTO HACIA ABAJO — el precio promedio bajó entre la primera y
     la segunda mitad del período y el volumen NO subió. El descuento no
     compró nada, así que se puede recuperar.
  B. EXPERIMENTO HACIA ARRIBA — el precio subió y el volumen NO cayó. Es
     la evidencia más fuerte de demanda insensible al precio.
  C. PISO DE MARCA — el precio quedó por debajo de `costo × PISO_MARCA` y
     el volumen nunca respondió a las bajas. Es una regla de política, no
     evidencia de mercado: va en amarillo, no en verde.

CONTROLES (sin ellos el análisis miente)
---------------------------------------
  - ESTACIONALIDAD: se calcula, no se hardcodea. Un SKU que concentra su
    año en pocos meses no se puede partir en dos mitades — la mitad con
    temporada siempre gana y parece que el precio no importó.
  - QUIEBRE DE STOCK: un SKU que se quedó sin mercadería vendió menos por
    falta de stock, no por precio. Se marca por semanas sin venta.
  - LANZAMIENTOS: sin historia comparable, quedan fuera.

LÍMITE QUE NO SE PUEDE SALVAR CON ESTOS DATOS
---------------------------------------------
Se observa el PRECIO PROMEDIO REALIZADO (monto/unidades), no el precio de
lista. Una "suba" puede ser simplemente que ese mes compraron menos
clientes bonificados. Para separar las dos cosas haría falta el historial
de listas de precios, que hoy no existe en ningún sistema.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PISO_MARCA = 1.85          # piso de marca de Suprabond: costo × 1,85
UMBRAL_PRECIO = 3.0        # % mínimo de cambio de precio para considerarlo movimiento
UMBRAL_VOLUMEN = 5.0       # % de tolerancia en volumen para decir "no se movió"
UMBRAL_ELASTICO = 25.0     # % de suba de volumen que marca demanda elástica
SEM_CERO_QUIEBRE = 6       # semanas sin venta en la 2ª mitad → sospecha de quiebre
SALTO_SEM_CERO = 4         # ...pero solo si AUMENTARON tanto respecto de la 1ª mitad
GAP_PISO_MAX = 20.0        # una suba mayor a esto no es acción de precio: es revisar el costo
CONCENTRACION_ESTACIONAL = 0.75   # ventana de 5 meses consecutivos: calibrado sobre datos reales


def preparar_lineas(df_fc: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la facturación cruda para el análisis de precios.

    Se queda con moneda UYU, líneas con SKU y unidades positivas: las NCF
    invierten el signo y ensuciarían el precio unitario. Agrega `precio`
    (unitario neto), `mes` y `semana`.
    """
    d = df_fc.copy()
    d["fecha"] = pd.to_datetime(d["fecha"])
    d["sku"] = d["sku"].astype(str).str.strip().str.upper()
    d = d[(d["moneda"] == "UYU") & (d["sku"].str.len() > 0) & (d["unidades"] > 0)]
    d = d[d["monto"] > 0].copy()
    d["precio"] = d["monto"] / d["unidades"]
    d["mes"] = d["fecha"].dt.to_period("M")
    d["semana"] = d["fecha"].dt.to_period("W-SUN")
    return d


def serie_mensual(lineas: pd.DataFrame) -> pd.DataFrame:
    """Serie mensual por SKU: unidades, venta y precio promedio ponderado."""
    g = lineas.groupby(["sku", "mes"]).agg(
        unidades=("unidades", "sum"),
        venta=("monto", "sum"),
        clientes=("razon_social", "nunique"),
    )
    g["precio"] = (g["venta"] / g["unidades"]).round(2)
    return g.reset_index()


def _indice_estacional(unidades_por_mes: pd.Series) -> float:
    """Qué fracción del año explica la mejor ventana de 5 meses CONSECUTIVOS.

    La ventana tiene que ser consecutiva: los 5 meses más altos sueltos los
    tiene cualquier producto irregular, y eso no es una temporada. Medido
    sobre los datos reales de Suprabond, la separación es nítida —
    burletes y zócalos dan 0,83-0,90; siliconas, espumas y candados dan
    0,43-0,52. Por eso el umbral está en 0,75.
    """
    v = unidades_por_mes.values.astype(float)
    total = v.sum()
    if total <= 0 or len(v) < 5:
        return 0.0
    ventanas = [v[i:i + 5].sum() for i in range(len(v) - 4)]
    return float(max(ventanas) / total)


def analizar(
    lineas: pd.DataFrame,
    costos: dict[str, float],
    *,
    min_unidades: int = 150,
    min_meses: int = 8,
    min_clientes: int = 8,
) -> pd.DataFrame:
    """Corre las tres lentes y devuelve una fila por SKU con el semáforo.

    `costos` es {sku: costo vigente}. Los SKU sin costo quedan igual en el
    resultado (con `costo` NaN) pero no se clasifican: sin costo no hay
    margen y sin margen no hay decisión de precio.
    """
    if lineas.empty:
        return pd.DataFrame()

    fin = lineas["fecha"].max()
    ini = lineas["fecha"].min()
    corte = ini + (fin - ini) / 2

    g = lineas.groupby("sku")
    t = pd.DataFrame({
        "producto": g["producto"].first(),
        "unidades": g["unidades"].sum(),
        "venta": g["monto"].sum(),
        "clientes": g["razon_social"].nunique(),
        "meses_con_venta": g["mes"].nunique(),
    })
    t["precio_prom"] = (t["venta"] / t["unidades"]).round(2)
    t["costo"] = pd.Series(costos).reindex(t.index)
    t["margen_u"] = (t["precio_prom"] - t["costo"]).round(2)
    t["margen_pct"] = (100 * t["margen_u"] / t["precio_prom"]).round(1)
    t["piso_marca"] = (t["costo"] * PISO_MARCA).round(2)

    # --- estacionalidad, calculada por SKU ---
    por_mes = lineas.pivot_table(index="sku", columns="mes", values="unidades",
                                 aggfunc="sum").fillna(0.0)
    t["indice_estacional"] = por_mes.apply(_indice_estacional, axis=1).round(2)
    t["estacional"] = t["indice_estacional"] > CONCENTRACION_ESTACIONAL

    # --- mitades ---
    a = lineas[lineas["fecha"] < corte].groupby("sku").agg(
        u1=("unidades", "sum"), m1=("monto", "sum"))
    b = lineas[lineas["fecha"] >= corte].groupby("sku").agg(
        u2=("unidades", "sum"), m2=("monto", "sum"))
    t = t.join(a).join(b)
    t["precio_1"] = (t["m1"] / t["u1"]).round(2)
    t["precio_2"] = (t["m2"] / t["u2"]).round(2)
    t["d_precio_pct"] = (100 * (t["precio_2"] / t["precio_1"] - 1)).round(1)
    t["d_unidades_pct"] = (100 * (t["u2"] / t["u1"] - 1)).round(1)
    # Elasticidad implícita. Se reporta pero NO se usa para clasificar: con
    # movimientos de precio chicos el cociente se dispara y no significa nada.
    t["elasticidad"] = np.where(
        t["d_precio_pct"].abs() >= UMBRAL_PRECIO,
        (t["d_unidades_pct"] / t["d_precio_pct"]).round(2),
        np.nan,
    )

    # --- sospecha de quiebre en la 2ª mitad ---
    # Un SKU lento tiene semanas en cero SIEMPRE: contarlas en términos
    # absolutos marca medio catálogo. Lo que delata un quiebre es que las
    # semanas en cero AUMENTEN respecto de la primera mitad, cuando el SKU
    # sí se vendía seguido.
    pri, seg = lineas[lineas["fecha"] < corte], lineas[lineas["fecha"] >= corte]
    sem1, sem2 = pri["semana"].nunique(), seg["semana"].nunique()
    act1 = pri.groupby("sku")["semana"].nunique().reindex(t.index).fillna(0)
    act2 = seg.groupby("sku")["semana"].nunique().reindex(t.index).fillna(0)
    t["semanas_sin_venta_1"] = (sem1 - act1).astype(int)
    t["semanas_sin_venta"] = (sem2 - act2).astype(int)
    t["sospecha_quiebre"] = (
        (t["semanas_sin_venta"] >= SEM_CERO_QUIEBRE)
        & ((t["semanas_sin_venta"] - t["semanas_sin_venta_1"]) >= SALTO_SEM_CERO)
    )

    # --- universo analizable ---
    t["analizable"] = (
        t["costo"].notna() & (t["costo"] > 0)
        & (t["unidades"] >= min_unidades)
        & (t["meses_con_venta"] >= min_meses)
        & (t["clientes"] >= min_clientes)
        & t["u1"].notna() & t["u2"].notna()
    )

    t["semaforo"], t["motivo"] = zip(*t.apply(_clasificar, axis=1))
    t["suba_sugerida"] = t.apply(_suba_sugerida, axis=1).round(2)
    t["suba_pct"] = (100 * t["suba_sugerida"] / t["precio_2"]).round(1)
    t["margen_extra_anual"] = (t["suba_sugerida"] * t["unidades"]).round(0)
    return t.sort_values("margen_extra_anual", ascending=False)


def _clasificar(r) -> tuple[str, str]:
    """Semáforo por SKU. El orden importa: lo urgente pisa a lo optimizable."""
    if pd.isna(r["costo"]) or r["costo"] <= 0:
        return "⚫ sin costo", "no hay costo cargado en el Sheet"
    if r["margen_pct"] < 0:
        return "🩸 bajo costo", f"se vende {abs(r['margen_pct']):.0f}% por debajo del costo"
    if not r["analizable"]:
        return "⚫ sin datos", "poca historia, poco volumen o pocos clientes"
    if r["estacional"]:
        return "⚫ estacional", (f"concentra {100*r['indice_estacional']:.0f}% del año en 5 meses; "
                                "partir el período en mitades confunde precio con temporada")
    if r["sospecha_quiebre"]:
        return "⚫ posible quiebre", (f"{r['semanas_sin_venta']} semanas sin venta: la caída "
                                     "puede ser falta de stock, no de demanda")

    bajo = r["d_precio_pct"] <= -UMBRAL_PRECIO
    subio = r["d_precio_pct"] >= UMBRAL_PRECIO
    vol_no_subio = r["d_unidades_pct"] <= UMBRAL_VOLUMEN
    vol_no_cayo = r["d_unidades_pct"] >= -UMBRAL_VOLUMEN
    vol_salto = r["d_unidades_pct"] >= UMBRAL_ELASTICO

    if bajo and vol_salto:
        return "🔴 no tocar", (f"bajaron el precio {abs(r['d_precio_pct']):.1f}% y el volumen "
                               f"subió {r['d_unidades_pct']:.0f}%: la demanda responde al precio")
    if bajo and vol_no_subio:
        return "🟢 subir", (f"bajaron el precio {abs(r['d_precio_pct']):.1f}% y el volumen "
                            f"hizo {r['d_unidades_pct']:+.1f}%: el descuento no compró nada")
    if subio and vol_no_cayo:
        return "🟢 subir", (f"subieron el precio {r['d_precio_pct']:.1f}% y el volumen hizo "
                            f"{r['d_unidades_pct']:+.1f}%: demanda insensible, probada")
    if r["precio_2"] < r["piso_marca"] and not vol_salto:
        gap = 100 * (r["piso_marca"] / r["precio_2"] - 1)
        if gap > GAP_PISO_MAX:
            # Subir 40% o 70% no es una decisión de precio: o el costo está
            # mal cargado o el producto está estructuralmente mal posicionado.
            return "🩸 revisar costo", (f"llegar al piso exigiría subir {gap:.0f}%: verificá el "
                                        f"costo ({r['costo']:.0f}) antes de tocar el precio")
        return "🟡 revisar", (f"está {gap:.0f}% por debajo del piso de marca "
                              f"(costo × {PISO_MARCA}) y el volumen nunca respondió")
    return "⚪ sin señal", "el precio no se movió lo suficiente como para leer algo"


def _suba_sugerida(r) -> float:
    """Cuánto subir, en pesos por unidad, según el motivo del semáforo."""
    if not isinstance(r["semaforo"], str):
        return 0.0
    if r["semaforo"].startswith("🟢"):
        objetivos = [r["piso_marca"]] if pd.notna(r["piso_marca"]) else []
        if r["d_precio_pct"] <= -UMBRAL_PRECIO:
            # Bajaron y no ganaron volumen → recuperar el precio anterior.
            objetivos.append(r["precio_1"])
        else:
            # Subieron y no perdieron volumen → repetir la misma suba. La
            # evidencia dice que el mercado la absorbió una vez.
            objetivos.append(r["precio_2"] * (1 + r["d_precio_pct"] / 100))
        return max(0.0, max(objetivos) - r["precio_2"])
    if r["semaforo"].startswith("🟡"):
        return max(0.0, r["piso_marca"] - r["precio_2"])
    return 0.0


def simular(precio: float, costo: float, suba_pct: float) -> dict:
    """Simulador de una suba: margen nuevo y cuánto volumen podés perder.

    El número que importa es `volumen_tolerable_pct`: por debajo de esa
    caída de unidades, la suba sigue conviniendo. Es la pregunta que
    realmente se hace el comercial, y no depende de estimar elasticidad.
    """
    if precio <= 0 or costo < 0:
        return {}
    nuevo = precio * (1 + suba_pct / 100)
    m_actual = precio - costo
    m_nuevo = nuevo - costo
    if m_nuevo <= 0:
        return {"precio_nuevo": nuevo, "margen_actual": m_actual,
                "margen_nuevo": m_nuevo, "volumen_tolerable_pct": float("nan")}
    # margen total igual: u_nuevas × m_nuevo = u_actuales × m_actual
    ratio = m_actual / m_nuevo
    return {
        "precio_nuevo": round(nuevo, 2),
        "margen_actual": round(m_actual, 2),
        "margen_nuevo": round(m_nuevo, 2),
        "margen_pct_nuevo": round(100 * m_nuevo / nuevo, 1),
        "volumen_tolerable_pct": round(100 * (1 - ratio), 1),
    }


def resumen_plata(t: pd.DataFrame) -> dict:
    """Totales por semáforo, para el encabezado de la pantalla."""
    if t.empty:
        return {}
    verde = t[t["semaforo"].str.startswith("🟢")]
    amar = t[t["semaforo"].str.startswith("🟡")]
    sangre = t[t["semaforo"].str.startswith("🩸")]
    perdida = ((sangre["costo"] - sangre["precio_prom"]) * sangre["unidades"]).clip(lower=0).sum()
    return {
        "verde_skus": len(verde),
        "verde_uyu": float(verde["margen_extra_anual"].sum()),
        "amarillo_skus": len(amar),
        "amarillo_uyu": float(amar["margen_extra_anual"].sum()),
        "bajo_costo_skus": len(sangre),
        "bajo_costo_uyu": float(perdida),
        "no_tocar_skus": int((t["semaforo"].str.startswith("🔴")).sum()),
    }
