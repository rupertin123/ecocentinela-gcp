"""Flota simulada de nodos EcoCentinela (Cloud Run Job).

Reemplaza al hardware físico: cada corrida avanza el modelo de población de
mosca blanca de cada nodo hasta «ahora», arma la carga binaria de 10 bytes y la
envía a la API de ingesta con el mismo JSON que mandaría ChirpStack.

Modos:
  normal   (por defecto) una transmisión por nodo; Cloud Scheduler lo corre cada 15 min.
  historia genera N días de historia de una vez y la carga en BigQuery por lotes.
  carga    prueba de carga: N nodos virtuales transmitiendo en paralelo.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

from ecocentinela import carga_util, chirpstack, modelo

PROYECTO = os.environ.get("GOOGLE_CLOUD_PROJECT", "local")
URL_INGESTA = os.environ.get("URL_INGESTA", "http://localhost:8080")
DATASET = os.environ.get("DATASET", "ecocentinela")


def log(msg: str, **campos) -> None:
    print(json.dumps({"severity": "INFO", "message": msg, "componente": "simulador", **campos},
                     ensure_ascii=False), flush=True)


# ------------------------------------------------------------------ estado
def _fs():
    from google.cloud import firestore

    return firestore.Client(project=PROYECTO)


def _a_estado(d: dict | None, nodo: modelo.Nodo, rng: random.Random) -> modelo.EstadoNodo:
    if not d:
        return modelo.EstadoNodo(densidad=rng.uniform(0.3, 1.5),
                                 ultimo=modelo.ahora() - timedelta(minutes=15))
    return modelo.EstadoNodo(
        densidad=d["densidad"], ultimo=datetime.fromisoformat(d["ultimo"]),
        bateria_v=d["bateria_v"], fcnt=d["fcnt"],
        mitigacion_hasta=datetime.fromisoformat(d["mitigacion_hasta"]) if d.get("mitigacion_hasta") else None)


def _de_estado(e: modelo.EstadoNodo) -> dict:
    return {"densidad": e.densidad, "ultimo": e.ultimo.isoformat(), "bateria_v": e.bateria_v, "fcnt": e.fcnt,
            "mitigacion_hasta": e.mitigacion_hasta.isoformat() if e.mitigacion_hasta else None}


def armar_evento(nodo_id: str, e: modelo.EstadoNodo, hasta: datetime, rng: random.Random) -> dict:
    capturas, temp, humedad = modelo.paso(e, hasta, rng)
    e.fcnt += 1
    banderas = 0
    if e.bateria_v < 3.5:
        banderas |= carga_util.BANDERA_BATERIA_BAJA
    if e.mitigacion_hasta and e.ultimo <= e.mitigacion_hasta:
        banderas |= carga_util.BANDERA_MITIGACION_ACTIVA
    lectura = carga_util.Lectura(
        adultos_trampa=capturas,
        confianza=max(0.6, min(0.98, rng.gauss(0.92 - 0.002 * capturas, 0.02))),  # más superposición, menos confianza
        temperatura_c=temp, humedad_suelo_pct=humedad, bateria_v=e.bateria_v,
        mapa_ocupacion=modelo.mapa_ocupacion(capturas, rng), banderas=banderas)
    return chirpstack.evento_up(nodo_id, e.fcnt, carga_util.codificar(lectura), e.ultimo, rng,
                                distancia_km=rng.uniform(0.5, 4.0))


# ------------------------------------------------------------------ envío
class Emisor:
    """POST autenticado: Cloud Run sólo acepta llamadas con un token OIDC de una cuenta autorizada."""

    def __init__(self) -> None:
        import requests

        self.s = requests.Session()
        self.token, self.vence = None, 0.0

    def _cabeceras(self) -> dict:
        if URL_INGESTA.startswith("http://"):  # red interna (local o Kubernetes): sin token
            return {}
        if time.time() > self.vence:
            import google.auth.transport.requests
            from google.oauth2 import id_token

            self.token = id_token.fetch_id_token(google.auth.transport.requests.Request(), URL_INGESTA)
            self.vence = time.time() + 45 * 60
        return {"Authorization": f"Bearer {self.token}"}

    def enviar(self, evento: dict) -> int:
        for intento in range(4):
            r = self.s.post(f"{URL_INGESTA}/v1/uplink", params={"event": "up"}, json=evento,
                            headers=self._cabeceras(), timeout=30)
            if r.status_code < 500 and r.status_code != 429:
                return r.status_code
            time.sleep(2 ** intento)  # reintento con espera exponencial
        return r.status_code


# ------------------------------------------------------------------ modos
def modo_normal() -> None:
    fs, rng, em = _fs(), random.Random(), Emisor()
    ahora = modelo.ahora()
    codigos: dict[int, int] = {}
    for nodo in modelo.NODOS:
        ref = fs.collection("sim_estado").document(nodo.id)
        snap = ref.get()
        e = _a_estado(snap.to_dict() if snap.exists else None, nodo, rng)
        # «Downlink» simulado: el tablero deja una orden en Firestore y el nodo la recibe en su próxima ventana.
        cmd_ref = fs.collection("comandos").document(nodo.id)
        cmd = cmd_ref.get()
        if cmd.exists and cmd.to_dict().get("pendiente"):
            e.mitigacion_hasta = ahora + timedelta(hours=48)
            cmd_ref.set({"pendiente": False, "aplicado": ahora.isoformat()}, merge=True)
            log("mitigación aplicada", nodo=nodo.id)
        if (ahora - e.ultimo) > timedelta(days=2):
            e.ultimo = ahora - timedelta(hours=1)  # no reconstruir días perdidos (p. ej. tras una pausa)
        evento = armar_evento(nodo.id, e, ahora, rng)
        codigo = em.enviar(evento)
        codigos[codigo] = codigos.get(codigo, 0) + 1
        ref.set(_de_estado(e))
    log("corrida terminada", codigos=codigos)
    if any(c >= 400 for c in codigos):
        sys.exit(1)


def modo_historia(dias: int) -> None:
    """Corre la misma lógica de la API de ingesta en memoria y carga el resultado en BigQuery por lotes.

    Las cargas por lotes (load jobs) de BigQuery son gratuitas; el streaming no.
    """
    os.environ["MODO_LOCAL"] = "1"
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingesta"))
    from fastapi.testclient import TestClient
    import main as ingesta  # noqa: E402  (la API de ingesta)

    cli, rng = TestClient(ingesta.app), random.Random(2026)
    fin = modelo.ahora().replace(second=0, microsecond=0)
    inicio = fin - timedelta(days=dias)
    estados = {n.id: modelo.EstadoNodo(densidad=rng.uniform(0.2, 1.2), ultimo=inicio) for n in modelo.NODOS}
    # El invernadero 02 arranca más infestado y recibe una intervención cuando cruza el umbral,
    # para que la historia muestre el efecto de la mitigación.
    for nid in ("RC-INV02-N1", "RC-INV02-N2", "RC-INV02-N3"):
        estados[nid].densidad = 3.0
    mitigaciones = []
    t = inicio
    while t < fin:
        t += timedelta(minutes=15)
        for n in modelo.NODOS:
            e = estados[n.id]
            r = cli.post("/v1/uplink", json=armar_evento(n.id, e, t, rng))
            assert r.status_code == 202, r.text
            if (n.invernadero == "Invernadero 02" and e.mitigacion_hasta is None
                    and r.json()["supera_umbral"]):
                for m in modelo.NODOS:
                    if m.invernadero == n.invernadero:
                        estados[m.id].mitigacion_hasta = t + timedelta(hours=48)
                mitigaciones.append({"invernadero": n.invernadero, "cuando": t.isoformat(), "origen": "historia",
                                     "perdida_evitada_usd": modelo.ModeloEconomico().perdida_evitada()})
    filas = [f for top, f in ingesta.destinos().publicados if top == ingesta.TOPICO]
    log("historia generada", filas=len(filas), desde=inicio.isoformat(), hasta=fin.isoformat())

    from google.cloud import bigquery

    bq = bigquery.Client(project=PROYECTO)
    tabla = f"{PROYECTO}.{DATASET}.telemetria"
    trabajo = bq.load_table_from_json(filas, tabla, job_config=bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND", source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON))
    trabajo.result()
    log("historia cargada en BigQuery", tabla=tabla, filas=trabajo.output_rows)

    fs = _fs()
    for m in mitigaciones:
        fs.collection("mitigaciones").add(m)
    for nid, e in estados.items():
        e.mitigacion_hasta = None
        fs.collection("sim_estado").document(nid).set(_de_estado(e))
        fs.collection("estado_nodos").document(nid).set(ingesta.destinos().estados[nid])
    log("estado inicial de la flota guardado en Firestore")


def modo_carga(n: int, hilos: int) -> None:
    """N nodos virtuales «PRUEBA-CARGA-xxxx» transmiten a la vez; mide latencias."""
    em, rng = Emisor(), random.Random(7)
    ahora = modelo.ahora()
    eventos = []
    for i in range(n):
        e = modelo.EstadoNodo(densidad=rng.uniform(1, 30), ultimo=ahora - timedelta(minutes=15))
        eventos.append(armar_evento(f"PRUEBA-CARGA-{i:04d}", e, ahora, rng))

    def uno(ev: dict) -> tuple[int, float]:
        t0 = time.perf_counter()
        c = em.enviar(ev)
        return c, (time.perf_counter() - t0) * 1000

    em._cabeceras()
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(hilos) as ex:
        res = list(ex.map(uno, eventos))
    total = time.perf_counter() - t0
    lat = sorted(r[1] for r in res)
    pct = lambda p: round(lat[min(len(lat) - 1, int(p / 100 * len(lat)))], 1)  # noqa: E731
    codigos: dict[int, int] = {}
    for c, _ in res:
        codigos[c] = codigos.get(c, 0) + 1
    log("prueba de carga", n=n, hilos=hilos, segundos=round(total, 2), por_segundo=round(n / total, 1),
        p50_ms=pct(50), p95_ms=pct(95), p99_ms=pct(99), codigos=codigos)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", default=os.environ.get("MODO", "normal"), choices=["normal", "historia", "carga"])
    ap.add_argument("--dias", type=int, default=30)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--hilos", type=int, default=32)
    a = ap.parse_args()
    {"normal": modo_normal, "historia": lambda: modo_historia(a.dias),
     "carga": lambda: modo_carga(a.n, a.hilos)}[a.modo]()
