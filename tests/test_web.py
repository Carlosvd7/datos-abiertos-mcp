"""Demo web: la API responde con lo que necesita la página y aplica los límites de uso."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from datos_abiertos_mcp.agente import Paso, Respuesta
from datos_abiertos_mcp.web import app as web


class AgenteFalso:
    def __init__(self):
        self.preguntas = []

    async def preguntar(self, pregunta):
        self.preguntas.append(pregunta)
        return Respuesta(
            pregunta=pregunta,
            respuesta="La tasa de paro es del 10,18 %.\nFuente: INE",
            sql=["SELECT 1"],
            fuentes=[{"tabla": "paro_epa", "organismo": "INE"}],
            pasos=[Paso("consultar_sql", {"sql": "SELECT 1"}, True, {})],
            resultado_final={"columnas": ["tasa"], "filas": [[10.18]], "truncado": False},
        )


@pytest.fixture()
def cliente(ruta_db, monkeypatch):
    monkeypatch.setattr(web, "Almacen", lambda: NS(listar_tablas=lambda: [], cerrar=lambda: None))
    monkeypatch.setenv("DEMO_PREGUNTAS_POR_HORA_IP", "2")
    falso = AgenteFalso()
    with TestClient(web.crear_app(agente_=falso)) as c:
        c.falso = falso
        yield c


def test_pagina_y_cabeceras_de_seguridad(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "Habla con los datos" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "sk-ant" not in r.text  # nada parecido a una clave en el HTML


def test_preguntar_devuelve_respuesta_sql_y_fuentes(cliente):
    r = cliente.post("/api/preguntar", json={"pregunta": "¿Paro en España?"})
    assert r.status_code == 200
    datos = r.json()
    assert datos["respuesta"].startswith("La tasa de paro")
    assert datos["sql"] == ["SELECT 1"]
    assert datos["tabla"]["filas"] == [[10.18]]
    assert datos["restantes"]["hora"] == 1


def test_limite_por_hora(cliente):
    for _ in range(2):
        assert cliente.post("/api/preguntar", json={"pregunta": "hola hola"}).status_code == 200
    r = cliente.post("/api/preguntar", json={"pregunta": "hola hola"})
    assert r.status_code == 429 and "por hora" in r.json()["detail"]
    assert len(cliente.falso.preguntas) == 2  # la tercera no llega al modelo


@pytest.mark.parametrize("pregunta", ["", "ab", "x" * 301])
def test_valida_longitud(cliente, pregunta):
    assert cliente.post("/api/preguntar", json={"pregunta": pregunta}).status_code == 422


def test_limitador_ventanas():
    lim = web.LimitadorDeUso(por_hora_ip=2, por_dia_ip=3, por_dia_total=4)
    t = 1_000_000.0
    assert lim.comprobar("a", t) is None
    assert lim.comprobar("a", t + 1) is None
    assert "por hora" in lim.comprobar("a", t + 2)
    assert lim.comprobar("a", t + 3700) is None  # pasada una hora
    assert "por día" in lim.comprobar("a", t + 7400)
    assert lim.comprobar("b", t + 7400) is None
    assert "máximo de preguntas de hoy" in lim.comprobar("c", t + 7401)
    assert lim.comprobar("c", t + 90000) is None  # pasado un día
