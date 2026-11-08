"""Genera una base SQLite con el MISMO esquema y simulador que la PoC original (marzo 2026),
para tener un origen realista que migrar. Uso: python migracion/generar_base_legada.py salida.db"""
import random
import sqlite3
import sys
from datetime import datetime, timedelta

con = sqlite3.connect(sys.argv[1])
con.execute("CREATE TABLE IF NOT EXISTS telemetry_logs (node_id TEXT, timestamp TEXT, temperature REAL, "
            "soil_moisture REAL, battery_level REAL, pest_density INTEGER, confidence_score REAL)")
rng, densidad, t = random.Random(3), 10, datetime(2026, 3, 23, 21, 0)
for _ in range(2000):  # misma caminata aleatoria que src/simulation/lora_mock.py
    densidad = max(0, densidad + rng.randint(-2, 8)) if densidad < 120 else 10
    con.execute("INSERT INTO telemetry_logs VALUES (?,?,?,?,?,?,?)", (
        "ESP32-CAM-DELTA-4", t.isoformat(), round(rng.uniform(22.0, 28.5), 1), round(rng.uniform(40, 60), 1),
        round(rng.uniform(3.8, 4.2), 2), densidad, round(rng.uniform(0.85, 0.98), 2)))
    t += timedelta(seconds=2)
con.commit()
print(con.execute("SELECT COUNT(*) FROM telemetry_logs").fetchone()[0], "filas")
