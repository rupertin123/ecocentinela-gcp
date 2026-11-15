"""Modelo agronómico de EcoCentinela v2: soja a campo, 3 plagas en 3 estratos de la planta.

Todo número de este archivo tiene una de dos etiquetas:
  [FUENTE]   viene de una publicación citada (ver lista abajo).
  [SUPUESTO] es un valor ilustrativo de la simulación; un piloto a campo lo calibraría.

Por qué soja y por qué estas tres plagas
----------------------------------------
El departamento Río Cuarto encabeza la superficie de soja y de maíz de Córdoba [1]. En soja hay
tres plagas clave que viven, de preferencia, en estratos distintos de la planta y que se ven desde
afuera (la cámara no puede ver plagas que viven DENTRO del tallo, como la barrenadora del maíz):

* SUPERIOR  oruga medidora, Rachiplusia nu: habita la parte superior del canopeo [2].
* MEDIO     chinche de la alfalfa, Piezodorus guildinii: las ninfas se ubican «por lo general en los
            estratos medio e inferior del canopeo» [3] y los adultos ponen el 70-82 % de sus posturas
            fuera de los 35 cm superiores [4].
* INFERIOR  trips, Caliothrips phaseoli: larvas y adultos forman colonias «preferentemente en las
            hojas inferiores, protegidos de la radiación solar» [5].

Fuentes
-------
[1] Bolsa de Cereales de Córdoba, estimación de siembra 2024/25 (Río Cuarto: 1.º en soja y maíz).
[2] Insects 2025, 16(4):365 (doi 10.3390/insects16040365) y Population Fluctuation and Vertical
    Distribution of the Soybean Looper, SCIRP 2018 (id 85603): R. nu en el tercio superior,
    Chrysodeixis includens en el medio-inferior.
[3] Massaro & Gonsebatt, «Evaluación del control de hemípteros fitófagos en soja...», INTA EEA Oliveros.
[4] Temple, Davis, Hardke, Price & Leonard (2016). Insects 7(2):27, doi 10.3390/insects7020027.
[5] Alonso, M. (2023). Caliothrips phaseoli: características morfológicas. INTA EEA Balcarce.
[6] Iannone (INTA), umbral orugas defoliadoras: >20 orugas (>1,5 cm)/m en vegetativo y >10/m en
    reproductivo (citado en Giuggia et al., Rev. FCA UNCuyo 43(1), 2011).
[7] INTA Pergamino, umbrales de chinche verde (surcos a 70 cm): 0,8-1/m en R3-R4, 2/m en R5,
    7-8/m en R6-R7 (complejo de chinches; se usa el valor inferior de cada rango).
[8] Gamundi et al. (2006), INTA Oliveros: 25 trips por folíolo en R5. No hay umbral oficial vigente
    para C. phaseoli en Argentina (Igarzábal et al., 2009): se usa como referencia.
[9] SMN, Estadísticas Climatológicas Normales 1991-2020, estación Río Cuarto Aero.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

ZONA_AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano

# ---------------------------------------------------------------- clima [9]
# Temperatura media, máxima media y mínima media mensual (°C), enero..diciembre.
T_MAX = [29.1, 27.7, 26.3, 22.9, 19.1, 16.3, 15.7, 18.6, 21.1, 23.9, 26.8, 29.0]
T_MIN = [17.6, 16.5, 15.1, 11.6, 8.2, 4.8, 3.8, 5.5, 8.1, 11.5, 14.3, 16.6]

# ---------------------------------------------------------------- calendario de campaña
# El lote es simulado. Para que la demostración muestre siempre las etapas en las que se toman
# decisiones (V6 a R7), el simulador usa un RELOJ DE CAMPAÑA: la campaña se repite cada 90 días.
# [SUPUESTO] siembra de primera el 10/11 y fenología aproximada de un grupo de madurez IV-V en el sur
# de Córdoba (días desde la siembra, DDS). Un piloto real usaría la fecha de siembra y el monitoreo.
SIEMBRA_REFERENCIA = date(2025, 11, 10)
INICIO_RELOJ = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)  # 16/10/2026 00:00 en Argentina
DDS_INICIO, DURACION_CICLO_DIAS = 50, 90
ESTADIOS = [  # (DDS desde el que rige, estadio)
    (0, "VE"), (25, "V3"), (40, "V6"), (55, "R1"), (65, "R2"), (75, "R3"), (85, "R4"),
    (95, "R5"), (110, "R6"), (130, "R7"), (145, "R8"),
]


def dds(t: datetime) -> float:
    """Días desde la siembra en el reloj de campaña (de 50 a 140, y vuelve a empezar)."""
    dias = (t - INICIO_RELOJ).total_seconds() / 86400
    return DDS_INICIO + dias % DURACION_CICLO_DIAS


def ciclo(t: datetime) -> int:
    """Número de campaña simulada (cambia cada 90 días)."""
    return int((t - INICIO_RELOJ).total_seconds() // (86400 * DURACION_CICLO_DIAS))


def estadio(t: datetime) -> str:
    d = dds(t)
    return [e for desde, e in ESTADIOS if d >= desde][-1]


def fecha_agronomica(t: datetime) -> date:
    """Fecha del calendario de campaña que corresponde a `t` (para el clima y el tablero)."""
    return SIEMBRA_REFERENCIA + timedelta(days=int(dds(t)))


def reproductivo(t: datetime) -> bool:
    return estadio(t).startswith("R")


def _interpolar_mes(tabla: list[float], dia: date, hora: float) -> float:
    """Interpola entre meses tomando cada normal como valor del día 15."""
    m, d = dia.month - 1, dia.day + hora / 24
    if d >= 15:
        a, b, f = m, (m + 1) % 12, (d - 15) / 30.4
    else:
        a, b, f = (m - 1) % 12, m, (d + 15.4) / 30.4
    return tabla[a] + (tabla[b] - tabla[a]) * f


def temperatura_aire(t: datetime) -> float:
    """Ciclo diario sinusoidal entre la mínima (06 h) y la máxima (15 h) normales de la fecha agronómica."""
    local = t.astimezone(ZONA_AR)
    h = local.hour + local.minute / 60
    dia = fecha_agronomica(t)
    tmax, tmin = _interpolar_mes(T_MAX, dia, h), _interpolar_mes(T_MIN, dia, h)
    if 6 <= h < 15:
        fase = -math.cos(math.pi * (h - 6) / 9)
    else:
        fase = math.cos(math.pi * ((h - 15) % 24) / 15)
    return (tmax + tmin) / 2 + (tmax - tmin) / 2 * fase


def humedad_relativa(temp_c: float, rng: random.Random) -> float:
    """Humedad relativa del aire: más baja en las horas de calor. [SUPUESTO] relación simple."""
    return max(20.0, min(98.0, 95 - 2.2 * (temp_c - 12) + rng.gauss(0, 4)))


# ---------------------------------------------------------------- plagas
@dataclass(frozen=True)
class Plaga:
    codigo: str
    estrato: str
    nombre: str
    cientifico: str
    unidad: str
    altura_cm: int            # altura de la cámara sobre el suelo
    individuos_por_unidad: float  # cuántos individuos ve una imagen por cada unidad de densidad
    capacidad: float          # techo de la población (unidad agronómica) [SUPUESTO]
    tasa_max_dia: float       # crecimiento intrínseco máximo por día [SUPUESTO]
    desde_dds: float          # desde cuándo coloniza el lote [SUPUESTO]
    inmigracion_dia: float = 0.0  # llegada de adultos desde lotes vecinos (unidad/día) [SUPUESTO]

    def umbral(self, t: datetime) -> float:
        return umbral(self.codigo, t)


PLAGAS: dict[str, Plaga] = {
    # Imagen lateral del canopeo superior: cubre ~0,5 m de surco; se ve ~60 % de las orugas. [SUPUESTO]
    "rachiplusia": Plaga("rachiplusia", "superior", "Oruga medidora", "Rachiplusia nu",
                         "larvas/m", 75, 0.30, 45.0, 0.11, 40),
    # Imagen lateral del estrato medio: ~0,5 m de surco; las chinches se esconden. [SUPUESTO]
    "piezodorus": Plaga("piezodorus", "medio", "Chinche de la alfalfa", "Piezodorus guildinii",
                        "chinches/m", 45, 0.30, 14.0, 0.09, 66, inmigracion_dia=0.07),
    # Macro sobre un folíolo inferior: se ve la cara superior (~50 % de los trips). [SUPUESTO]
    "caliothrips": Plaga("caliothrips", "inferior", "Trips de la soja", "Caliothrips phaseoli",
                         "trips/folíolo", 15, 0.50, 90.0, 0.09, 45),
}
POR_ESTRATO = {p.estrato: p for p in PLAGAS.values()}
ESTRATOS = ["superior", "medio", "inferior"]


def umbral(codigo: str, t: datetime) -> float:
    """Umbral de acción de referencia según la plaga y el estadio del cultivo."""
    e = estadio(t)
    if codigo == "rachiplusia":                         # [FUENTE 6]
        return 10.0 if e.startswith("R") else 20.0
    if codigo == "piezodorus":                          # [FUENTE 7]
        if e in ("R3", "R4"):
            return 0.8
        if e == "R5":
            return 2.0
        if e in ("R6", "R7", "R8"):
            return 7.0
        return 0.8  # antes de R3 la chinche casi no daña, pero se toma el umbral más exigente
    if codigo == "caliothrips":                         # [FUENTE 8]
        return 25.0
    raise KeyError(codigo)


def fuente_umbral(codigo: str) -> str:
    return {
        "rachiplusia": "Iannone (INTA): >20 larvas >1,5 cm/m en vegetativo, >10/m en reproductivo",
        "piezodorus": "INTA Pergamino (chinche verde, surcos a 70 cm): 0,8/m R3-R4 · 2/m R5 · 7/m R6-R7",
        "caliothrips": "Gamundi et al. (2006), INTA Oliveros: 25 trips/folíolo en R5 (sin umbral oficial vigente)",
    }[codigo]


# ---------------------------------------------------------------- nodos (ficticios)
@dataclass(frozen=True)
class Nodo:
    id: str
    punto: str      # mástil de muestreo (P1..P4): cada mástil lleva 3 cámaras a distinta altura
    plaga: str
    lat: float
    lon: float

    @property
    def estrato(self) -> str:
        return PLAGAS[self.plaga].estrato

    @property
    def altura_cm(self) -> int:
        return PLAGAS[self.plaga].altura_cm


# Lote FICTICIO de soja al este de Río Cuarto (no es un productor real). 4 mástiles en el lote,
# separados ~300 m, cada uno con 3 nodos: 4 cámaras por plaga y por altura.
_MASTILES = {"P1": (-33.0712, -64.2405), "P2": (-33.0731, -64.2371),
             "P3": (-33.0748, -64.2408), "P4": (-33.0729, -64.2442)}
_SIGLA = {"superior": "SUP", "medio": "MED", "inferior": "INF"}
NODOS: list[Nodo] = [
    Nodo(f"RC-{_SIGLA[p.estrato]}-{m}", m, p.codigo, lat, lon)
    for p in PLAGAS.values() for m, (lat, lon) in _MASTILES.items()
]
POR_ID = {n.id: n for n in NODOS}


def nodos_de(estrato: str) -> list[Nodo]:
    return [n for n in NODOS if n.estrato == estrato]


# ---------------------------------------------------------------- dinámica de la plaga
def factor_temperatura(temp_c: float) -> float:
    """Crecimiento relativo según temperatura. [SUPUESTO] lineal entre 10 °C (0) y 28 °C (1), cae arriba de 34 °C."""
    if temp_c <= 10:
        return 0.0
    if temp_c <= 28:
        return (temp_c - 10) / 18
    return max(0.0, 1 - (temp_c - 28) / 12)


def factor_fenologico(plaga: Plaga, t: datetime) -> float:
    """La plaga coloniza desde cierto estadio y su crecimiento cae con la madurez (R7). [SUPUESTO]"""
    d = dds(t)
    if d < plaga.desde_dds:
        return 0.0
    if d >= 130:
        return -0.5  # el cultivo madura: la población declina
    return min(1.0, (d - plaga.desde_dds) / 10 + 0.3)


@dataclass
class EstadoNodo:
    """Estado interno que el simulador guarda entre corridas (en Firestore)."""
    sin_tratar: float              # densidad «testigo»: la que habría sin insecticida
    ultimo: datetime
    bateria_v: float = 4.05
    fcnt: int = 0
    ciclo: int = 0
    extra: dict = field(default_factory=dict)


def densidad_inicial(plaga: Plaga, rng: random.Random) -> float:
    return {"rachiplusia": rng.uniform(1.0, 3.0), "piezodorus": rng.uniform(0.02, 0.08),
            "caliothrips": rng.uniform(2.0, 4.0)}[plaga.codigo]


def avanzar(plaga: Plaga, e: EstadoNodo, hasta: datetime, rng: random.Random, paso_min: int = 60) -> None:
    """Avanza la población «sin tratar» hasta `hasta` con crecimiento logístico."""
    t, dt = e.ultimo, timedelta(minutes=paso_min)
    while t + dt <= hasta:
        t += dt
        r = plaga.tasa_max_dia * factor_temperatura(temperatura_aire(t)) * factor_fenologico(plaga, t)
        n = max(e.sin_tratar, 0.01 if dds(t) >= plaga.desde_dds else 0.0)
        llegan = plaga.inmigracion_dia if plaga.desde_dds <= dds(t) < 125 else 0.0
        crece = r * n * (1 - n / plaga.capacidad) + llegan
        e.sin_tratar = max(0.0, n + crece * paso_min / 1440 * rng.uniform(0.6, 1.4))
        e.bateria_v = _bateria(e.bateria_v, t, rng)
    e.ultimo = t


def _bateria(v: float, t: datetime, rng: random.Random) -> float:
    """Panel solar de día, consumo de noche. [SUPUESTO] el nodo duerme de noche."""
    h = t.astimezone(ZONA_AR).hour
    if 9 <= h <= 18:
        return min(4.15, v + 0.025 * rng.uniform(0.3, 1.0))
    return max(3.30, v - 0.012)


# ---------------------------------------------------------------- cámara
HORA_PRIMERA_FOTO, HORA_ULTIMA_FOTO = 7, 20  # ESP32-CAM sin luz infrarroja: sólo fotografía de día


def es_de_dia(t: datetime) -> bool:
    return HORA_PRIMERA_FOTO <= t.astimezone(ZONA_AR).hour <= HORA_ULTIMA_FOTO


def conteo_imagen(plaga: Plaga, densidad: float, rng: random.Random) -> int:
    """Individuos que el modelo FOMO cuenta en UNA imagen (Poisson alrededor del valor esperado)."""
    return poisson(densidad * plaga.individuos_por_unidad, rng)


def densidad_desde_conteos(plaga: Plaga, conteos: list[int]) -> float:
    """Lo que hace la nube: promedio de conteos por imagen -> densidad en la unidad agronómica."""
    if not conteos:
        return 0.0
    return sum(conteos) / len(conteos) / plaga.individuos_por_unidad


def poisson(lam: float, rng: random.Random) -> int:
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    lim, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= lim:
            return k
        k += 1


def mapa_ocupacion(conteo: int, rng: random.Random) -> int:
    """Celdas de la imagen (grilla 4x4) donde el FOMO detectó centroides."""
    celdas = min(16, conteo)
    return sum(1 << i for i in rng.sample(range(16), celdas)) if celdas else 0


def ahora() -> datetime:
    return datetime.now(timezone.utc)
