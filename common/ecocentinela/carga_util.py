"""Codificación binaria de la telemetría LoRaWAN del nodo EcoCentinela.

Por qué binario y no JSON
-------------------------
Argentina usa el plan de frecuencias AU915-928 (banda 915-928 MHz, ENACOM
Res. 551/2021 y 280/2022). Según LoRaWAN Regional Parameters RP002-1.0.3,
tabla 45, con DR2 (SF10 / 125 kHz) la carga de aplicación máxima es:

* 51 bytes con UplinkDwellTime = 0 (sin límite de permanencia), y
* 11 bytes con UplinkDwellTime = 1 (límite de 400 ms), que es el valor que
  todo nodo AU915 DEBE asumir al arrancar hasta que el servidor le diga otra cosa.

El JSON «minificado» de 54 bytes de la prueba de concepto original no entra en
ninguno de los dos casos. Este formato ocupa 10 bytes y entra incluso en el
peor caso, dejando 1 byte de margen.

Formato (big-endian, puerto LoRaWAN 1, versión 1)
------------------------------------------------
byte 0      versión (4 bits altos) | banderas (4 bits bajos)
bytes 1-2   uint16  adultos nuevos contados en la trampa amarilla desde el envío anterior
byte 3      uint8   confianza media del modelo FOMO (0-255 -> 0,0-1,0)
bytes 4-5   int16   temperatura del aire junto a la hoja, centésimas de °C
byte 6      uint8   humedad volumétrica del suelo, medios puntos porcentuales (0-127,5 %)
byte 7      uint8   tensión de batería: (mV - 2500) / 10  -> 2,50 a 5,05 V
bytes 8-9   uint16  mapa de ocupación 4x4 de la trampa (bit i = celda i con detecciones)

El identificador del nodo NO viaja en la carga: LoRaWAN ya lo transporta en la
cabecera (DevAddr / DevEUI), así que repetirlo sería desperdiciar bytes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

VERSION = 1
LARGO = 10
LIMITE_AU915_DR2_DWELL = 11  # RP002-1.0.3, tabla 45, UplinkDwellTime = 1
_FORMATO = ">BHBhBBH"

# Banderas (4 bits)
BANDERA_BATERIA_BAJA = 0x1
BANDERA_TRAMPA_SATURADA = 0x2
BANDERA_ERROR_CAMARA = 0x4
BANDERA_MITIGACION_ACTIVA = 0x8


class CargaInvalida(ValueError):
    """La carga recibida no respeta el formato."""


@dataclass(frozen=True)
class Lectura:
    adultos_trampa: int
    confianza: float
    temperatura_c: float
    humedad_suelo_pct: float
    bateria_v: float
    mapa_ocupacion: int = 0
    banderas: int = 0

    def celdas_ocupadas(self) -> list[int]:
        return [i for i in range(16) if self.mapa_ocupacion >> i & 1]


def _acotar(valor: float, minimo: int, maximo: int) -> int:
    return max(minimo, min(maximo, int(round(valor))))


def codificar(lectura: Lectura) -> bytes:
    datos = struct.pack(
        _FORMATO,
        (VERSION << 4) | (lectura.banderas & 0x0F),
        _acotar(lectura.adultos_trampa, 0, 0xFFFF),
        _acotar(lectura.confianza * 255, 0, 255),
        _acotar(lectura.temperatura_c * 100, -32768, 32767),
        _acotar(lectura.humedad_suelo_pct * 2, 0, 255),
        _acotar((lectura.bateria_v * 1000 - 2500) / 10, 0, 255),
        lectura.mapa_ocupacion & 0xFFFF,
    )
    assert len(datos) == LARGO <= LIMITE_AU915_DR2_DWELL
    return datos


def decodificar(datos: bytes) -> Lectura:
    if len(datos) != LARGO:
        raise CargaInvalida(f"se esperaban {LARGO} bytes y llegaron {len(datos)}")
    cab, adultos, conf, temp, hum, bat, mapa = struct.unpack(_FORMATO, datos)
    version = cab >> 4
    if version != VERSION:
        raise CargaInvalida(f"versión de formato desconocida: {version}")
    return Lectura(
        adultos_trampa=adultos,
        confianza=round(conf / 255, 3),
        temperatura_c=temp / 100,
        humedad_suelo_pct=hum / 2,
        bateria_v=round((bat * 10 + 2500) / 1000, 2),
        mapa_ocupacion=mapa,
        banderas=cab & 0x0F,
    )


def json_equivalente(lectura: Lectura, nodo: str) -> bytes:
    """El JSON minificado de la PoC original, sólo para comparar tamaños."""
    import json

    return json.dumps(
        {"id": nodo, "t": lectura.temperatura_c, "p": lectura.adultos_trampa,
         "c": lectura.confianza, "h": lectura.humedad_suelo_pct, "b": lectura.bateria_v},
        separators=(",", ":"),
    ).encode()
