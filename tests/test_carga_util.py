import pytest
from ecocentinela import carga_util as c

LECTURA = c.Lectura(37, 0.91, 23.15, 34.5, 3.94, 0b1010010001, c.BANDERA_BATERIA_BAJA)


def test_entra_en_el_peor_caso_de_au915():
    """RP002-1.0.3 tabla 45: DR2 con UplinkDwellTime=1 admite 11 bytes de aplicación."""
    assert len(c.codificar(LECTURA)) <= c.LIMITE_AU915_DR2_DWELL == 11


def test_ida_y_vuelta():
    assert c.decodificar(c.codificar(LECTURA)) == LECTURA


def test_json_de_la_poc_original_no_entraba():
    assert len(c.json_equivalente(LECTURA, "ESP32-CAM-DELTA-4")) > 51


@pytest.mark.parametrize("malo", [b"", b"\x10" * 9, b"\x20" + b"\x00" * 9])
def test_rechaza_cargas_invalidas(malo):
    with pytest.raises(c.CargaInvalida):
        c.decodificar(malo)


def test_valores_extremos_se_acotan():
    l = c.decodificar(c.codificar(c.Lectura(99999, 1.7, 500, -3, 9, 0)))
    assert l.adultos_trampa == 65535 and l.confianza == 1.0 and l.humedad_suelo_pct == 0
