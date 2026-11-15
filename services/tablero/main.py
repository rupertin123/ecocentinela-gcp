"""Tablero de EcoCentinela (Cloud Run): FastAPI + WebSocket.

Lee el último estado de cada nodo y las aplicaciones registradas desde Firestore; la historia, el
pronóstico (BigQuery ML) y el análisis de eficacia residual desde BigQuery; y empuja actualizaciones
al navegador por WebSocket.

Seguridad (el tablero es público):
* Todo lo que se puede LEER es público y no contiene datos personales.
* Lo único que ESCRIBE (registrar que se aplicó un producto) exige iniciar sesión con Google: el
  servidor verifica la firma del token de identidad de Google, la audiencia (nuestro ID de cliente
  OAuth) y que el correo esté en la lista de cuentas autorizadas, guardada en Secret Manager.
* Cabeceras de seguridad (CSP, nosniff, etc.) y verificación del origen en el WebSocket.

Decisiones de costo (todo debe entrar en el nivel gratuito):
* Una sola caché en memoria compartida por todos los visitantes: Firestore se lee como máximo una
  vez cada 30 s y BigQuery una vez cada 10 min.
* Cada sesión de WebSocket se corta a los 10 min: en Cloud Run una conexión abierta mantiene la
  instancia facturando aunque nadie mire la pestaña.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ecocentinela import decision, eficacia, modelo

PROYECTO = os.environ.get("GOOGLE_CLOUD_PROJECT", "local")
DATASET = os.environ.get("DATASET", "ecocentinela")
MODO_LOCAL = os.environ.get("MODO_LOCAL") == "1"
SESION_MAX_S = int(os.environ.get("SESION_MAX_S", "600"))
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "")
ADMINS = {c.strip().lower() for c in os.environ.get("ADMIN_EMAILS", "").split(",") if c.strip()}
TOPICO_APLICACIONES = os.environ.get("TOPICO_APLICACIONES", "aplicaciones")
ESTATICO = Path(__file__).parent / "static"

app = FastAPI(title="EcoCentinela - Tablero", version="3.0.0", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ESTATICO), name="static")

CSP = ("default-src 'self'; "
       "script-src 'self' https://cdnjs.cloudflare.com https://accounts.google.com/gsi/client; "
       "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; "
       "frame-src https://accounts.google.com/gsi/; "
       "connect-src 'self' https://accounts.google.com/gsi/; "
       "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def cabeceras_seguridad(request: Request, llamar):
    r = await llamar(request)
    r.headers["Content-Security-Policy"] = CSP
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    r.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"  # la ventana de Google Sign-In
    r.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    r.headers["Strict-Transport-Security"] = "max-age=31536000"
    return r


_cache: dict[str, tuple[float, object]] = {}


def _cacheado(clave: str, ttl: float, fn):
    t, v = _cache.get(clave, (0.0, None))
    if time.time() - t < ttl:
        return v
    v = fn()
    _cache[clave] = (time.time(), v)
    return v


_fs = _bq = _pub = None


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


def pub():
    global _pub
    if _pub is None:
        from google.cloud import pubsub_v1

        _pub = pubsub_v1.PublisherClient()
    return _pub


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


# ------------------------------------------------------------------ lecturas
DATOS_LOCALES: dict = {"estados": {}, "aplicaciones": [], "ajustes": {}}  # lo usan las pruebas


def estados() -> dict:
    def leer():
        if MODO_LOCAL:
            return DATOS_LOCALES["estados"]
        return {d.id: d.to_dict() for d in fs().collection("estado_nodos").stream()}

    return _cacheado("estados", 30, leer)


def aplicaciones() -> list[dict]:
    """Aplicaciones de la campaña simulada actual. Nunca se devuelve quién la registró."""
    def leer():
        if MODO_LOCAL:
            return DATOS_LOCALES["aplicaciones"]
        ciclo = modelo.ciclo(modelo.ahora())
        return [{k: v for k, v in d.to_dict().items() if k != "registrada_por"}
                for d in fs().collection("aplicaciones").where("ciclo", "==", ciclo).stream()]

    return _cacheado("aplicaciones", 30, leer)


def _consulta(sql: str, **params) -> list[dict]:
    from google.cloud import bigquery

    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()])
    filas = bq().query(sql.format(p=PROYECTO, d=DATASET), job_config=cfg).result()
    return [{k: _iso(v) for k, v in dict(f).items()} for f in filas]


SQL_AJUSTES = """
SELECT aplicacion_id, e0_campo AS e0, k_campo AS k, r2, dias_con_datos AS dias, descubierto_campo
FROM `{p}.{d}.eficacia_residual`
"""


def ajustes() -> dict:
    """Ajuste de eficacia residual de cada aplicación (BigQuery)."""
    def leer():
        if MODO_LOCAL:
            return DATOS_LOCALES["ajustes"]
        try:
            return {f["aplicacion_id"]: f for f in _consulta(SQL_AJUSTES)}
        except Exception:  # la tabla de aplicaciones todavía está vacía
            return {}

    return _cacheado("ajustes", 600, leer)


SQL_HISTORIA = """
SELECT TIMESTAMP_TRUNC(medido, HOUR) AS hora, ROUND(AVG(densidad_est), 2) AS densidad,
       ROUND(MIN(densidad_est), 2) AS minimo, ROUND(MAX(densidad_est), 2) AS maximo,
       ANY_VALUE(umbral) AS umbral, COUNT(DISTINCT nodo_id) AS nodos
FROM `{p}.{d}.telemetria_limpia`
WHERE estrato = @estrato AND medido > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 DAY)
GROUP BY hora ORDER BY hora
"""
SQL_PRONOSTICO = """
SELECT hora, ROUND(densidad, 2) AS densidad, ROUND(limite_inferior, 2) AS inferior,
       ROUND(limite_superior, 2) AS superior
FROM `{p}.{d}.pronostico`
WHERE estrato = @estrato AND hora > CURRENT_TIMESTAMP()
ORDER BY hora LIMIT 72
"""
SQL_PUNTOS = """
SELECT CAST(FLOOR(dias) AS INT64) AS dia, ROUND(AVG(e_obs), 3) AS e_obs,
       ROUND(AVG(n_observada), 2) AS observada, ROUND(AVG(n_testigo), 2) AS testigo
FROM `{p}.{d}.eficacia_puntos`
WHERE aplicacion_id = @apl
GROUP BY dia ORDER BY dia
"""


def historia(estrato: str) -> dict:
    def leer():
        if MODO_LOCAL:
            return {"historia": [], "pronostico": []}
        try:
            pron = _consulta(SQL_PRONOSTICO, estrato=estrato)
        except Exception:  # el modelo todavía no se entrenó
            pron = []
        return {"historia": _consulta(SQL_HISTORIA, estrato=estrato), "pronostico": pron}

    return _cacheado(f"historia:{estrato}", 600, leer)


def puntos_eficacia(apl_id: str) -> list[dict]:
    def leer():
        if MODO_LOCAL:
            return []
        try:
            return _consulta(SQL_PUNTOS, apl=apl_id)
        except Exception:
            return []

    return _cacheado(f"puntos:{apl_id}", 600, leer)


def resumen() -> dict:
    t = modelo.ahora()
    return {
        "estratos": decision.evaluar_todo(estados(), aplicaciones(), ajustes(), t),
        "estadio": modelo.estadio(t), "dds": round(modelo.dds(t), 1),
        "fecha_agronomica": modelo.fecha_agronomica(t).isoformat(), "de_dia": modelo.es_de_dia(t),
        "servidor": t.isoformat(),
    }


def detalle(estrato: str) -> dict:
    """Todo lo que el tablero necesita para un estrato: nodos, historia, pronóstico y eficacia."""
    if estrato not in modelo.ESTRATOS:
        raise HTTPException(404, "estrato desconocido")
    e = estados()
    nodos = []
    for n in modelo.nodos_de(estrato):
        d = e.get(n.id, {})
        nodos.append({"id": n.id, "punto": n.punto, "altura_cm": n.altura_cm,
                      **{k: _iso(d.get(k)) for k in ("medido", "densidad_est", "conteo_imagen", "confianza",
                                                     "temperatura_c", "humedad_rel_pct", "bateria_v", "rssi",
                                                     "snr", "mapa_ocupacion", "fotos_ventana")},
                      "bitacora": (d.get("bitacora") or [])[-12:]})
    plaga = modelo.POR_ESTRATO[estrato]
    propias = sorted((a for a in aplicaciones() if a.get("plaga") == plaga.codigo),
                     key=lambda a: str(a["aplicada_en"]))
    curva = None
    if propias:
        a = propias[-1]
        p = eficacia.PRODUCTOS[a["producto"]]
        aj = ajustes().get(a["id"]) or {}
        curva = {"aplicacion_id": a["id"], "principio_activo": p.principio_activo,
                 "aplicada_en": _iso(a["aplicada_en"]), "fuente": p.fuente,
                 "bibliografia": {"e0": p.e0, "k": p.k}, "e_min": eficacia.E_MIN,
                 "campo": ({"e0": aj["e0"], "k": aj["k"], "r2": aj.get("r2"), "dias": aj.get("dias")}
                           if aj.get("e0") is not None and aj.get("k") else None),
                 "observada": puntos_eficacia(a["id"])}
    return {"estrato": estrato, "nodos": nodos, **historia(estrato), "eficacia": curva,
            "aplicaciones": [{"id": a["id"], "aplicada_en": _iso(a["aplicada_en"]), "producto": a["producto"],
                              "origen": a.get("origen")} for a in propias]}


# ------------------------------------------------------------------ rutas de lectura
@app.get("/")
def inicio():
    return FileResponse(ESTATICO / "index.html")


@app.get("/salud")
def salud():
    return {"estado": "ok"}


@app.get("/api/config")
def config():
    """Lo que el navegador necesita para el botón de Google (el ID de cliente OAuth es público)."""
    return {"oauth_client_id": OAUTH_CLIENT_ID or None, "registro_habilitado": bool(OAUTH_CLIENT_ID and ADMINS),
            "productos": [{"codigo": p.codigo, "plaga": p.plaga, "principio_activo": p.principio_activo}
                          for p in eficacia.PRODUCTOS.values()]}


@app.get("/api/resumen")
def api_resumen():
    return resumen()


@app.get("/api/estrato/{estrato}")
def api_estrato(estrato: str):
    return detalle(estrato)


# ------------------------------------------------------------------ registro de aplicaciones (autenticado)
def verificar_usuario(authorization: str | None) -> str:
    """Valida el token de identidad de Google y devuelve el correo si está autorizado."""
    if not (OAUTH_CLIENT_ID and ADMINS):
        raise HTTPException(503, "El registro de aplicaciones no está configurado")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Iniciá sesión con Google para registrar una aplicación")
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token

    try:
        datos = id_token.verify_oauth2_token(authorization[7:], greq.Request(), OAUTH_CLIENT_ID)
    except ValueError:
        raise HTTPException(401, "Sesión inválida o vencida: volvé a iniciar sesión")
    correo = str(datos.get("email", "")).lower()
    if not datos.get("email_verified") or correo not in ADMINS:
        raise HTTPException(403, "Esta cuenta no está autorizada a registrar aplicaciones")
    return correo


class PedidoAplicacion(BaseModel):
    estrato: str
    producto: str
    aplicada_en: datetime | None = None
    nota: str = Field(default="", max_length=200)


@app.post("/api/aplicaciones", status_code=201)
def registrar(p: PedidoAplicacion, authorization: str | None = Header(default=None)):
    """El productor avisa que aplicó: desde ese momento el sistema cuenta el decaimiento de la eficacia."""
    correo = verificar_usuario(authorization)
    if p.estrato not in modelo.ESTRATOS:
        raise HTTPException(422, "estrato desconocido")
    prod = eficacia.PRODUCTOS.get(p.producto)
    plaga = modelo.POR_ESTRATO[p.estrato]
    if prod is None or prod.plaga != plaga.codigo:
        raise HTTPException(422, f"ese producto no es para {plaga.nombre}")
    ahora = modelo.ahora()
    cuando = (p.aplicada_en or ahora).astimezone(timezone.utc)
    if not ahora - timedelta(days=7) <= cuando <= ahora + timedelta(minutes=5):
        raise HTTPException(422, "la fecha de aplicación debe estar dentro de los últimos 7 días")
    for a in aplicaciones():
        if a.get("plaga") == plaga.codigo and abs((datetime.fromisoformat(str(a["aplicada_en"])) - cuando)
                                                  .total_seconds()) < 86400:
            raise HTTPException(409, "ya hay una aplicación registrada para esa plaga en ese día")
    doc = {"id": f"apl-{uuid.uuid4().hex[:12]}", "estrato": p.estrato, "plaga": plaga.codigo,
           "producto": prod.codigo, "aplicada_en": cuando.isoformat(), "registrada": ahora.isoformat(),
           "origen": "tablero", "ciclo": modelo.ciclo(cuando), "nota": p.nota.strip()}
    if MODO_LOCAL:
        DATOS_LOCALES["aplicaciones"].append(doc)
    else:
        fs().collection("aplicaciones").document(doc["id"]).set({**doc, "registrada_por": correo})
        fila = {"aplicacion_id": doc["id"], "estrato": doc["estrato"], "plaga": doc["plaga"],
                "producto": doc["producto"], "principio_activo": prod.principio_activo,
                "aplicada_en": doc["aplicada_en"], "registrada": doc["registrada"], "origen": "tablero",
                "ciclo": doc["ciclo"], "e0_bibliografia": prod.e0, "k_bibliografia": prod.k}
        pub().publish(pub().topic_path(PROYECTO, TOPICO_APLICACIONES), json.dumps(fila).encode()).result(timeout=30)
    for clave in ("aplicaciones", "ajustes"):
        _cache.pop(clave, None)
    par = {"e0": prod.e0, "k": prod.k}
    descubierto = eficacia.fecha_descubierto(cuando, par["e0"], par["k"])
    return {"ok": True, "id": doc["id"], "descubierto_estimado": _iso(descubierto)}


# ------------------------------------------------------------------ agente
class PedidoAgente(BaseModel):
    pregunta: str = Field(min_length=3, max_length=300)
    estrato: str = "medio"


@app.post("/api/agente")
def agente(p: PedidoAgente):
    """Asistente agronómico (Gemini, capa gratuita) que consulta los datos con herramientas."""
    if p.estrato not in modelo.ESTRATOS:
        raise HTTPException(422, "estrato desconocido")
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(503, "Asistente no configurado")
    from agente import responder

    try:
        return responder(p.pregunta, p.estrato, resumen, detalle)
    except Exception as e:  # cuota de la capa gratuita agotada, red, etc.
        raise HTTPException(503, f"El asistente no está disponible ahora ({type(e).__name__}).")


# ------------------------------------------------------------------ WebSocket
@app.websocket("/ws/telemetria")
async def ws(sock: WebSocket):
    origen = sock.headers.get("origin", "")
    if origen and origen.split("://", 1)[-1] != sock.headers.get("host", ""):
        await sock.close(code=4403)  # otro sitio no puede abrir sesiones contra este tablero
        return
    await sock.accept()
    estrato = sock.query_params.get("estrato", "medio")
    inicio_s = time.time()
    try:
        while time.time() - inicio_s < SESION_MAX_S:
            if estrato in modelo.ESTRATOS:
                datos = await asyncio.to_thread(lambda: {"resumen": resumen(), "detalle": detalle(estrato)})
                await sock.send_text(json.dumps(datos, default=str))
            try:
                msg = await asyncio.wait_for(sock.receive_text(), timeout=15)
                estrato = str(json.loads(msg).get("estrato", estrato))[:20]
            except asyncio.TimeoutError:
                pass
        await sock.close(code=4000, reason="sesion-maxima")
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
