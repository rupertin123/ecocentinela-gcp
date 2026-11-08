"""Modelo agronómico y económico de EcoCentinela (zona: Cinturón Verde de Río Cuarto).

Todo número de este archivo tiene una de dos etiquetas:
  [FUENTE]   viene de una publicación citada.
  [SUPUESTO] es un valor ilustrativo para la simulación; en un piloto real se calibraría.

Fuentes
-------
[1] SMN, Estadísticas Climatológicas Normales 1991-2020, estación Río Cuarto Aero.
[2] Greenberg, Legaspi, Jones & Enkegaard (2000). Environmental Entomology 29(4):851-860.
    Trialeurodes vaporariorum: umbral inferior de desarrollo 2,92 °C; 483,4 °D de huevo a adulto.
[3] Polack & Mitidieri (2005). Protocolo de manejo integrado de plagas en tomate, INTA San Pedro.
    Umbral de acción: 10 adultos por hoja y/o 8 ninfas por folíolo. Citado en [4].
[4] Scotta, Sánchez & Arregui (2014). FAVE Sección Ciencias Agrarias 13(1). UNL, Esperanza (Santa Fe).
    Pérdidas de rendimiento comercial por fumagina del 60 % al 80 %; hasta 77,1 adultos por hoja.
[5] UNRC: el Cinturón Verde de Río Cuarto tenía 178 invernaderos sobre unas 19,8 ha
    (≈ 0,11 ha por invernadero).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- clima [1]
# Temperatura media, máxima media y mínima media mensual (°C), enero..diciembre.
T_MEDIA = [23.0, 21.7, 20.0, 16.5, 12.8, 9.7, 8.8, 11.2, 14.0, 17.2, 20.3, 22.5]
T_MAX = [29.1, 27.7, 26.3, 22.9, 19.1, 16.3, 15.7, 18.6, 21.1, 23.9, 26.8, 29.0]
T_MIN = [17.6, 16.5, 15.1, 11.6, 8.2, 4.8, 3.8, 5.5, 8.1, 11.5, 14.3, 16.6]

# Diferencia invernadero - exterior. [SUPUESTO] valores típicos de un invernadero
# frío (sin calefacción) con ventilación lateral; se calibran con el sensor real.
DELTA_DIA_INV = 6.0
DELTA_NOCHE_INV = 1.5

ZONA_AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano


def _interpolar_mes(tabla: list[float], t: datetime) -> float:
    """Interpola entre meses tomando cada normal como valor del día 15."""
    local = t.astimezone(ZONA_AR)
    m = local.month - 1
    dia = local.day + local.hour / 24
    if dia >= 15:
        a, b, f = m, (m + 1) % 12, (dia - 15) / 30.4
    else:
        a, b, f = (m - 1) % 12, m, (dia + 15.4) / 30.4
    return tabla[a] + (tabla[b] - tabla[a]) * f


def temperatura_exterior(t: datetime) -> float:
    """Ciclo diario sinusoidal entre la mínima (06 h) y la máxima (15 h) normales."""
    tmax, tmin = _interpolar_mes(T_MAX, t), _interpolar_mes(T_MIN, t)
    h = t.astimezone(ZONA_AR).hour + t.astimezone(ZONA_AR).minute / 60
    if 6 <= h < 15:   # sube 9 h, de la mínima a la máxima
        fase = -math.cos(math.pi * (h - 6) / 9)
    else:             # baja 15 h, de la máxima a la mínima
        fase = math.cos(math.pi * ((h - 15) % 24) / 15)
    return (tmax + tmin) / 2 + (tmax - tmin) / 2 * fase


def temperatura_invernadero(t: datetime, ruido: float = 0.0) -> float:
    h = t.astimezone(ZONA_AR).hour
    delta = DELTA_DIA_INV if 9 <= h <= 18 else DELTA_NOCHE_INV
    return temperatura_exterior(t) + delta + ruido


# ---------------------------------------------------------------- plaga [2] [3] [4]
T_BASE = 2.92            # [FUENTE 2] °C
GRADOS_DIA_GEN = 483.4   # [FUENTE 2] °D de huevo a adulto
UMBRAL_ACCION = 10.0     # [FUENTE 3] adultos por hoja
CAPACIDAD_CARGA = 80.0   # [SUPUESTO] apoyado en el máximo observado de 77,1 adultos/hoja [4]
R0 = 20.0                # [SUPUESTO] tasa neta de reproducción por generación
EFICACIA_MITIGACION = 0.85  # [SUPUESTO] heredado del modelo económico de la PoC original
# Capturas diarias en la trampa amarilla por cada adulto/hoja de densidad.
# [SUPUESTO] El piloto a campo debe calibrar esta relación contando hojas a mano.
CAPTURAS_POR_ADULTO_HOJA_DIA = 4.0


def grados_dia(temp_c: float, horas: float) -> float:
    return max(0.0, temp_c - T_BASE) * horas / 24


def adultos_hoja_desde_trampa(capturas_dia: float) -> float:
    """Lo que hace la nube: pasar de capturas en trampa a densidad estimada por hoja."""
    return capturas_dia / CAPTURAS_POR_ADULTO_HOJA_DIA


# ---------------------------------------------------------------- economía [4] [5]
@dataclass(frozen=True)
class ModeloEconomico:
    """Mantiene los tres parámetros de la PoC original y agrega la superficie."""
    valor_en_riesgo_usd_ha: float = 15000.0  # PoC original; ver justificación en docs
    costo_intervencion_usd_ha: float = 300.0  # PoC original [SUPUESTO]
    eficacia: float = EFICACIA_MITIGACION
    superficie_ha: float = 0.11              # [FUENTE 5] 19,8 ha / 178 invernaderos

    def perdida_evitada(self) -> float:
        """Lucro cesante evitado al intervenir a tiempo en un invernadero (USD)."""
        bruto = self.valor_en_riesgo_usd_ha * self.superficie_ha * self.eficacia
        return round(bruto - self.costo_intervencion_usd_ha * self.superficie_ha, 2)


# ---------------------------------------------------------------- nodos (ficticios)
@dataclass(frozen=True)
class Nodo:
    id: str
    invernadero: str
    cultivo: str
    lat: float
    lon: float


# Ubicaciones FICTICIAS dentro del área periurbana de Río Cuarto (no son productores reales).
NODOS: list[Nodo] = [
    Nodo(f"RC-INV{i:02d}-N{j}", f"Invernadero {i:02d}", c, lat + 0.0006 * j, lon + 0.0004 * j)
    for i, (c, lat, lon) in enumerate(
        [("tomate", -33.0985, -64.3121), ("tomate", -33.1402, -64.3870),
         ("pimiento", -33.1588, -64.3318), ("tomate", -33.0921, -64.3712)], start=1)
    for j in (1, 2, 3)
]


@dataclass
class EstadoNodo:
    """Estado interno que el simulador guarda entre corridas (en Firestore)."""
    densidad: float                 # adultos por hoja (verdad del simulador)
    ultimo: datetime
    bateria_v: float = 4.05
    fcnt: int = 0
    mitigacion_hasta: datetime | None = None
    extra: dict = field(default_factory=dict)


def paso(estado: EstadoNodo, hasta: datetime, rng: random.Random,
         minutos: int = 15) -> tuple[int, float, float]:
    """Avanza el estado hasta `hasta`. Devuelve (capturas, temperatura, humedad_suelo)."""
    capturas = 0
    temp = temperatura_invernadero(estado.ultimo)
    t = estado.ultimo
    dt = timedelta(minutes=minutos)
    while t + dt <= hasta:
        t += dt
        temp = temperatura_invernadero(t, rng.gauss(0, 0.6))
        dd = grados_dia(temp, minutos / 60)
        n = estado.densidad
        crecimiento = math.log(R0) * n * (1 - n / CAPACIDAD_CARGA) * dd / GRADOS_DIA_GEN
        if estado.mitigacion_hasta and t <= estado.mitigacion_hasta:
            # La intervención reduce la población un `EFICACIA` en ~48 h.
            crecimiento = -n * (1 - (1 - EFICACIA_MITIGACION) ** (minutos / 60 / 48))
        estado.densidad = max(0.2, n + crecimiento * rng.uniform(0.7, 1.3))
        lam = estado.densidad * CAPTURAS_POR_ADULTO_HOJA_DIA * minutos / 1440
        capturas += _poisson(lam, rng)
        estado.bateria_v = _bateria(estado.bateria_v, t, rng)
    estado.ultimo = t
    h = t.astimezone(ZONA_AR).hour
    humedad = 30 + 8 * math.exp(-((h - 8) % 24) / 6) + rng.gauss(0, 1)  # riego por goteo a las 8 h [SUPUESTO]
    return capturas, temp, humedad


def _bateria(v: float, t: datetime, rng: random.Random) -> float:
    """Carga solar de día (variable según nubosidad) y consumo de noche. [SUPUESTO]"""
    h = t.astimezone(ZONA_AR).hour
    if 9 <= h <= 17:
        # 9 h de sol x 4 pasos: recupera en promedio ~0,30 V, más que los ~0,24 V que consume de noche.
        return min(4.15, v + 0.012 * rng.uniform(0.4, 1.0))
    return max(3.30, v - 0.004)


def _poisson(lam: float, rng: random.Random) -> int:
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def mapa_ocupacion(capturas: int, rng: random.Random) -> int:
    """Celdas de la trampa (4x4) donde el FOMO detectó centroides."""
    celdas = min(16, capturas)
    return sum(1 << i for i in rng.sample(range(16), celdas)) if celdas else 0


def ahora() -> datetime:
    return datetime.now(timezone.utc)
