"""Evento «up» con el formato de la integración HTTP de ChirpStack v4 (JSON).

En el sistema real, el gateway LoRaWAN reenvía los paquetes al servidor de red
ChirpStack, que llama por HTTP a la API de ingesta con este JSON. El simulador
genera exactamente el mismo formato, así que la nube no distingue un nodo real
de uno simulado.
"""
from __future__ import annotations

import base64
import random
from datetime import datetime

# AU915 sub-banda 2 (canales 8-15): 916,8 a 918,2 MHz en pasos de 200 kHz.
# Es la sub-banda que usan la mayoría de los gateways de 8 canales en AU915.
FRECUENCIAS_SB2 = [916_800_000 + 200_000 * i for i in range(8)]


def dev_eui(nodo_id: str) -> str:
    import hashlib

    return hashlib.sha256(nodo_id.encode()).hexdigest()[:16]


def evento_up(nodo_id: str, fcnt: int, carga: bytes, cuando: datetime,
              rng: random.Random, distancia_km: float = 2.0) -> dict:
    rssi = int(-60 - 20 * distancia_km ** 0.5 + rng.gauss(0, 3))
    return {
        "deduplicationId": f"{dev_eui(nodo_id)}-{fcnt}",
        "time": cuando.isoformat().replace("+00:00", "Z"),
        "deviceInfo": {
            "applicationName": "ecocentinela",
            "deviceName": nodo_id,
            "devEui": dev_eui(nodo_id),
            "deviceProfileName": "esp32cam-fomo-au915",
        },
        "fCnt": fcnt,
        "fPort": 1,
        "dr": 2,
        "data": base64.b64encode(carga).decode(),
        "rxInfo": [{
            "gatewayId": "rc-gw-01",
            "rssi": rssi,
            "snr": round(rng.uniform(2.0, 9.5), 1),
        }],
        "txInfo": {
            "frequency": rng.choice(FRECUENCIAS_SB2),
            "modulation": {"lora": {"bandwidth": 125000, "spreadingFactor": 10, "codeRate": "CR_4_5"}},
        },
    }
