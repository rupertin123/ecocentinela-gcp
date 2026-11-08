import random
from datetime import datetime, timedelta, timezone
from ecocentinela import modelo as m


def test_clima_noviembre_rio_cuarto():
    # SMN 1991-2020: noviembre máx 26,8 / mín 14,3
    siesta = datetime(2026, 11, 15, 18, tzinfo=timezone.utc)  # 15 h en Argentina
    madrugada = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)  # 06 h
    assert abs(m.temperatura_exterior(siesta) - 26.9) < 1.0
    assert abs(m.temperatura_exterior(madrugada) - 14.6) < 1.5


def test_sin_desarrollo_debajo_del_umbral_termico():
    assert m.grados_dia(2.0, 24) == 0
    assert m.grados_dia(12.92, 24) == 10


def test_la_poblacion_crece_en_primavera_y_baja_con_mitigacion():
    rng = random.Random(1)
    t0 = datetime(2026, 10, 9, tzinfo=timezone.utc)
    e = m.EstadoNodo(densidad=1.0, ultimo=t0)
    m.paso(e, t0 + timedelta(days=20), rng)
    assert e.densidad > 3
    antes = e.densidad
    e.mitigacion_hasta = e.ultimo + timedelta(hours=48)
    m.paso(e, e.ultimo + timedelta(hours=48), rng)
    assert e.densidad < antes * 0.3


def test_perdida_evitada():
    # 15000 USD/ha * 0,11 ha * 0,85 - 300 USD/ha * 0,11 ha
    assert m.ModeloEconomico().perdida_evitada() == 1369.5


def test_doce_nodos_ficticios():
    assert len(m.NODOS) == 12 and len({n.id for n in m.NODOS}) == 12
