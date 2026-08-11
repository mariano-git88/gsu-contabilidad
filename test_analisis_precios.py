"""Tests de analisis_precios.py — casos construidos a mano, sin red ni Sheet.

Correr: python test_analisis_precios.py
"""

from __future__ import annotations

import pandas as pd

import analisis_precios as ap


def _lineas(filas):
    """Arma un DF con el schema de load_fc_api a partir de tuplas cortas."""
    return pd.DataFrame([{
        "documento": f"D{i}", "razon_social": cli, "vendedor": "v@x.com",
        "fecha": pd.Timestamp(fecha), "tipo": "FAC", "moneda": "UYU",
        "sku": sku, "producto": sku, "unidades": u, "monto": u * p,
    } for i, (fecha, sku, cli, u, p) in enumerate(filas)])


def _serie(sku, precios_por_mes, unidades_por_mes, n_clientes=10):
    """Genera 12 meses de venta con el precio y volumen pedidos."""
    filas = []
    for i, (p, u) in enumerate(zip(precios_por_mes, unidades_por_mes)):
        mes = pd.Timestamp("2025-09-01") + pd.DateOffset(months=i)
        for c in range(n_clientes):
            filas.append((mes + pd.Timedelta(days=c), sku, f"CLI{c}", u / n_clientes, p))
    return filas


def test_bajaron_precio_sin_ganar_volumen():
    """Precio -10%, volumen plano -> verde, con suba sugerida al precio viejo."""
    f = _serie("BAJO", [100] * 6 + [90] * 6, [100] * 12)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {"BAJO": 40.0})
    r = t.loc["BAJO"]
    assert r["semaforo"].startswith("🟢"), r["semaforo"]
    assert r["d_precio_pct"] < -5, r["d_precio_pct"]
    assert r["suba_sugerida"] > 0, "tiene que sugerir recuperar el precio"
    print("OK  bajaron precio sin ganar volumen -> verde")


def test_subieron_precio_sin_perder_volumen():
    """Precio +10%, volumen plano -> verde y sugiere repetir la suba."""
    f = _serie("SUBE", [100] * 6 + [110] * 6, [100] * 12)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {"SUBE": 40.0})
    r = t.loc["SUBE"]
    assert r["semaforo"].startswith("🟢"), r["semaforo"]
    assert r["suba_sugerida"] > 0, "si absorbió una suba, tiene que sugerir otra"
    print("OK  subieron precio sin perder volumen -> verde")


def test_elastico_no_tocar():
    """Precio -10% y volumen +80% -> rojo."""
    f = _serie("ELAS", [100] * 6 + [90] * 6, [100] * 6 + [180] * 6)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {"ELAS": 40.0})
    r = t.loc["ELAS"]
    assert r["semaforo"].startswith("🔴"), r["semaforo"]
    assert r["suba_sugerida"] == 0, "a un elástico no se le sugiere subir"
    print("OK  elástico -> rojo, sin sugerencia")


def test_estacional_no_se_clasifica():
    """Volumen concentrado en 5 meses consecutivos -> se marca estacional.

    Ojo: un estacional real vende POCO fuera de temporada, no cero. Con
    ceros literales el SKU no llega al mínimo de meses con venta y cae en
    "sin datos" antes de que se evalúe la estacionalidad — que es la
    respuesta correcta, pero no es lo que este test quiere probar.
    """
    u = [10, 10, 10, 300, 300, 300, 300, 300, 10, 10, 10, 10]
    f = _serie("TEMP", [100] * 12, u)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {"TEMP": 40.0})
    r = t.loc["TEMP"]
    assert "estacional" in r["semaforo"], r["semaforo"]
    print(f"OK  estacional detectado (índice {r['indice_estacional']})")


def test_no_estacional_parejo():
    """Volumen parejo -> NO estacional (el falso positivo que rompía todo)."""
    f = _serie("PAREJO", [100] * 12, [100] * 12)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {"PAREJO": 40.0})
    assert not t.loc["PAREJO"]["estacional"], "un SKU parejo no puede ser estacional"
    print(f"OK  parejo NO estacional (índice {t.loc['PAREJO']['indice_estacional']})")


def test_bajo_costo_manda():
    """Vender bajo costo pisa cualquier otra clasificación."""
    f = _serie("PERD", [30] * 12, [100] * 12)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {"PERD": 50.0})
    assert t.loc["PERD"]["semaforo"].startswith("🩸"), t.loc["PERD"]["semaforo"]
    print("OK  bajo costo -> sangría, pisa al resto")


def test_sin_costo_no_se_clasifica():
    f = _serie("NOCOST", [100] * 12, [100] * 12)
    t = ap.analizar(ap.preparar_lineas(_lineas(f)), {})
    assert "sin costo" in t.loc["NOCOST"]["semaforo"]
    print("OK  sin costo -> no se clasifica")


def test_ncf_no_ensucia_el_precio():
    """Una NCF (unidades negativas) no debe entrar al cálculo de precio."""
    f = _serie("NCF", [100] * 12, [100] * 12)
    d = _lineas(f)
    mala = d.iloc[[0]].copy()
    mala["unidades"] = -10
    mala["monto"] = -1000
    mala["tipo"] = "NCF"
    lin = ap.preparar_lineas(pd.concat([d, mala], ignore_index=True))
    assert (lin["unidades"] > 0).all(), "quedaron unidades negativas en el análisis de precio"
    print("OK  las NCF quedan fuera del precio unitario")


def test_simulador_volumen_tolerable():
    """Con margen 50 y suba 10% sobre 100, el volumen tolerable es 16,7%."""
    s = ap.simular(precio=100.0, costo=50.0, suba_pct=10.0)
    assert s["precio_nuevo"] == 110.0
    assert s["margen_nuevo"] == 60.0
    assert abs(s["volumen_tolerable_pct"] - 16.7) < 0.1, s
    print(f"OK  simulador: se puede perder {s['volumen_tolerable_pct']}% del volumen")


if __name__ == "__main__":
    test_bajaron_precio_sin_ganar_volumen()
    test_subieron_precio_sin_perder_volumen()
    test_elastico_no_tocar()
    test_estacional_no_se_clasifica()
    test_no_estacional_parejo()
    test_bajo_costo_manda()
    test_sin_costo_no_se_clasifica()
    test_ncf_no_ensucia_el_precio()
    test_simulador_volumen_tolerable()
    print("\nTodos los tests pasaron.")
