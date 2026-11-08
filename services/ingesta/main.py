"""API de ingesta de EcoCentinela (Cloud Run).

Recibe los eventos «up» que enviaría la integración HTTP de ChirpStack v4,
decodifica la carga binaria de 10 bytes, valida rangos (calidad de datos),
publica la fila en Pub/Sub (que la escribe sola en BigQuery) y actualiza el
último estado del nodo en Firestore para el tablero.
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
INTERVALO_NOMINAL_MIN = 15.0
HORAS_MINIMAS = 12.0  # ventana mínima de trampa para confiar en la estimación


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

app = FastAPI(title="EcoCentinela - Ingesta", version="2.0.0",
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
    "temperatura_c": (-10.0, 60.0),
    "humedad_suelo_pct": (0.0, 100.0),
    "bateria_v": (2.8, 4.4),
    "confianza": (0.0, 1.0),
    "adultos_trampa": (0, 5000),
}


def _rechazar(motivo: str, evento: dict) -> None:
    log.warning("evento rechazado", extra={"extra_json": {"motivo": motivo}})
    destinos().publicar(TOPICO_RECHAZOS, {"motivo": motivo, "evento": json.dumps(evento, default=str)[:4000],
                                          "recibido": datetime.now(timezone.utc).isoformat()}, {"motivo": motivo[:100]})


@app.get("/salud")
def salud() -> dict:
    return {"estado": "ok", "proyecto": PROYECTO}


@app.post("/v1/uplink", status_code=202)
def recibir(evento: EventoUp, event: str = Query("up")) -> dict:
    if event != "up":
        return {"ignorado": event}  # join, status, ack... no interesan acá
    nodo_id = evento.deviceInfo.deviceName
    nodo = CATALOGO.get(nodo_id)
    es_prueba = nodo_id.startswith("PRUEBA-CARGA-")
    if es_prueba:  # nodos virtuales de la prueba de carga: se procesan igual pero no tocan el tablero
        nodo = modelo.Nodo(nodo_id, "PRUEBA DE CARGA", "ninguno", 0.0, 0.0)
    if nodo is None:
        _rechazar("nodo desconocido", evento.model_dump())
        raise HTTPException(404, f"nodo no registrado: {nodo_id}")
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
        "temperatura_c": lectura.temperatura_c, "humedad_suelo_pct": lectura.humedad_suelo_pct,
        "bateria_v": lectura.bateria_v, "confianza": lectura.confianza, "adultos_trampa": lectura.adultos_trampa,
    }
    for campo, (lo, hi) in RANGOS.items():
        if not lo <= valores[campo] <= hi:
            _rechazar(f"{campo} fuera de rango: {valores[campo]}", evento.model_dump())
            raise HTTPException(422, f"{campo} fuera de rango")

    previo = {} if es_prueba else (destinos().estado(nodo_id) or {})
    intervalo = INTERVALO_NOMINAL_MIN
    if previo.get("medido"):
        anterior = datetime.fromisoformat(previo["medido"])
        intervalo = (evento.time - anterior).total_seconds() / 60
        if previo.get("fcnt") is not None and evento.fCnt <= previo["fcnt"]:
            # Reintento o duplicado: Pub/Sub es «al menos una vez» y ChirpStack puede reenviar.
            log.info("duplicado ignorado", extra={"extra_json": {"nodo": nodo_id, "fcnt": evento.fCnt}})
            return {"duplicado": True}
    intervalo = min(max(intervalo, 5.0), 360.0)
    # Una sola ventana de 15 min tiene muy pocas capturas (0, 1, 2...): estimar con ella sería puro ruido.
    # Se usa la suma móvil de las últimas 24 h, que es como se lee una trampa amarilla en el campo.
    medido_iso = evento.time.astimezone(timezone.utc).isoformat()
    corte = evento.time.timestamp() - 86400
    ventana = [v for v in (previo.get("ventana") or []) if datetime.fromisoformat(v[0]).timestamp() > corte]
    ventana.append([medido_iso, lectura.adultos_trampa])
    primero = datetime.fromisoformat(previo.get("primer_visto") or medido_iso).timestamp() - intervalo * 60
    horas = max(1.0, min(24.0, (evento.time.timestamp() - max(primero, corte)) / 3600))
    capturas_dia = sum(v[1] for v in ventana) * 24 / horas
    adultos_hoja = round(modelo.adultos_hoja_desde_trampa(capturas_dia), 2)
    rx = evento.rxInfo[0] if evento.rxInfo else RxInfo()
    lora = evento.txInfo.get("modulation", {}).get("lora", {})

    fila = {
        "evento_id": evento.deduplicationId,
        "nodo_id": nodo_id,
        "invernadero": nodo.invernadero,
        "cultivo": nodo.cultivo,
        "dev_eui": evento.deviceInfo.devEui,
        "fcnt": evento.fCnt,
        "medido": medido_iso,
        "recibido": datetime.now(timezone.utc).isoformat(),
        "adultos_trampa": lectura.adultos_trampa,
        "intervalo_min": round(intervalo, 2),
        "adultos_hoja_est": adultos_hoja,
        # Con menos de 12 h de trampa la estimación es provisoria y no dispara alertas.
        "supera_umbral": horas >= HORAS_MINIMAS and adultos_hoja >= modelo.UMBRAL_ACCION,
        "horas_ventana": round(horas, 2),
        "confianza": lectura.confianza,
        "temperatura_c": lectura.temperatura_c,
        "humedad_suelo_pct": lectura.humedad_suelo_pct,
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
        return {"aceptado": True, "adultos_hoja_est": adultos_hoja}
    bitacora = (previo.get("bitacora") or [])[-19:]
    nivel = "CRITICAL" if fila["supera_umbral"] else "INFO"
    aviso = " Action threshold exceeded." if fila["supera_umbral"] else (
        " Provisional (trap window < 12 h)." if horas < HORAS_MINIMAS else "")
    bitacora.append(f"[{nivel} {evento.time.astimezone(modelo.ZONA_AR):%d/%m %H:%M}] "
                    f"{adultos_hoja:.1f} adults/leaf (trap: {lectura.adultos_trampa}, "
                    f"conf {lectura.confianza:.2f}, {len(crudo)} B, RSSI {rx.rssi} dBm).{aviso}")
    destinos().guardar_estado(nodo_id, {
        **{k: fila[k] for k in ("medido", "fcnt", "adultos_hoja_est", "supera_umbral", "temperatura_c",
                                "humedad_suelo_pct", "bateria_v", "mapa_ocupacion", "rssi", "snr", "confianza")},
        "invernadero": nodo.invernadero, "cultivo": nodo.cultivo, "lat": nodo.lat, "lon": nodo.lon,
        "bitacora": bitacora, "ventana": ventana, "primer_visto": previo.get("primer_visto") or medido_iso,
    })
    log.info("uplink aceptado", extra={"extra_json": {"nodo": nodo_id, "fcnt": evento.fCnt,
                                                      "adultos_hoja": adultos_hoja, "rssi": rx.rssi}})
    return {"aceptado": True, "adultos_hoja_est": adultos_hoja, "supera_umbral": fila["supera_umbral"]}


@app.exception_handler(Exception)
async def _error(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse

    log.error(f"error no controlado: {exc!r}", extra={"extra_json": {"ruta": request.url.path}})
    return JSONResponse({"error": "interno"}, status_code=500)
