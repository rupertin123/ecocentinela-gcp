"""Tablero de EcoCentinela (Cloud Run): FastAPI + WebSocket.

Lee el último estado de cada nodo desde Firestore, la historia y el pronóstico
desde BigQuery, y empuja actualizaciones al navegador por WebSocket.

Decisiones de costo (todo debe entrar en el nivel gratuito):
* Una sola caché en memoria compartida por todos los visitantes: Firestore se
  lee como máximo una vez cada 30 s y BigQuery una vez cada 5 min por nodo.
* Cada sesión de WebSocket se corta a los 10 min: en Cloud Run una conexión
  abierta mantiene la instancia facturando aunque nadie mire la pestaña.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ecocentinela import modelo

PROYECTO = os.environ.get("GOOGLE_CLOUD_PROJECT", "local")
DATASET = os.environ.get("DATASET", "ecocentinela")
MODO_LOCAL = os.environ.get("MODO_LOCAL") == "1"
SESION_MAX_S = int(os.environ.get("SESION_MAX_S", "600"))
ESTATICO = Path(__file__).parent / "static"
ECON = modelo.ModeloEconomico()

app = FastAPI(title="EcoCentinela - Tablero", version="2.0.0")
app.mount("/static", StaticFiles(directory=ESTATICO), name="static")

_cache: dict[str, tuple[float, object]] = {}


def _cacheado(clave: str, ttl: float, fn):
    t, v = _cache.get(clave, (0.0, None))
    if time.time() - t < ttl:
        return v
    v = fn()
    _cache[clave] = (time.time(), v)
    return v


_fs = _bq = None


def fs():
    global _fs
    if _fs is None:
        from google.cloud import firestore

        _fs = firestore.Client(project=PROYECTO)
    return _fs


def bq():
    global _bq
    if _bq is None:
        from google.cloud import bigquery

        _bq = bigquery.Client(project=PROYECTO)
    return _bq


# ------------------------------------------------------------------ lecturas
def _leer_estados() -> dict:
    if MODO_LOCAL:
        return {}
    return {d.id: d.to_dict() for d in fs().collection("estado_nodos").stream()}


def estados() -> dict:
    return _cacheado("estados", 30, _leer_estados)


def _leer_ahorro() -> dict:
    if MODO_LOCAL:
        return {"total_usd": 0.0, "intervenciones": 0}
    docs = [d.to_dict() for d in fs().collection("mitigaciones").stream()]
    return {"total_usd": round(sum(d.get("perdida_evitada_usd", 0) for d in docs), 2), "intervenciones": len(docs)}


def ahorro() -> dict:
    return _cacheado("ahorro", 30, _leer_ahorro)


SQL_HISTORIA = f"""
SELECT TIMESTAMP_TRUNC(medido, HOUR) AS hora,
       ROUND(AVG(adultos_hoja_est), 2) AS adultos_hoja,
       ROUND(AVG(temperatura_c), 1) AS temperatura
FROM `{{p}}.{DATASET}.telemetria_limpia`
WHERE nodo_id = @nodo AND medido > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
GROUP BY hora ORDER BY hora
"""
SQL_PRONOSTICO = f"""
SELECT hora, ROUND(adultos_hoja, 2) AS adultos_hoja, ROUND(limite_inferior, 2) AS inferior,
       ROUND(limite_superior, 2) AS superior
FROM `{{p}}.{DATASET}.pronostico`
WHERE nodo_id = @nodo AND hora > CURRENT_TIMESTAMP()
ORDER BY hora LIMIT 48
"""


def _consulta(sql: str, nodo: str) -> list[dict]:
    from google.cloud import bigquery

    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("nodo", "STRING", nodo)])
    filas = bq().query(sql.format(p=PROYECTO), job_config=cfg).result()
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(f).items()} for f in filas]


def historia(nodo: str) -> dict:
    def leer():
        if MODO_LOCAL:
            return {"historia": [], "pronostico": []}
        try:
            pron = _consulta(SQL_PRONOSTICO, nodo)
        except Exception:  # el modelo todavía no se entrenó
            pron = []
        return {"historia": _consulta(SQL_HISTORIA, nodo), "pronostico": pron}

    return _cacheado(f"historia:{nodo}", 300, leer)


def _validar(nodo: str) -> modelo.Nodo:
    n = next((n for n in modelo.NODOS if n.id == nodo), None)
    if n is None:
        raise HTTPException(404, "nodo desconocido")
    return n


def foto(nodo: str) -> dict:
    """Todo lo que el tablero necesita para un nodo, en un solo mensaje."""
    e = estados().get(nodo, {})
    pron = historia(nodo)["pronostico"]
    cruce = next((p["hora"] for p in pron if p["adultos_hoja"] >= modelo.UMBRAL_ACCION), None)
    return {
        "nodo": nodo, "estado": e, "ahorro": ahorro(), "umbral": modelo.UMBRAL_ACCION,
        "cruce_pronosticado": cruce, "perdida_evitada_por_intervencion": ECON.perdida_evitada(),
        "servidor": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ rutas
@app.get("/")
def inicio():
    return FileResponse(ESTATICO / "index.html")


@app.get("/salud")
def salud():
    return {"estado": "ok"}


@app.get("/api/nodos")
def nodos():
    e = estados()
    return [{"id": n.id, "invernadero": n.invernadero, "cultivo": n.cultivo,
             "supera_umbral": e.get(n.id, {}).get("supera_umbral", False)} for n in modelo.NODOS]


@app.get("/api/foto/{nodo}")
def api_foto(nodo: str):
    _validar(nodo)
    return foto(nodo)


@app.get("/api/historia/{nodo}")
def api_historia(nodo: str):
    _validar(nodo)
    return historia(nodo)


class PedidoMitigacion(BaseModel):
    nodo: str


@app.post("/api/mitigacion")
def mitigar(p: PedidoMitigacion):
    """Deja una orden de intervención para todo el invernadero (el «downlink» simulado)."""
    n = _validar(p.nodo)
    if MODO_LOCAL:
        return {"ok": True, "perdida_evitada_usd": ECON.perdida_evitada()}
    from google.cloud import firestore

    ref = fs().collection("bloqueos").document(n.invernadero.replace(" ", "_"))
    ultimo = ref.get()
    if ultimo.exists and ultimo.to_dict()["hasta"] > datetime.now(timezone.utc):
        raise HTTPException(429, "Este invernadero ya tiene una intervención en curso (se puede pedir una cada 48 h).")
    estado = estados().get(n.id, {})
    if not estado.get("supera_umbral"):
        raise HTTPException(409, "La densidad está por debajo del umbral de acción: no se recomienda intervenir.")
    ahora = datetime.now(timezone.utc)
    ref.set({"hasta": ahora + timedelta(hours=48)})
    for m in modelo.NODOS:
        if m.invernadero == n.invernadero:
            fs().collection("comandos").document(m.id).set({"pendiente": True, "pedido": ahora.isoformat()})
    fs().collection("mitigaciones").add({"invernadero": n.invernadero, "cuando": ahora.isoformat(),
                                         "origen": "tablero", "perdida_evitada_usd": ECON.perdida_evitada(),
                                         "registrado": firestore.SERVER_TIMESTAMP})
    _cache.pop("ahorro", None)
    return {"ok": True, "perdida_evitada_usd": ECON.perdida_evitada()}


class PedidoAgente(BaseModel):
    nodo: str
    pregunta: str


@app.post("/api/agente")
def agente(p: PedidoAgente):
    """Asistente agronómico (Gemini, capa gratuita) que consulta los datos con herramientas."""
    _validar(p.nodo)
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(503, "Asistente no configurado")
    if not 3 <= len(p.pregunta) <= 300:
        raise HTTPException(422, "La pregunta debe tener entre 3 y 300 caracteres")
    from agente import responder

    try:
        return responder(p.pregunta, p.nodo, estados, historia)
    except Exception as e:  # cuota de la capa gratuita agotada, red, etc.
        raise HTTPException(503, f"El asistente no está disponible ahora ({type(e).__name__}).")


@app.websocket("/ws/telemetria")
async def ws(sock: WebSocket):
    await sock.accept()
    nodo = sock.query_params.get("nodo", modelo.NODOS[0].id)
    inicio_s = time.time()
    try:
        while time.time() - inicio_s < SESION_MAX_S:
            datos = await asyncio.to_thread(foto, nodo)
            await sock.send_text(json.dumps(datos, default=str))
            try:
                msg = await asyncio.wait_for(sock.receive_text(), timeout=10)
                nodo = json.loads(msg).get("nodo", nodo)
            except asyncio.TimeoutError:
                pass
        await sock.close(code=4000, reason="sesion-maxima")
    except WebSocketDisconnect:
        pass
