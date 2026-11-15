import random
from datetime import datetime, timedelta, timezone

from ecocentinela import modelo as m

T0 = m.INICIO_RELOJ


def test_doce_nodos_tres_estratos_de_cuatro_camaras():
    assert len(m.NODOS) == 12 and len({n.id for n in m.NODOS}) == 12
    for e, altura in (("superior", 75), ("medio", 45), ("inferior", 15)):
        nodos = m.nodos_de(e)
        assert len(nodos) == 4 and {n.altura_cm for n in nodos} == {altura}
        assert len({n.punto for n in nodos}) == 4  # una cámara de cada estrato en cada mástil


def test_reloj_de_campana_ciclico():
    assert m.dds(T0) == 50 and m.estadio(T0) == "V3" or m.estadio(T0) == "V6"
    assert m.estadio(T0 + timedelta(days=30)) == "R3"
    assert m.ciclo(T0 + timedelta(days=89)) == 0 and m.ciclo(T0 + timedelta(days=91)) == 1
    assert abs(m.dds(T0 + timedelta(days=91)) - 51) < 1e-6


def test_clima_de_la_fecha_agronomica():
    # Con 30 días de reloj la fecha agronómica es fines de enero: SMN enero máx 29,1 / mín 17,6
    t = T0 + timedelta(days=30)
    siesta = t.replace(hour=18)       # 15 h en Argentina
    madrugada = t.replace(hour=9)     # 06 h
    assert m.fecha_agronomica(t).month == 1
    assert abs(m.temperatura_aire(siesta) - 29.0) < 1.0
    assert abs(m.temperatura_aire(madrugada) - 17.4) < 1.0


def test_umbrales_por_estadio():
    veg, r3, r5, r6 = (T0 + timedelta(days=d) for d in (0, 30, 50, 65))
    assert m.umbral("rachiplusia", veg) == 20 and m.umbral("rachiplusia", r3) == 10
    assert m.umbral("piezodorus", r3) == 0.8 and m.umbral("piezodorus", r5) == 2 and m.umbral("piezodorus", r6) == 7
    assert m.umbral("caliothrips", r5) == 25


def test_las_chinches_llegan_desde_afuera_en_r2():
    rng = random.Random(3)
    p = m.PLAGAS["piezodorus"]
    e = m.EstadoNodo(sin_tratar=0.0, ultimo=T0)
    m.avanzar(p, e, T0 + timedelta(days=10), rng)
    assert e.sin_tratar == 0.0  # todavía no colonizan (DDS < 66)
    m.avanzar(p, e, T0 + timedelta(days=30), rng)
    assert 0.4 < e.sin_tratar < 3


def test_conteo_y_densidad_son_inversos_en_promedio():
    rng = random.Random(9)
    p = m.PLAGAS["caliothrips"]
    conteos = [m.conteo_imagen(p, 20.0, rng) for _ in range(2000)]
    assert abs(m.densidad_desde_conteos(p, conteos) - 20.0) < 0.6


def test_solo_fotos_de_dia():
    assert m.es_de_dia(datetime(2026, 11, 15, 15, tzinfo=timezone.utc))      # 12 h AR
    assert not m.es_de_dia(datetime(2026, 11, 15, 3, tzinfo=timezone.utc))  # 00 h AR


def test_bateria_solar_no_se_descarga():
    rng = random.Random(5)
    e = m.EstadoNodo(sin_tratar=1.0, ultimo=T0, bateria_v=4.0)
    m.avanzar(m.PLAGAS["rachiplusia"], e, T0 + timedelta(days=30), rng)
    assert e.bateria_v > 3.7
