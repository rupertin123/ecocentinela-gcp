"""Flota simulada de nodos EcoCentinela (Cloud Run Job).

Reemplaza al hardware físico. Lote de soja con 4 mástiles y 3 cámaras por mástil
(75, 45 y 15 cm): cada cámara vigila UNA plaga en SU estrato de la planta. En cada
corrida se avanza la población de cada nodo hasta «ahora», se descuenta el efecto
de los insecticidas que el productor registró, se «saca la foto», se cuenta la plaga
y se envía la carga binaria de 10 bytes a la API de ingesta con el mismo JSON que
mandaría ChirpStack.

Modos:
  normal   (por defecto) una foto por nodo; Cloud Scheduler lo corre cada hora de 7 a 20 h.
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

from ecocentinela import carga_util, chirpstack, eficacia, modelo

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


def _a_estado(d: dict | None, nodo: modelo.Nodo, rng: random.Random, t: datetime) -> modelo.EstadoNodo:
    plaga = modelo.PLAGAS[nodo.plaga]
    if not d or d.get("ciclo") != modelo.ciclo(t):
        # Primera corrida o campaña nueva (el reloj de campaña se repite cada 90 días): se reinicia la plaga.
        return modelo.EstadoNodo(sin_tratar=modelo.densidad_inicial(plaga, rng), ultimo=t - timedelta(hours=1),
                                 fcnt=(d or {}).get("fcnt", 0), ciclo=modelo.ciclo(t))
    return modelo.EstadoNodo(sin_tratar=d["sin_tratar"], ultimo=datetime.fromisoformat(d["ultimo"]),
                             bateria_v=d["bateria_v"], fcnt=d["fcnt"], ciclo=d["ciclo"])


def _de_estado(e: modelo.EstadoNodo) -> dict:
    return {"sin_tratar": e.sin_tratar, "ultimo": e.ultimo.isoformat(), "bateria_v": e.bateria_v,
            "fcnt": e.fcnt, "ciclo": e.ciclo}


def armar_evento(nodo: modelo.Nodo, e: modelo.EstadoNodo, hasta: datetime, aplicaciones: list[dict],
                 rng: random.Random) -> dict:
    """Avanza el nodo hasta `hasta`, «saca la foto», la cuenta y arma el uplink de 10 bytes."""
    plaga = modelo.PLAGAS[nodo.plaga]
    modelo.avanzar(plaga, e, hasta, rng)
    # Lo que realmente hay en la planta: la población sin tratar menos lo que elimina el insecticida.
    real = e.sin_tratar * (1 - eficacia.eficacia_en_campo(aplicaciones, plaga.codigo, e.ultimo))
    conteo = modelo.conteo_imagen(plaga, real, rng)
    e.fcnt += 1
    banderas = 0
    if e.bateria_v < 3.5:
        banderas |= carga_util.BANDERA_BATERIA_BAJA
    if rng.random() < 0.02:  # ráfaga de viento: la foto sale movida y el nodo la marca
        banderas |= carga_util.BANDERA_IMAGEN_BORROSA
    temp = modelo.temperatura_aire(e.ultimo) + rng.gauss(0, 0.6)
    lectura = carga_util.Lectura(
        conteo=conteo,
        confianza=max(0.55, min(0.98, rng.gauss(0.91 - 0.003 * conteo, 0.025))),  # más individuos, más superposición
        temperatura_c=temp, humedad_rel_pct=modelo.humedad_relativa(temp, rng), bateria_v=e.bateria_v,
        mapa_ocupacion=modelo.mapa_ocupacion(conteo, rng), banderas=banderas)
    return chirpstack.evento_up(nodo.id, e.fcnt, carga_util.codificar(lectura), e.ultimo, rng,
                                distancia_km=rng.uniform(0.3, 2.5))


def _aplicaciones_firestore(fs, t: datetime) -> list[dict]:
    """Aplicaciones registradas por el productor en la campaña simulada actual."""
    salida = []
    for d in fs.collection("aplicaciones").where("ciclo", "==", modelo.ciclo(t)).stream():
        a = d.to_dict()
        a["aplicada_en"] = datetime.fromisoformat(a["aplicada_en"])
        salida.append(a)
    return salida


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
    ahora = modelo.ahora().replace(second=0, microsecond=0)
    if not modelo.es_de_dia(ahora):
        log("de noche los nodos duermen: no hay fotos")  # Cloud Scheduler sólo dispara de 7 a 20 h
        return
    fs, rng, em = _fs(), random.Random(), Emisor()
    aplicaciones = _aplicaciones_firestore(fs, ahora)
    previos = {d.id: d.to_dict() for d in fs.collection("sim_estado").stream()}  # 1 consulta, no 12
    lote = fs.batch()
    codigos: dict[int, int] = {}
    for nodo in modelo.NODOS:
        e = _a_estado(previos.get(nodo.id), nodo, rng, ahora)
        if (ahora - e.ultimo) > timedelta(days=2):
            e.ultimo = ahora - timedelta(hours=1)  # no reconstruir días perdidos (p. ej. tras una pausa)
        codigo = em.enviar(armar_evento(nodo, e, ahora, aplicaciones, rng))
        codigos[codigo] = codigos.get(codigo, 0) + 1
        lote.set(fs.collection("sim_estado").document(nodo.id), _de_estado(e))
    lote.commit()
    log("corrida terminada", codigos=codigos, estadio=modelo.estadio(ahora), aplicaciones=len(aplicaciones))
    if any(c >= 400 for c in codigos):
        sys.exit(1)


def _guion_historia(estrato: str, cruces: dict, t: datetime, supera: bool) -> bool:
    """Decide si en la historia el «productor» aplicó: sólo el estrato superior, la primera vez que la
    recomendación aparece en R3 o después. Así la historia muestra una aplicación con su decaimiento
    y deja a las chinches (estrato medio) esperando la decisión del productor en el tablero."""
    if estrato != "superior" or not supera or estrato in cruces:
        return False
    return modelo.dds(t) >= 73


def modo_historia(dias: int) -> None:
    """Corre la misma lógica de la API de ingesta en memoria y carga el resultado en BigQuery por lotes.

    Las cargas por lotes (load jobs) de BigQuery son gratuitas; el streaming no.
    """
    os.environ["MODO_LOCAL"] = "1"
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingesta"))
    from fastapi.testclient import TestClient
    import main as ingesta  # noqa: E402  (la API de ingesta)

    cli, rng = TestClient(ingesta.app), random.Random(2026)
    fin = modelo.ahora().replace(minute=0, second=0, microsecond=0)
    inicio = fin - timedelta(days=dias)
    if modelo.ciclo(inicio) != modelo.ciclo(fin):  # la historia no puede cruzar el reinicio de campaña
        inicio = modelo.INICIO_RELOJ + timedelta(days=modelo.DURACION_CICLO_DIAS * modelo.ciclo(fin), hours=1)
    estados = {n.id: modelo.EstadoNodo(sin_tratar=modelo.densidad_inicial(modelo.PLAGAS[n.plaga], rng),
                                       ultimo=inicio, ciclo=modelo.ciclo(fin)) for n in modelo.NODOS}
    aplicaciones: list[dict] = []
    cruces: dict[str, datetime] = {}
    t = inicio
    while t < fin:
        t += timedelta(hours=1)
        if not modelo.es_de_dia(t):
            continue
        por_estrato: dict[str, list[bool]] = {}
        for n in modelo.NODOS:
            r = cli.post("/v1/uplink", json=armar_evento(n, estados[n.id], t, aplicaciones, rng))
            assert r.status_code == 202, r.text
            por_estrato.setdefault(n.estrato, []).append(bool(r.json().get("supera_umbral")))
        for estrato, v in por_estrato.items():
            if _guion_historia(estrato, cruces, t, sum(v) >= 3):
                cruces[estrato] = t
                plaga = modelo.POR_ESTRATO[estrato].codigo
                p = eficacia.PRODUCTO_DE_PLAGA[plaga]
                aplicada = t + timedelta(hours=20)  # el productor recibe la recomendación y aplica al otro día
                aplicaciones.append({"id": f"hist-{estrato}-{aplicada:%Y%m%d}", "estrato": estrato, "plaga": plaga,
                                     "producto": p.codigo, "aplicada_en": aplicada, "registrada": aplicada,
                                     "origen": "historia", "ciclo": modelo.ciclo(t)})
    filas = [f for top, f in ingesta.destinos().publicados if top == ingesta.TOPICO]
    log("historia generada", filas=len(filas), desde=inicio.isoformat(), hasta=fin.isoformat(),
        aplicaciones=len(aplicaciones))

    from google.cloud import bigquery

    bq = bigquery.Client(project=PROYECTO)
    cfg = dict(write_disposition="WRITE_APPEND", source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
    trabajo = bq.load_table_from_json(filas, f"{PROYECTO}.{DATASET}.telemetria", job_config=bigquery.LoadJobConfig(**cfg))
    trabajo.result()
    log("historia cargada en BigQuery", filas=trabajo.output_rows)
    if aplicaciones:
        filas_apl = [_fila_aplicacion(a) for a in aplicaciones]
        bq.load_table_from_json(filas_apl, f"{PROYECTO}.{DATASET}.aplicaciones",
                                job_config=bigquery.LoadJobConfig(**cfg)).result()

    fs = _fs()
    for a in aplicaciones:
        fs.collection("aplicaciones").document(a["id"]).set(
            {**a, "aplicada_en": a["aplicada_en"].isoformat(), "registrada": a["registrada"].isoformat()})
    for nid, e in estados.items():
        fs.collection("sim_estado").document(nid).set(_de_estado(e))
        fs.collection("estado_nodos").document(nid).set(ingesta.destinos().estados[nid])
    log("estado inicial de la flota guardado en Firestore")


def _fila_aplicacion(a: dict) -> dict:
    p = eficacia.PRODUCTOS[a["producto"]]
    return {"aplicacion_id": a["id"], "estrato": a["estrato"], "plaga": a["plaga"], "producto": a["producto"],
            "principio_activo": p.principio_activo, "aplicada_en": a["aplicada_en"].isoformat(),
            "registrada": a["registrada"].isoformat(), "origen": a["origen"], "ciclo": a["ciclo"],
            "e0_bibliografia": p.e0, "k_bibliografia": p.k}


def modo_carga(n: int, hilos: int) -> None:
    """N nodos virtuales «PRUEBA-CARGA-xxxx» transmiten a la vez; mide latencias."""
    em, rng = Emisor(), random.Random(7)
    ahora = modelo.ahora()
    eventos = []
    for i in range(n):
        e = modelo.EstadoNodo(sin_tratar=rng.uniform(1, 30), ultimo=ahora - timedelta(hours=1))
        nodo = modelo.Nodo(f"PRUEBA-CARGA-{i:04d}", "PRUEBA", "rachiplusia", 0.0, 0.0)
        eventos.append(armar_evento(nodo, e, ahora, [], rng))

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
