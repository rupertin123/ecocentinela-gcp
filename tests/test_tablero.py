"""Pruebas del tablero en modo local, con la verificación de Google reemplazada por una falsa."""
import importlib
import os
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

RAIZ = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture()
def tablero(monkeypatch):
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cliente-de-prueba.apps.googleusercontent.com")
    monkeypatch.setenv("ADMIN_EMAILS", "Productor@Ejemplo.com")
    sys.path.insert(0, os.path.join(RAIZ, "services", "tablero"))
    sys.modules.pop("main", None)
    t = importlib.import_module("main")
    sys.path.pop(0)
    sys.modules.pop("main", None)  # que las pruebas de la ingesta sigan importando su propio «main»
    from google.oauth2 import id_token

    def falso(token, _req, audiencia):
        assert audiencia == "cliente-de-prueba.apps.googleusercontent.com"
        if token == "vencido":
            raise ValueError("Token expired")
        return {"email": token, "email_verified": True}

    monkeypatch.setattr(id_token, "verify_oauth2_token", falso)
    t.DATOS_LOCALES["aplicaciones"].clear()
    t._cache.clear()
    return t


def _post(cli, token=None, **cuerpo):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return cli.post("/api/aplicaciones", json={"estrato": "medio", "producto": "tiametoxam_lambdacialotrina", **cuerpo}, headers=h)


def test_lectura_publica_y_cabeceras(tablero):
    cli = TestClient(tablero.app)
    r = cli.get("/api/resumen")
    assert r.status_code == 200 and len(r.json()["estratos"]) == 3
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert cli.get("/docs").status_code == 404  # la documentación automática no se publica


def test_registrar_exige_sesion(tablero):
    cli = TestClient(tablero.app)
    assert _post(cli).status_code == 401
    assert _post(cli, "vencido").status_code == 401
    assert _post(cli, "intruso@ejemplo.com").status_code == 403


def test_registro_valido_y_reglas(tablero):
    cli = TestClient(tablero.app)
    r = _post(cli, "productor@ejemplo.com")
    assert r.status_code == 201 and r.json()["descubierto_estimado"]
    assert _post(cli, "productor@ejemplo.com").status_code == 409  # dos veces el mismo día
    assert _post(cli, "productor@ejemplo.com", producto="clorantraniliprol").status_code == 422  # no es para chinches
    vieja = (tablero.modelo.ahora() - timedelta(days=9)).isoformat()
    assert _post(cli, "productor@ejemplo.com", aplicada_en=vieja, estrato="inferior",
                 producto="spinetoram_metoxifenocide").status_code == 422
    assert "registrada_por" not in str(cli.get("/api/estrato/medio").json())


def test_websocket_rechaza_otro_origen(tablero):
    cli = TestClient(tablero.app)
    with pytest.raises(Exception):
        with cli.websocket_connect("/ws/telemetria", headers={"origin": "https://malicioso.example"}) as ws:
            ws.receive_text()


def test_el_javascript_del_tablero_compila():
    """Un error de sintaxis en app.js deja el tablero vacío sin que falle ninguna API (pasó el 15/11/2026)."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node no está instalado")
    r = subprocess.run(["node", "--check", os.path.join(RAIZ, "services", "tablero", "static", "app.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
