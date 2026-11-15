"""API de ingesta de EcoCentinela (Cloud Run).

Recibe los eventos «up» que enviaría la integración HTTP de ChirpStack v4,
decodifica la carga binaria de 10 bytes (el conteo que hizo la cámara en el borde),
valida rangos (calidad de datos), estima la densidad de la plaga con las fotos de las
últimas 24 h, publica la fila en Pub/Sub (que la escribe sola en BigQuery) y actualiza
el último estado del nodo en Firestore para el tablero.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ecocentinela import carga_util, modelo

PROYECTO = os.environ.get("GOOGLE_CLOUD_PROJECT", "local")
TOPICO = os.environ.get("TOPICO_TELEMETRIA", "telemetria")
TOPICO_RECHAZOS = os.environ.get("TOPICO_RECHAZOS", "telemetria-rechazada")
MODO_LOCAL = os.environ.get("MODO_LOCAL") == "1"
CATALOGO = {n.id: n for n in modelo.NODOS}
INTERVALO_NOMINAL_MIN = 60.0  # una foto por hora, de día
FOTOS_MINIMAS = 6             # fotos válidas en 24 h para confiar en la estimación


class _JsonLog(logging.Formatter):
    """Cloud Logging interpreta cada línea JSON de stdout como un registro estructurado."""

    def format(self, r: logging.LogRecord) -> str:
        d = {"severity": r.levelname, "message": r.getMessage(), "componente": "ingesta"}
        d.update(getattr(r, "extra_json", {}))
        return json.dumps(d, ensure_ascii=False)


_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(_JsonLog())
log = logging.getLogger("ingesta")
log.handlers, log.propagate = [_h], False
log.setLevel(logging.INFO)

app = FastAPI(title="EcoCentinela - Ingesta", version="3.0.0",
              description="Recibe telemetría LoRaWAN (formato ChirpStack v4).")


# --------------------------------------------------------------------- clientes
class _Destinos:
    """Clientes de Google Cloud creados una sola vez (o falsos en modo local)."""

    def __init__(self) -> None:
        self.publicados: list[tuple[str, dict]] = []
        self.estados: dict[str, dict] = {}
        if MODO_LOCAL:
            return
        from google.cloud import firestore, pubsub_v1

        self.pub = pubsub_v1.PublisherClient()
        self.fs = firestore.Client(project=PROYECTO)

    def publicar(self, topico: str, fila: dict, atributos: dict) -> None:
        if MODO_LOCAL:
            self.publicados.append((topico, fila))
            return
        ruta = self.pub.topic_path(PROYECTO, topico)
        self.pub.publish(ruta, json.dumps(fila).encode(), **atributos).result(timeout=30)

    def estado(self, nodo: str) -> dict | None:
        if MODO_LOCAL:
            return self.estados.get(nodo)
        doc = self.fs.collection("estado_nodos").document(nodo).get()
        return doc.to_dict() if doc.exists else None

    def guardar_estado(self, nodo: str, datos: dict) -> None:
        if MODO_LOCAL:
            self.estados[nodo] = {**self.estados.get(nodo, {}), **datos}
            return
        self.fs.collection("estado_nodos").document(nodo).set(datos, merge=True)


_destinos: _Destinos | None = None


def destinos() -> _Destinos:
    global _destinos
    if _destinos is None:
        _destinos = _Destinos()
    return _destinos


# --------------------------------------------------------------------- modelos
class DeviceInfo(BaseModel):
    deviceName: str
    devEui: str


class RxInfo(BaseModel):
    rssi: int = 0
    snr: float = 0.0


class EventoUp(BaseModel):
    deduplicationId: str
    time: datetime
    deviceInfo: DeviceInfo
    fCnt: int = Field(ge=0)
    fPort: int
    dr: int | None = None
    data: str
    rxInfo: list[RxInfo] = []
    txInfo: dict = {}


# Rangos físicamente posibles; lo que cae afuera es un dato corrupto.
RANGOS = {
    "temperatura_c": (-15.0, 55.0),
    "humedad_rel_pct": (0.0, 100.0),
    "bateria_v": (2.8, 4.4),
    "confianza": (0.0, 1.0),
    "conteo": (0, 2000),
}
BANDERAS_DESCARTE = carga_util.BANDERA_ERROR_CAMARA | carga_util.BANDERA_LENTE_OBSTRUIDA


def _rechazar(motivo: str, evento: dict) -> None:
    log.warning("evento rechazado", extra={"extra_json": {"motivo": motivo}})
    destinos().publicar(TOPICO_RECHAZOS, {"motivo": motivo, "evento": json.dumps(evento, default=str)[:4000],
                                          "recibido": datetime.now(timezone.utc).isoformat()}, {"motivo": motivo[:100]})


@app.get("/salud")
def salud() -> dict:
    return {"estado": "ok"}


@app.post("/v1/uplink", status_code=202)
def recibir(evento: EventoUp, event: str = Query("up")) -> dict:
    if event != "up":
        return {"ignorado": event}  # join, status, ack... no interesan acá
    nodo_id = evento.deviceInfo.deviceName
    nodo = CATALOGO.get(nodo_id)
    es_prueba = nodo_id.startswith("PRUEBA-CARGA-")
    if es_prueba:  # nodos virtuales de la prueba de carga: se procesan igual pero no tocan el tablero
        nodo = modelo.Nodo(nodo_id, "PRUEBA", "rachiplusia", 0.0, 0.0)
    if nodo is None:
        _rechazar("nodo desconocido", evento.model_dump())
        raise HTTPException(404, "nodo no registrado")
    if evento.fPort != 1:
        _rechazar(f"puerto {evento.fPort} no soportado", evento.model_dump())
        raise HTTPException(422, "puerto LoRaWAN no soportado")
    try:
        crudo = base64.b64decode(evento.data, validate=True)
        lectura = carga_util.decodificar(crudo)
    except (ValueError, carga_util.CargaInvalida) as e:
        _rechazar(f"carga inválida: {e}", evento.model_dump())
        raise HTTPException(422, f"carga inválida: {e}")

    valores = {
        "temperatura_c": lectura.temperatura_c, "humedad_rel_pct": lectura.humedad_rel_pct,
        "bateria_v": lectura.bateria_v, "confianza": lectura.confianza, "conteo": lectura.conteo,
    }
    for campo, (lo, hi) in RANGOS.items():
        if not lo <= valores[campo] <= hi:
            _rechazar(f"{campo} fuera de rango: {valores[campo]}", evento.model_dump())
            raise HTTPException(422, f"{campo} fuera de rango")

    plaga = modelo.PLAGAS[nodo.plaga]
    previo = {} if es_prueba else (destinos().estado(nodo_id) or {})
    intervalo = INTERVALO_NOMINAL_MIN
    if previo.get("medido"):
        anterior = datetime.fromisoformat(previo["medido"])
        intervalo = (evento.time - anterior).total_seconds() / 60
        if previo.get("fcnt") is not None and evento.fCnt <= previo["fcnt"]:
            # Reintento o duplicado: Pub/Sub es «al menos una vez» y ChirpStack puede reenviar.
            log.info("duplicado ignorado", extra={"extra_json": {"nodo": nodo_id, "fcnt": evento.fCnt}})
            return {"duplicado": True}
    intervalo = min(max(intervalo, 5.0), 1440.0)

    # Una sola foto tiene pocos individuos (0, 1, 2...): estimar con ella sería puro ruido.
    # Se promedian las fotos válidas de las últimas 24 h (de día: hasta 14 fotos por nodo).
    medido_iso = evento.time.astimezone(timezone.utc).isoformat()
    corte = evento.time.timestamp() - 86400
    valida = not (lectura.banderas & BANDERAS_DESCARTE)
    ventana = [v for v in (previo.get("ventana") or []) if datetime.fromisoformat(v[0]).timestamp() > corte]
    if valida:
        ventana.append([medido_iso, lectura.conteo])
    densidad = round(modelo.densidad_desde_conteos(plaga, [v[1] for v in ventana]), 2)
    umbral = modelo.umbral(plaga.codigo, evento.time)
    rx = evento.rxInfo[0] if evento.rxInfo else RxInfo()
    lora = evento.txInfo.get("modulation", {}).get("lora", {})

    fila = {
        "evento_id": evento.deduplicationId,
        "nodo_id": nodo_id,
        "punto": nodo.punto,
        "estrato": plaga.estrato,
        "plaga": plaga.codigo,
        "altura_cm": plaga.altura_cm,
        "dev_eui": evento.deviceInfo.devEui,
        "fcnt": evento.fCnt,
        "medido": medido_iso,
        "recibido": datetime.now(timezone.utc).isoformat(),
        "conteo_imagen": lectura.conteo,
        "imagen_valida": valida,
        "intervalo_min": round(intervalo, 2),
        "densidad_est": densidad,
        "unidad": plaga.unidad,
        "umbral": umbral,
        "fotos_ventana": len(ventana),
        # Con menos de 6 fotos en la ventana la estimación es provisoria y no dispara recomendaciones.
        "supera_umbral": len(ventana) >= FOTOS_MINIMAS and densidad >= umbral,
        "estadio": modelo.estadio(evento.time),
        "confianza": lectura.confianza,
        "temperatura_c": lectura.temperatura_c,
        "humedad_rel_pct": lectura.humedad_rel_pct,
        "bateria_v": lectura.bateria_v,
        "mapa_ocupacion": lectura.mapa_ocupacion,
        "banderas": lectura.banderas,
        "rssi": rx.rssi,
        "snr": rx.snr,
        "frecuencia_hz": evento.txInfo.get("frequency"),
        "sf": lora.get("spreadingFactor"),
        "bytes_carga": len(crudo),
        "carga_hex": crudo.hex(),
    }
    destinos().publicar(TOPICO, fila, {"nodo_id": nodo_id, "evento_id": evento.deduplicationId})

    if es_prueba:
        return {"aceptado": True, "densidad_est": densidad}
    bitacora = (previo.get("bitacora") or [])[-19:]
    nivel = "CRITICAL" if fila["supera_umbral"] else ("WARN" if not valida else "INFO")
    aviso = " Above action threshold." if fila["supera_umbral"] else (
        " Image discarded (camera/lens flag)." if not valida else
        (" Provisional (< 6 photos in 24 h)." if len(ventana) < FOTOS_MINIMAS else ""))
    bitacora.append(f"[{nivel} {evento.time.astimezone(modelo.ZONA_AR):%d/%m %H:%M}] "
                    f"{lectura.conteo} {plaga.cientifico} in photo -> {densidad:.2f} {plaga.unidad} "
                    f"(24 h mean, conf {lectura.confianza:.2f}, {len(crudo)} B, RSSI {rx.rssi} dBm).{aviso}")
    destinos().guardar_estado(nodo_id, {
        **{k: fila[k] for k in ("medido", "fcnt", "densidad_est", "supera_umbral", "temperatura_c", "umbral",
                                "humedad_rel_pct", "bateria_v", "mapa_ocupacion", "rssi", "snr", "confianza",
                                "fotos_ventana", "conteo_imagen", "estadio")},
        "punto": nodo.punto, "estrato": plaga.estrato, "plaga": plaga.codigo, "lat": nodo.lat, "lon": nodo.lon,
        "bitacora": bitacora, "ventana": ventana,
    })
    log.info("uplink aceptado", extra={"extra_json": {"nodo": nodo_id, "fcnt": evento.fCnt,
                                                      "densidad": densidad, "rssi": rx.rssi}})
    return {"aceptado": True, "densidad_est": densidad, "supera_umbral": fila["supera_umbral"]}


@app.exception_handler(Exception)
async def _error(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse

    log.error(f"error no controlado: {exc!r}", extra={"extra_json": {"ruta": request.url.path}})
    return JSONResponse({"error": "interno"}, status_code=500)
