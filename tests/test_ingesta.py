import base64
import random
from datetime import timedelta

from fastapi.testclient import TestClient

import main as ingesta
from ecocentinela import carga_util, chirpstack, modelo

cli = TestClient(ingesta.app)


def _evento(nodo="RC-INV01-N1", fcnt=1, carga=None, minutos=0):
    carga = carga or carga_util.codificar(carga_util.Lectura(5, 0.9, 22.0, 35.0, 3.9, 3))
    t = modelo.ahora() + timedelta(minutes=minutos)
    return chirpstack.evento_up(nodo, fcnt, carga, t, random.Random(1))


def test_acepta_y_publica():
    r = cli.post("/v1/uplink", json=_evento())
    assert r.status_code == 202
    topico, fila = ingesta.destinos().publicados[-1]
    assert topico == ingesta.TOPICO and fila["bytes_carga"] == 10 and fila["sf"] == 10


def test_duplicado_se_ignora():
    cli.post("/v1/uplink", json=_evento("RC-INV01-N2", 7))
    r = cli.post("/v1/uplink", json=_evento("RC-INV01-N2", 7))
    assert r.json() == {"duplicado": True}


def test_densidad_con_ventana_movil_de_24h():
    carga = carga_util.codificar(carga_util.Lectura(10, 0.9, 22.0, 35.0, 3.9))
    for i in range(96):  # un día completo, 10 capturas cada 15 min = 960 capturas por día
        r = cli.post("/v1/uplink", json=_evento("RC-INV03-N1", i + 1, carga, minutos=15 * i))
    # 960 / 4 capturas por adulto-hoja-día = 240 adultos por hoja
    assert abs(r.json()["adultos_hoja_est"] - 240.0) < 0.5


def test_una_ventana_sin_capturas_no_desploma_la_estimacion():
    con = carga_util.codificar(carga_util.Lectura(1, 0.9, 22.0, 35.0, 3.9))
    sin = carga_util.codificar(carga_util.Lectura(0, 0.9, 22.0, 35.0, 3.9))
    for i in range(40):
        r = cli.post("/v1/uplink", json=_evento("RC-INV03-N2", i + 1, con if i % 2 else sin, minutos=15 * i))
    assert 10 < r.json()["adultos_hoja_est"] < 14  # 0,5 capturas / 15 min = 48 por día = 12 por hoja


def test_nodo_desconocido_va_a_rechazos():
    r = cli.post("/v1/uplink", json=_evento("INTRUSO"))
    assert r.status_code == 404
    assert ingesta.destinos().publicados[-1][0] == ingesta.TOPICO_RECHAZOS


def test_carga_corrupta():
    ev = _evento("RC-INV04-N1")
    ev["data"] = base64.b64encode(b"{\"t\":23.1}").decode()
    assert cli.post("/v1/uplink", json=ev).status_code == 422


def test_bateria_fuera_de_rango():
    carga = bytearray(carga_util.codificar(carga_util.Lectura(1, 0.9, 22, 35, 3.9)))
    carga[7] = 0  # 2,50 V: imposible para una celda de litio en uso
    assert cli.post("/v1/uplink", json=_evento("RC-INV04-N2", carga=bytes(carga))).status_code == 422


def test_eventos_que_no_son_up_se_ignoran():
    r = cli.post("/v1/uplink?event=join", json=_evento("RC-INV04-N3"))
    assert r.json() == {"ignorado": "join"}


def test_primeras_horas_son_provisorias():
    muchas = carga_util.codificar(carga_util.Lectura(30, 0.9, 22.0, 35.0, 3.9))
    r = cli.post("/v1/uplink", json=_evento("RC-INV02-N3", 1, muchas))
    assert r.json()["adultos_hoja_est"] > 10 and r.json()["supera_umbral"] is False
