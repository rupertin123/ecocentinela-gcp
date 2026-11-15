import base64
import random
from datetime import timedelta

from fastapi.testclient import TestClient

import main as ingesta
from ecocentinela import carga_util, chirpstack, modelo

cli = TestClient(ingesta.app)
T0 = (modelo.INICIO_RELOJ + timedelta(days=30)).replace(hour=10)  # 07 h AR, R3


def _evento(nodo="RC-SUP-P1", fcnt=1, carga=None, horas=0):
    carga = carga or carga_util.codificar(carga_util.Lectura(5, 0.9, 22.0, 60.0, 3.9, 3))
    return chirpstack.evento_up(nodo, fcnt, carga, T0 + timedelta(hours=horas), random.Random(1))


def test_acepta_y_publica():
    r = cli.post("/v1/uplink", json=_evento())
    assert r.status_code == 202
    topico, fila = ingesta.destinos().publicados[-1]
    assert topico == ingesta.TOPICO and fila["bytes_carga"] == 10 and fila["sf"] == 10
    assert fila["estrato"] == "superior" and fila["altura_cm"] == 75 and fila["unidad"] == "larvas/m"


def test_duplicado_se_ignora():
    cli.post("/v1/uplink", json=_evento("RC-SUP-P2", 7))
    r = cli.post("/v1/uplink", json=_evento("RC-SUP-P2", 7))
    assert r.json() == {"duplicado": True}


def test_densidad_es_la_media_de_las_fotos_de_24h():
    for i in range(14):  # un día de fotos, 6 chinches por foto
        carga = carga_util.codificar(carga_util.Lectura(6, 0.9, 25.0, 55.0, 3.9))
        r = cli.post("/v1/uplink", json=_evento("RC-MED-P1", i + 1, carga, horas=i))
    # 6 por foto / 0,30 individuos visibles por chinche/m = 20 chinches/m
    assert abs(r.json()["densidad_est"] - 20.0) < 0.01 and r.json()["supera_umbral"] is True


def test_las_fotos_de_ayer_salen_de_la_ventana():
    muchas = carga_util.codificar(carga_util.Lectura(30, 0.9, 25.0, 55.0, 3.9))
    pocas = carga_util.codificar(carga_util.Lectura(0, 0.9, 25.0, 55.0, 3.9))
    for i in range(10):
        cli.post("/v1/uplink", json=_evento("RC-INF-P1", i + 1, muchas, horas=i))
    for i in range(10):
        r = cli.post("/v1/uplink", json=_evento("RC-INF-P1", 20 + i, pocas, horas=25 + i))
    assert r.json()["densidad_est"] == 0.0


def test_foto_con_lente_obstruida_no_cuenta():
    buena = carga_util.codificar(carga_util.Lectura(4, 0.9, 25.0, 55.0, 3.9))
    mala = carga_util.codificar(carga_util.Lectura(200, 0.4, 25.0, 55.0, 3.9, 0, carga_util.BANDERA_LENTE_OBSTRUIDA))
    cli.post("/v1/uplink", json=_evento("RC-INF-P2", 1, buena))
    r = cli.post("/v1/uplink", json=_evento("RC-INF-P2", 2, mala, horas=1))
    assert r.json()["densidad_est"] == 8.0  # 4 / 0,5


def test_nodo_desconocido_va_a_rechazos():
    r = cli.post("/v1/uplink", json=_evento("INTRUSO"))
    assert r.status_code == 404
    assert ingesta.destinos().publicados[-1][0] == ingesta.TOPICO_RECHAZOS


def test_carga_corrupta():
    ev = _evento("RC-SUP-P3")
    ev["data"] = base64.b64encode(b"{\"t\":23.1}").decode()
    assert cli.post("/v1/uplink", json=ev).status_code == 422


def test_bateria_fuera_de_rango():
    carga = bytearray(carga_util.codificar(carga_util.Lectura(1, 0.9, 22, 35, 3.9)))
    carga[7] = 0  # 2,50 V: imposible para una celda de litio en uso
    assert cli.post("/v1/uplink", json=_evento("RC-SUP-P4", carga=bytes(carga))).status_code == 422


def test_eventos_que_no_son_up_se_ignoran():
    r = cli.post("/v1/uplink?event=join", json=_evento("RC-MED-P3"))
    assert r.json() == {"ignorado": "join"}


def test_primeras_fotos_son_provisorias():
    muchas = carga_util.codificar(carga_util.Lectura(30, 0.9, 22.0, 35.0, 3.9))
    r = cli.post("/v1/uplink", json=_evento("RC-MED-P4", 1, muchas))
    assert r.json()["densidad_est"] > 1 and r.json()["supera_umbral"] is False
