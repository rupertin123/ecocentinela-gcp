"""Agente agrónomo con Gemini y llamadas a funciones, sin frameworks (ReAct a mano).

El modelo no inventa datos: para responder tiene que pedir las herramientas
(estado del lote, detalle de cada estrato, pronóstico y eficacia residual) y el servidor las
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

INSTRUCCIONES = """Sos el asistente agronómico de EcoCentinela: una red (SIMULADA) de 12 cámaras con IA en el
borde en un lote ficticio de soja cerca de Río Cuarto, Córdoba, Argentina. Hay 4 mástiles y en cada uno 3 cámaras a
distinta altura; cada altura vigila una plaga: estrato superior (75 cm) oruga medidora Rachiplusia nu, estrato medio
(45 cm) chinche de la alfalfa Piezodorus guildinii, estrato inferior (15 cm) trips Caliothrips phaseoli.
Respondé en el idioma de la pregunta, en no más de 130 palabras.
Reglas:
- Usá SIEMPRE las herramientas para obtener datos; nunca inventes números ni fechas.
- Citá la fuente del umbral cuando hables de aplicar.
- La densidad es una ESTIMACIÓN a partir de los conteos de las cámaras; aclaralo.
- El sistema RECOMIENDA y el productor decide. No indiques marcas ni dosis: nombrá, si hace falta, el principio
  activo de referencia que trae la herramienta y sugerí confirmar la receta con un ingeniero agrónomo matriculado.
  Mencioná alternativas de manejo integrado (monitoreo, enemigos naturales, rotación de modos de acción).
- La eficacia residual se estima con E(t)=E0·e^(-k·t); el lote queda «descubierto» cuando baja de 50 %. Si la
  herramienta trae un ajuste de BigQuery con datos del lote, priorizalo sobre el valor bibliográfico y decilo.
- Recordá que los datos son simulados si te preguntan por un productor o un campo real."""


def _herramientas(resumen, detalle):
    def estado_del_lote() -> dict:
        """Estadio del cultivo y, para cada estrato, densidad estimada, umbral, recomendación y protección vigente."""
        r = resumen()
        return {"estadio": r["estadio"], "fecha_agronomica": r["fecha_agronomica"],
                "estratos": [{k: e.get(k) for k in ("estrato", "nombre", "unidad", "densidad", "umbral", "estado",
                                                    "fuente_umbral", "producto_sugerido", "proteccion")}
                             for e in r["estratos"]]}

    def detalle_de_estrato(estrato: str) -> dict:
        """Detalle de un estrato (superior, medio o inferior): nodos, tendencia de 10 días, pronóstico de
        BigQuery ML (ARIMA_PLUS, 72 h) y curva de eficacia residual de la última aplicación."""
        try:
            d = detalle(estrato)
        except Exception:
            return {"error": "estrato desconocido: usá superior, medio o inferior"}
        serie = d["historia"]
        ef = d.get("eficacia")
        return {
            "nodos": [{"id": n["id"], "densidad_est": n.get("densidad_est")} for n in d["nodos"]],
            "densidad_hace_5_dias": serie[-70]["densidad"] if len(serie) >= 70 else None,
            "densidad_ahora": serie[-1]["densidad"] if serie else None,
            "pronostico_72h": d["pronostico"][-1]["densidad"] if d["pronostico"] else None,
            "eficacia": None if not ef else {
                "principio_activo": ef["principio_activo"], "aplicada_en": ef["aplicada_en"],
                "bibliografia": ef["bibliografia"], "ajuste_bigquery": ef["campo"], "fuente": ef["fuente"],
                "umbral_descubierto": ef["e_min"]},
        }

    return {f.__name__: f for f in (estado_del_lote, detalle_de_estrato)}


def responder(pregunta: str, estrato: str, resumen, detalle) -> dict:
    ahora = time.time()
    while _pedidos and ahora - _pedidos[0] > 3600:
        _pedidos.popleft()
    if len(_pedidos) >= LIMITE_POR_HORA:
        return {"respuesta": "El asistente alcanzó su límite por hora (capa gratuita). Probá más tarde.", "pasos": []}
    _pedidos.append(ahora)

    from google import genai
    from google.genai import types

    funciones = _herramientas(resumen, detalle)
    # Los alias «-latest» sólo existen en la versión v1beta de la API.
    cliente = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(api_version="v1beta", timeout=30000))
    config = types.GenerateContentConfig(
        system_instruction=INSTRUCCIONES, temperature=0.2,
        tools=list(funciones.values()),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    mensajes = [types.Content(role="user", parts=[types.Part(text=f"(Estrato seleccionado en el tablero: {estrato}) {pregunta}")])]
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
            try:
                resultado = f(**(ll.args or {})) if f else {"error": "herramienta inexistente"}
            except TypeError:
                resultado = {"error": "argumentos inválidos"}
            pasos.append({"herramienta": ll.name, "argumentos": dict(ll.args or {})})
            partes.append(types.Part.from_function_response(name=ll.name, response={"resultado": resultado}))
        mensajes.append(types.Content(role="user", parts=partes))
    return {"respuesta": "No pude completar la consulta en pocos pasos.", "pasos": pasos}
