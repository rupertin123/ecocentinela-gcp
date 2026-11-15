from datetime import timedelta

from ecocentinela import decision, eficacia, modelo

T = (modelo.INICIO_RELOJ + timedelta(days=30)).replace(hour=17)  # R3, 14 h en Argentina


def _estados(estrato, densidad, fotos=10):
    return {n.id: {"medido": (T - timedelta(minutes=30)).isoformat(), "densidad_est": densidad,
                   "fotos_ventana": fotos} for n in modelo.nodos_de(estrato)}


def test_recomienda_cuando_supera_el_umbral_sin_proteccion():
    r = decision.evaluar("medio", _estados("medio", 1.6), [], {}, T)
    assert r["estado"] == "RECOMENDAR" and r["umbral"] == 0.8
    assert r["producto_sugerido"] == "tiametoxam + lambdacialotrina"


def test_espera_el_efecto_las_primeras_72_h():
    apl = [{"id": "a1", "producto": "tiametoxam_lambdacialotrina", "aplicada_en": (T - timedelta(hours=30)).isoformat()}]
    assert decision.evaluar("medio", _estados("medio", 1.6), apl, {}, T)["estado"] == "ESPERAR_EFECTO"


def test_protegido_con_fecha_de_descubierto_bibliografica():
    aplicada = T - timedelta(days=5)
    apl = [{"id": "a1", "producto": "clorantraniliprol", "aplicada_en": aplicada.isoformat()}]
    r = decision.evaluar("superior", _estados("superior", 3.0), apl, {}, T)
    assert r["estado"] == "PROTEGIDO" and r["proteccion"]["origen"] == "bibliografia"
    p = eficacia.PRODUCTOS["clorantraniliprol"]
    esperado = aplicada + timedelta(days=eficacia.dias_hasta_descubierto(p.e0, p.k))
    assert r["proteccion"]["fecha_descubierto"] == esperado.isoformat()


def test_el_ajuste_de_bigquery_reemplaza_a_la_bibliografia():
    apl = [{"id": "a1", "producto": "clorantraniliprol", "aplicada_en": (T - timedelta(days=12)).isoformat()}]
    ajuste = {"a1": {"e0": 0.93, "k": 0.0693, "r2": 0.95, "dias": 9}}
    r = decision.evaluar("superior", _estados("superior", 12.0), apl, ajuste, T)
    # con el ajuste de campo (vida media 10 d) el lote ya está descubierto: vuelve a recomendar
    assert r["proteccion"]["origen"] == "bigquery" and not r["proteccion"]["vigente"]
    assert r["estado"] == "RECOMENDAR"


def test_supera_el_umbral_con_proteccion_vigente_es_revisar_control():
    apl = [{"id": "a1", "producto": "clorantraniliprol", "aplicada_en": (T - timedelta(days=4)).isoformat()}]
    assert decision.evaluar("superior", _estados("superior", 15.0), apl, {}, T)["estado"] == "REVISAR_CONTROL"


def test_vigilar_y_sin_accion():
    assert decision.evaluar("inferior", _estados("inferior", 20.0), [], {}, T)["estado"] == "VIGILAR"
    assert decision.evaluar("inferior", _estados("inferior", 5.0), [], {}, T)["estado"] == "SIN_ACCION"


def test_datos_insuficientes():
    est = _estados("medio", 1.6, fotos=3)
    assert decision.evaluar("medio", est, [], {}, T)["estado"] == "DATOS_INSUFICIENTES"
    est = dict(list(_estados("medio", 1.6).items())[:2])  # sólo 2 de 4 nodos
    assert decision.evaluar("medio", est, [], {}, T)["estado"] == "DATOS_INSUFICIENTES"
