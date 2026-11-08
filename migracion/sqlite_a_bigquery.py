"""Migra la base SQLite del gateway de la PoC original (tabla telemetry_logs) a BigQuery.

Pasos de una migración de datos «de las instalaciones a la nube»:
  1. Extraer por lotes con un generador (memoria constante aunque la base sea grande).
  2. Transformar al esquema nuevo (nombres en castellano, unidades explícitas, UTC).
  3. Cargar con un load job (gratis) en una tabla separada: nunca mezclar datos viejos sin validar.
  4. Conciliar: contar filas y comparar sumas de control entre origen y destino.

Uso: python migracion/sqlite_a_bigquery.py ruta/ecocentinela.db
"""
from __future__ import annotations

import gc
import io
import json
import sqlite3
import sys
from datetime import timezone, datetime

PROYECTO = "ecocentinela-rio-cuarto"
TABLA = f"{PROYECTO}.ecocentinela.telemetria_legado"
LOTE = 5000


def extraer(ruta: str):
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)  # sólo lectura: el origen no se toca
    cur = con.execute("SELECT node_id, timestamp, temperature, soil_moisture, battery_level, "
                      "pest_density, confidence_score FROM telemetry_logs ORDER BY timestamp")
    while filas := cur.fetchmany(LOTE):
        yield filas
        gc.collect()
    con.close()


def transformar(fila: tuple) -> dict:
    nodo, ts, temp, hum, bat, plaga, conf = fila
    t = datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)  # la PoC guardaba datetime.utcnow() sin zona
    return {"nodo_id": nodo, "medido": t.isoformat(), "temperatura_c": temp, "humedad_suelo_pct": hum,
            "bateria_v": bat, "conteo_fomo": plaga, "confianza": conf, "origen": "poc-sqlite-2026-03"}


def main(ruta: str) -> None:
    from google.cloud import bigquery

    bq = bigquery.Client(project=PROYECTO)
    esquema = [bigquery.SchemaField(n, t) for n, t in [
        ("nodo_id", "STRING"), ("medido", "TIMESTAMP"), ("temperatura_c", "FLOAT"),
        ("humedad_suelo_pct", "FLOAT"), ("bateria_v", "FLOAT"), ("conteo_fomo", "INTEGER"),
        ("confianza", "FLOAT"), ("origen", "STRING")]]
    cfg = bigquery.LoadJobConfig(schema=esquema, write_disposition="WRITE_TRUNCATE",
                                 source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
    n_origen, suma_origen, primero = 0, 0, True
    for lote in extraer(ruta):
        buf = io.BytesIO("".join(json.dumps(transformar(f)) + "\n" for f in lote).encode())
        bq.load_table_from_file(buf, TABLA, job_config=cfg).result()
        if primero:
            cfg.write_disposition, primero = "WRITE_APPEND", False
        n_origen += len(lote)
        suma_origen += sum(f[5] for f in lote)

    r = list(bq.query(f"SELECT COUNT(*) n, SUM(conteo_fomo) s FROM `{TABLA}`").result())[0]
    ok = (r.n, r.s) == (n_origen, suma_origen)
    print(json.dumps({"filas_origen": n_origen, "filas_destino": r.n, "suma_origen": suma_origen,
                      "suma_destino": r.s, "conciliacion": "OK" if ok else "DIFERENCIA"}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
