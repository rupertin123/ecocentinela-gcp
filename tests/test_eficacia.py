import math
import random
import re
from datetime import timedelta
from pathlib import Path

from ecocentinela import eficacia as ef, modelo

RAIZ = Path(__file__).resolve().parent.parent


def test_ajuste_a_los_datos_de_inta_oliveros():
    """Perotti, Gamundi & Russo (2010): adultos de P. guildinii, tiametoxam + lambdacialotrina."""
    a = ef.ajustar([(2, .89), (3, .84), (6, .38), (10, .82), (14, .58)])
    p = ef.PRODUCTOS["tiametoxam_lambdacialotrina"]
    assert abs(a["e0"] - p.e0) < 0.005 and abs(a["k"] - p.k) < 0.0005


def test_el_ajuste_recupera_parametros_conocidos():
    rng = random.Random(1)
    pts = [(d / 2, 0.9 * math.exp(-0.07 * d / 2) * rng.uniform(0.97, 1.03)) for d in range(2, 30)]
    a = ef.ajustar(pts)
    assert abs(a["k"] - 0.07) < 0.01 and abs(a["e0"] - 0.9) < 0.05 and a["r2"] > 0.9


def test_con_menos_de_3_dias_no_hay_ajuste():
    assert ef.ajustar([(1.0, .9), (1.5, .85), (2.2, .8)]) is None


def test_fecha_de_descubierto():
    p = ef.PRODUCTOS["clorantraniliprol"]
    # vida media 14 d y E0 0,95: E = 0,5 a los 14·log2(1,9) ≈ 13,0 días
    assert abs(ef.dias_hasta_descubierto(p.e0, p.k) - 14 * math.log2(1.9)) < 1e-9
    assert ef.dias_hasta_descubierto(0.4, 0.1) == 0.0


def test_eficacia_en_campo_combina_aplicaciones_y_respeta_la_plaga():
    t = modelo.INICIO_RELOJ + timedelta(days=30)
    apl = [{"producto": "clorantraniliprol", "aplicada_en": t - timedelta(days=2)}]
    e = ef.eficacia_en_campo(apl, "rachiplusia", t)
    p = ef.PRODUCTOS["clorantraniliprol"]
    assert abs(e - p.e0_campo * math.exp(-p.k_campo * 2)) < 1e-9
    assert ef.eficacia_en_campo(apl, "piezodorus", t) == 0.0  # el producto no es para chinches
    doble = ef.eficacia_en_campo(apl * 2, "rachiplusia", t)
    assert abs(doble - (1 - (1 - e) ** 2)) < 1e-9


def test_el_sql_usa_los_mismos_parametros_que_el_modelo():
    sql = (RAIZ / "sql" / "eficacia_puntos.sql").read_text()
    for p in modelo.PLAGAS.values():
        m = re.search(rf"'{p.estrato}' AS estrato, ([\d.]+) AS capacidad, ([\d.]+) AS tasa_max", sql)
        assert m and float(m.group(1)) == p.capacidad and float(m.group(2)) == p.tasa_max_dia
    assert "0.5" in (RAIZ / "sql" / "eficacia_residual.sql").read_text() and ef.E_MIN == 0.5
