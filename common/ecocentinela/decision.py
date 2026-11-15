"""Recomendación de aplicación por estrato (la usan el tablero, el agente y las pruebas).

Regla de decisión, para cada estrato (= una plaga, 4 cámaras a la misma altura):

1. Densidad del estrato = promedio de los nodos con dato reciente (≤ 3 h de día). Hacen falta al menos
   3 de los 4 nodos y cada uno con ≥ 6 fotos en su ventana de 24 h; si no, «datos insuficientes».
2. Protección vigente = la última aplicación sobre esa plaga todavía tiene eficacia residual ≥ E_MIN.
   La eficacia usa el ajuste de BigQuery si ya existe (≥ 3 días de datos) o el valor bibliográfico.
3. Estados posibles:
   - RECOMENDAR       densidad ≥ umbral y sin protección vigente.
   - ESPERAR_EFECTO   hubo aplicación hace menos de 72 h: el producto todavía está actuando.
   - REVISAR_CONTROL  densidad ≥ umbral pese a tener protección vigente (posible falla de control).
   - PROTEGIDO        densidad < umbral y protección vigente (informa la fecha estimada de descubierto).
   - VIGILAR          densidad < umbral, sin protección, pero ≥ 70 % del umbral.
   - SIN_ACCION       densidad < 70 % del umbral y sin protección.
El sistema RECOMIENDA; quien decide y aplica es el productor, que después registra la aplicación.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ecocentinela import eficacia, modelo

HORAS_DATO_RECIENTE = 3
NODOS_MINIMOS = 3
FOTOS_MINIMAS = 6
HORAS_EFECTO = 72


def _dt(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def densidad_estrato(estrato: str, estados: dict, t: datetime) -> dict:
    """Promedio de los nodos del estrato con dato reciente y suficiente."""
    valores, considerados = [], []
    for n in modelo.nodos_de(estrato):
        e = estados.get(n.id) or {}
        medido = _dt(e.get("medido"))
        if medido is None or e.get("densidad_est") is None:
            continue
        # De noche los nodos duermen: se acepta el último dato del día (hasta 14 h).
        limite = HORAS_DATO_RECIENTE if modelo.es_de_dia(t) else 14
        if t - medido > timedelta(hours=limite) or (e.get("fotos_ventana") or 0) < FOTOS_MINIMAS:
            continue
        valores.append(float(e["densidad_est"]))
        considerados.append(n.id)
    densidad = round(sum(valores) / len(valores), 2) if valores else None
    return {"densidad": densidad, "nodos": considerados, "suficiente": len(valores) >= NODOS_MINIMOS}


def parametros(apl: dict, ajustes: dict) -> dict:
    """E0 y k de una aplicación: el ajuste de BigQuery si existe; si no, el valor de la bibliografía."""
    p = eficacia.PRODUCTOS[apl["producto"]]
    a = ajustes.get(apl["id"])
    if a and a.get("e0") is not None and a.get("k") and a["k"] > 0:
        return {"e0": a["e0"], "k": a["k"], "origen": "bigquery", "r2": a.get("r2"), "dias": a.get("dias")}
    return {"e0": p.e0, "k": p.k, "origen": "bibliografia", "r2": None, "dias": None}


def evaluar(estrato: str, estados: dict, aplicaciones: list[dict], ajustes: dict, t: datetime) -> dict:
    plaga = modelo.POR_ESTRATO[estrato]
    umbral = modelo.umbral(plaga.codigo, t)
    d = densidad_estrato(estrato, estados, t)
    propias = sorted((a for a in aplicaciones if eficacia.PRODUCTOS.get(a.get("producto"))
                      and eficacia.PRODUCTOS[a["producto"]].plaga == plaga.codigo),
                     key=lambda a: _dt(a["aplicada_en"]))
    ultima = propias[-1] if propias else None
    proteccion = None
    if ultima:
        aplicada = _dt(ultima["aplicada_en"])
        par = parametros(ultima, ajustes)
        dias = (t - aplicada).total_seconds() / 86400
        e_actual = eficacia.eficacia(par["e0"], par["k"], dias)
        descubierto = eficacia.fecha_descubierto(aplicada, par["e0"], par["k"])
        proteccion = {
            "aplicacion_id": ultima["id"], "producto": ultima["producto"],
            "principio_activo": eficacia.PRODUCTOS[ultima["producto"]].principio_activo,
            "aplicada_en": aplicada.isoformat(), "dias": round(dias, 1),
            "eficacia_actual": round(e_actual, 3), "vigente": e_actual >= eficacia.E_MIN,
            "fecha_descubierto": descubierto.isoformat() if descubierto else None,
            "origen": par["origen"], "e0": round(par["e0"], 3), "k": round(par["k"], 4),
            "vida_media_dias": round(0.693147 / par["k"], 1) if par["k"] > 0 else None,
            "r2": par["r2"], "dias_ajuste": par["dias"],
        }

    dens = d["densidad"]
    if dens is None or not d["suficiente"]:
        estado = "DATOS_INSUFICIENTES"
    elif proteccion and proteccion["dias"] * 24 < HORAS_EFECTO:
        estado = "ESPERAR_EFECTO"
    elif dens >= umbral:
        estado = "REVISAR_CONTROL" if proteccion and proteccion["vigente"] else "RECOMENDAR"
    elif proteccion and proteccion["vigente"]:
        estado = "PROTEGIDO"
    elif dens >= 0.7 * umbral:
        estado = "VIGILAR"
    else:
        estado = "SIN_ACCION"

    return {
        "estrato": estrato, "plaga": plaga.codigo, "nombre": plaga.nombre, "cientifico": plaga.cientifico,
        "unidad": plaga.unidad, "altura_cm": plaga.altura_cm, "umbral": umbral,
        "fuente_umbral": modelo.fuente_umbral(plaga.codigo), "densidad": dens, "nodos": d["nodos"],
        "estado": estado, "proteccion": proteccion,
        "producto_sugerido": eficacia.PRODUCTO_DE_PLAGA[plaga.codigo].principio_activo,
        "estadio": modelo.estadio(t),
    }


def evaluar_todo(estados: dict, aplicaciones: list[dict], ajustes: dict, t: datetime) -> list[dict]:
    return [evaluar(e, estados, aplicaciones, ajustes, t) for e in modelo.ESTRATOS]
