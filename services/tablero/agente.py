"""Agente agrónomo con Gemini y llamadas a funciones, sin frameworks (ReAct a mano).

El modelo no inventa datos: para responder tiene que pedir las herramientas
(estado del nodo, historia, pronóstico, umbral y fuentes) y el servidor las
ejecuta contra Firestore y BigQuery. Corre sobre la capa GRATUITA de la API de
Gemini, en un proyecto sin facturación: no puede generar costos.
"""
from __future__ import annotations

import os
import time
from collections import deque

from ecocentinela import modelo

# El modelo «lite» responde en ~1 s en la capa gratuita; el «flash» completo tardaba más de 1 min.
MODELO = os.environ.get("MODELO_GEMINI", "gemini-flash-lite-latest")
MAX_RONDAS = 4
_pedidos: deque[float] = deque()
LIMITE_POR_HORA = int(os.environ.get("LIMITE_AGENTE_HORA", "30"))

INSTRUCCIONES = f"""Sos el asistente agronómico de EcoCentinela, una red (simulada) de trampas amarillas
con cámara para mosca blanca de los invernaderos (Trialeurodes vaporariorum) en el Cinturón Verde de
Río Cuarto, Córdoba, Argentina. Respondé en el idioma de la pregunta, en no más de 120 palabras.
Reglas:
- Usá SIEMPRE las herramientas para obtener datos; nunca inventes números.
- El umbral de acción es {modelo.UMBRAL_ACCION:g} adultos por hoja y/o 8 ninfas por folíolo
  (Polack y Mitidieri, 2005, INTA). Citalo cuando hables de intervenir.
- La densidad por hoja es una ESTIMACIÓN a partir de capturas en trampa; aclaralo.
- No recomiendes productos químicos ni dosis: sugerí consultar con un ingeniero agrónomo matriculado
  y mencioná alternativas de manejo integrado (por ejemplo, control biológico).
- Recordá que los datos son simulados si te preguntan por un productor o un campo real."""


def _herramientas(estados, historia):
    def estado_nodo(nodo: str) -> dict:
        """Último estado conocido de un nodo: densidad estimada, temperatura, batería y si supera el umbral."""
        e = estados().get(nodo)
        if not e:
            return {"error": f"nodo desconocido: {nodo}"}
        return {k: e.get(k) for k in ("medido", "adultos_hoja_est", "supera_umbral", "temperatura_c",
                                      "humedad_suelo_pct", "bateria_v", "invernadero", "cultivo")}

    def resumen_flota() -> list[dict]:
        """Densidad estimada actual de los 12 nodos, para comparar invernaderos."""
        return [{"nodo": k, "invernadero": v.get("invernadero"), "adultos_hoja_est": v.get("adultos_hoja_est"),
                 "supera_umbral": v.get("supera_umbral")} for k, v in sorted(estados().items())]

    def tendencia(nodo: str) -> dict:
        """Densidad promedio por hora de las últimas 72 h y pronóstico de las próximas 48 h (BigQuery ML)."""
        h = historia(nodo)
        serie = h["historia"]
        return {"hace_72h": serie[0]["adultos_hoja"] if serie else None,
                "hace_24h": serie[-24]["adultos_hoja"] if len(serie) >= 24 else None,
                "ahora": serie[-1]["adultos_hoja"] if serie else None,
                "pronostico_48h": h["pronostico"][-1]["adultos_hoja"] if h["pronostico"] else None}

    def umbral_y_fuentes() -> dict:
        """Umbral de acción y fuentes científicas que usa el sistema."""
        return {"umbral_accion_adultos_hoja": modelo.UMBRAL_ACCION, "umbral_ninfas_foliolo": 8,
                "fuente_umbral": "Polack y Mitidieri (2005), INTA San Pedro",
                "desarrollo": "T. vaporariorum: 2,92 °C base, 483,4 °D huevo-adulto (Greenberg et al., 2000)",
                "perdidas": "60-80 % de rendimiento comercial por fumagina (Scotta et al., 2014, UNL)"}

    return {f.__name__: f for f in (estado_nodo, resumen_flota, tendencia, umbral_y_fuentes)}


def responder(pregunta: str, nodo: str, estados, historia) -> dict:
    ahora = time.time()
    while _pedidos and ahora - _pedidos[0] > 3600:
        _pedidos.popleft()
    if len(_pedidos) >= LIMITE_POR_HORA:
        return {"respuesta": "El asistente alcanzó su límite por hora (capa gratuita). Probá más tarde.", "pasos": []}
    _pedidos.append(ahora)

    from google import genai
    from google.genai import types

    funciones = _herramientas(estados, historia)
    # Los alias «-latest» sólo existen en la versión v1beta de la API.
    cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta", timeout=30000))
    config = types.GenerateContentConfig(
        system_instruction=INSTRUCCIONES, temperature=0.2,
        tools=list(funciones.values()),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    mensajes = [types.Content(role="user", parts=[types.Part(text=f"(Nodo seleccionado: {nodo}) {pregunta}")])]
    pasos = []
    for _ in range(MAX_RONDAS):
        r = cliente.models.generate_content(model=MODELO, contents=mensajes, config=config)
        llamadas = r.function_calls or []
        if not llamadas:
            return {"respuesta": r.text, "pasos": pasos, "modelo": r.model_version}
        mensajes.append(r.candidates[0].content)
        partes = []
        for ll in llamadas:
            f = funciones.get(ll.name)
            resultado = f(**(ll.args or {})) if f else {"error": "herramienta inexistente"}
            pasos.append({"herramienta": ll.name, "argumentos": dict(ll.args or {})})
            partes.append(types.Part.from_function_response(name=ll.name, response={"resultado": resultado}))
        mensajes.append(types.Content(role="user", parts=partes))
    return {"respuesta": "No pude completar la consulta en pocos pasos.", "pasos": pasos}
