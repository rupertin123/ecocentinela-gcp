"""Corte automático de facturación (Cloud Run, recibe push de Pub/Sub).

Cloud Billing publica en el tópico «alertas-presupuesto» cada actualización del
presupuesto. Si el costo bruto del mes supera el tope, este servicio DESVINCULA
la cuenta de facturación del proyecto: todo se detiene y no se puede gastar más.
Es el patrón documentado por Google en «Disable billing usage with notifications».

Prueba segura: un mensaje con {"simulacro": true} sólo verifica permisos.
"""
from __future__ import annotations

import base64
import json
import os

from fastapi import FastAPI, Request

PROYECTO = os.environ.get("GOOGLE_CLOUD_PROJECT", "local")
app = FastAPI(title="EcoCentinela - Corte de facturación")


def _log(msg: str, **c) -> None:
    print(json.dumps({"severity": c.pop("severity", "INFO"), "message": msg, "componente": "corte", **c}), flush=True)


def _billing():
    from googleapiclient import discovery

    return discovery.build("cloudbilling", "v1", cache_discovery=False)


def _rm():
    from googleapiclient import discovery

    return discovery.build("cloudresourcemanager", "v1", cache_discovery=False)


@app.get("/salud")
def salud():
    return {"estado": "ok"}


@app.post("/")
async def recibir(req: Request):
    sobre = await req.json()
    datos = json.loads(base64.b64decode(sobre["message"]["data"]).decode())
    if datos.get("simulacro"):
        permiso = "resourcemanager.projects.deleteBillingAssignment"
        r = _rm().projects().testIamPermissions(resource=PROYECTO, body={"permissions": [permiso]}).execute()
        ok = permiso in r.get("permissions", [])
        _log("simulacro de corte", tiene_permiso=ok, severity="INFO" if ok else "ERROR")
        return {"simulacro": True, "tiene_permiso": ok}

    costo, tope = float(datos.get("costAmount", 0)), float(datos.get("budgetAmount", 0))
    _log("actualización de presupuesto", costo=costo, tope=tope, moneda=datos.get("currencyCode"))
    if tope <= 0 or costo < tope:
        return {"accion": "ninguna", "costo": costo, "tope": tope}

    nombre = f"projects/{PROYECTO}"
    info = _billing().projects().getBillingInfo(name=nombre).execute()
    if not info.get("billingEnabled"):
        return {"accion": "ya estaba desactivada"}
    _billing().projects().updateBillingInfo(name=nombre, body={"billingAccountName": ""}).execute()
    _log("FACTURACIÓN DESACTIVADA: se superó el tope", costo=costo, tope=tope, severity="CRITICAL")
    return {"accion": "facturación desactivada"}
