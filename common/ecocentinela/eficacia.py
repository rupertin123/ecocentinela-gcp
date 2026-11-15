"""Eficacia residual de los insecticidas: cuánto protege una aplicación y cuándo el lote queda descubierto.

Modelo
------
La eficacia de control decae con el tiempo desde la aplicación (degradación del residuo, crecimiento
de hojas nuevas sin producto, llegada de inmigrantes):

    E(t) = E0 · exp(-k · t)          t en días desde la aplicación

* E0 = eficacia inicial (fracción de la población que el producto elimina al comienzo).
* k  = tasa de decaimiento por día; la vida media es ln(2)/k.
* El lote se considera DESCUBIERTO cuando E(t) cae por debajo de E_MIN = 50 %. [SUPUESTO configurable]
  Fecha de descubierto: t* = ln(E0 / E_MIN) / k.

Eficacia observada (lo que calcula BigQuery con los datos de los nodos)
-----------------------------------------------------------------------
No hay parcela testigo sin tratar, así que se construye un «testigo virtual»: la tendencia de la
población en las 72 h previas a la aplicación (regresión log-lineal), proyectada hacia adelante y
acotada por la capacidad del ambiente. Después, con la misma idea que la fórmula de Henderson-Tilton:

    E_obs(d) = 1 - N_observada(d) / N_testigo(d)

y se ajusta ln(E_obs) = ln(E0) - k·d por mínimos cuadrados, con los datos desde las 48 h (antes, la media
móvil de 24 h de cada nodo todavía mezcla fotos del período de derribe). Con 3 días o más de datos el ajuste
reemplaza al valor bibliográfico. El SQL equivalente está en sql/eficacia_residual.sql.

Productos (valores de partida «a priori»)
-----------------------------------------
[A] Perotti, Gamundi & Russo (2010). Control de Piezodorus guildinii en el cultivo de soja. Para mejorar
    la producción 45, INTA EEA Oliveros. Eficacia sobre adultos (Henderson-Tilton) de
    tiametoxam + lambdacialotrina: 89, 84, 38, 82 y 58 % a los 2, 3, 6, 10 y 14 días. El ajuste
    exponencial por mínimos cuadrados de esos 5 puntos da E0 = 0,78 y k = 0,0216/día.
[B] Clorantraniliprol: residuos en el cultivo ~21 días (Astor et al., 2009, citado en Rev. Colomb.
    Entomol.); el fabricante declara 7-14 días de persistencia. [SUPUESTO] E0 = 0,95, vida media 14 d.
[C] Spinetoram + metoxifenocide sobre C. phaseoli: «eficacia superior y consistente hasta los 14 DDA»
    en el centro-sur de Córdoba (Ab Intus, UNRC). [SUPUESTO] E0 = 0,90, vida media 20 d.

Los productos se nombran por principio activo: el sistema NO recomienda marcas ni dosis; la decisión
y la receta son del ingeniero agrónomo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

E_MIN = 0.50  # [SUPUESTO] por debajo de esta eficacia residual el lote se considera descubierto
DIAS_MINIMOS_AJUSTE = 3


@dataclass(frozen=True)
class Producto:
    codigo: str
    plaga: str
    principio_activo: str
    e0: float          # eficacia inicial a priori
    k: float           # decaimiento a priori (1/día)
    fuente: str
    # Lo que «pasa de verdad» en el lote simulado. Es distinto del valor bibliográfico a propósito:
    # así se ve que el análisis en BigQuery aprende del campo y corrige la estimación inicial.
    e0_campo: float
    k_campo: float

    @property
    def vida_media(self) -> float:
        return math.log(2) / self.k


PRODUCTOS: dict[str, Producto] = {
    "clorantraniliprol": Producto("clorantraniliprol", "rachiplusia", "clorantraniliprol",
                                  0.95, math.log(2) / 14, "[B] Astor et al. 2009; persistencia de etiqueta 7-14 d",
                                  e0_campo=0.93, k_campo=math.log(2) / 10),
    "tiametoxam_lambdacialotrina": Producto("tiametoxam_lambdacialotrina", "piezodorus",
                                            "tiametoxam + lambdacialotrina", 0.78, 0.0216,
                                            "[A] Perotti, Gamundi & Russo 2010, INTA Oliveros (ajuste a adultos)",
                                            e0_campo=0.85, k_campo=0.035),
    "spinetoram_metoxifenocide": Producto("spinetoram_metoxifenocide", "caliothrips",
                                          "spinetoram + metoxifenocide", 0.90, math.log(2) / 20,
                                          "[C] Ab Intus (UNRC): consistente hasta 14 DDA",
                                          e0_campo=0.88, k_campo=math.log(2) / 16),
}
PRODUCTO_DE_PLAGA = {p.plaga: p for p in PRODUCTOS.values()}


def eficacia(e0: float, k: float, dias: float) -> float:
    return 0.0 if dias < 0 else e0 * math.exp(-k * dias)


def dias_hasta_descubierto(e0: float, k: float, e_min: float = E_MIN) -> float:
    if e0 <= e_min:
        return 0.0
    if k <= 0:
        return math.inf
    return math.log(e0 / e_min) / k


def fecha_descubierto(aplicada: datetime, e0: float, k: float) -> datetime | None:
    d = dias_hasta_descubierto(e0, k)
    return None if math.isinf(d) else aplicada + timedelta(days=d)


def eficacia_en_campo(aplicaciones: list[dict], plaga: str, t: datetime) -> float:
    """Fracción de la población eliminada en `t` por TODAS las aplicaciones vigentes sobre la plaga.

    Es lo que usa el simulador («la realidad» del lote). Varias aplicaciones se combinan como
    probabilidades independientes de sobrevivir.
    """
    sobrevive = 1.0
    for a in aplicaciones:
        p = PRODUCTOS.get(a["producto"])
        if not p or p.plaga != plaga:
            continue
        dias = (t - a["aplicada_en"]).total_seconds() / 86400
        if dias < 0:
            continue
        # El efecto de derribe no es instantáneo: llega a E0 en ~24 h.
        rampa = min(1.0, dias / 1.0)
        sobrevive *= 1 - eficacia(p.e0_campo, p.k_campo, dias) * rampa
    return 1 - sobrevive


def ajustar(puntos: list[tuple[float, float]]) -> dict | None:
    """Ajuste por mínimos cuadrados de ln(E) = ln(E0) - k·d. `puntos` = [(días, eficacia observada)].

    Es la versión en Python del cálculo de sql/eficacia_residual.sql (las pruebas comparan ambos).
    """
    pts = [(d, e) for d, e in puntos if 0.02 < e < 0.995]
    if len({round(d) for d, _ in pts}) < DIAS_MINIMOS_AJUSTE:
        return None
    n = len(pts)
    xs, ys = [d for d, _ in pts], [math.log(e) for _, e in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    pendiente = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    k = -pendiente
    e0 = math.exp(my - pendiente * mx)
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (pendiente ** 2 * sxx / syy) if syy else 1.0
    return {"e0": min(e0, 1.0), "k": k, "r2": r2, "n": n}
