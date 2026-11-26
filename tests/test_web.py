"""Demo web: la API responde con lo que necesita la página y aplica los límites de uso."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from datos_abiertos_mcp.agente import Paso, Respuesta
from datos_abiertos_mcp.web import app as web


class AgenteFalso:
    def __init__(self, coste=0.001):
        self.preguntas = []
        self.coste = coste

    async def preguntar(self, pregunta):
        self.preguntas.append(pregunta)
        return Respuesta(
            pregunta=pregunta,
            respuesta="La tasa de paro es del 10,18 %.\nFuente: INE",
            sql=["SELECT 1"],
            fuentes=[{"tabla": "paro_epa", "organismo": "INE"}],
            pasos=[Paso("consultar_sql", {"sql": "SELECT 1"}, True, {})],
            resultado_final={"columnas": ["tasa"], "filas": [[10.18]], "truncado": False},
            coste_usd=self.coste,
        )


def _cliente(monkeypatch, falso, **entorno):
    monkeypatch.setattr(web, "Almacen", lambda: NS(listar_tablas=lambda: [], cerrar=lambda: None))
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    c = TestClient(web.crear_app(agente_=falso))
    c.falso = falso
    return c


@pytest.fixture()
def cliente(monkeypatch):
    """Configuración por defecto de la demo: 1 pregunta propia por visitante y día."""
    with _cliente(monkeypatch, AgenteFalso()) as c:
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
    assert datos["restantes"]["dia"] == 0 and datos["de_cache"] is False


def test_una_pregunta_propia_por_visitante(cliente):
    assert cliente.post("/api/preguntar", json={"pregunta": "primera pregunta"}).status_code == 200
    r = cliente.post("/api/preguntar", json={"pregunta": "segunda pregunta"})
    assert r.status_code == 429 and "ejemplo" in r.json()["detail"]
    assert cliente.falso.preguntas == ["primera pregunta"]  # la segunda no llega al modelo


def test_respuestas_guardadas_no_gastan_cupo_ni_dinero(cliente):
    ejemplo = web.EJEMPLOS[0]
    assert cliente.post("/api/preguntar", json={"pregunta": ejemplo}).json()["de_cache"] is False
    # otra persona (u otra vez la misma) repite el ejemplo, escrito de otra forma
    for _ in range(3):
        r = cliente.post("/api/preguntar", json={"pregunta": ejemplo.upper().replace("¿", "")})
        assert r.status_code == 200 and r.json()["de_cache"] is True
    assert len(cliente.falso.preguntas) == 1
    # y el visitante conserva su pregunta propia
    r = cliente.post("/api/preguntar", json={"pregunta": "mi pregunta propia"})
    assert r.status_code == 200 and r.json()["restantes"]["dia"] == 0


def test_presupuesto_diario_pausa_la_demo(monkeypatch):
    caro = AgenteFalso(coste=0.10)
    with _cliente(
        monkeypatch,
        caro,
        DEMO_PREGUNTAS_POR_DIA_IP="100",
        DEMO_PREGUNTAS_POR_HORA_IP="100",
        DEMO_PRESUPUESTO_DIA_USD="0.15",
    ) as c:
        assert c.post("/api/preguntar", json={"pregunta": "pregunta uno"}).status_code == 200
        assert c.post("/api/preguntar", json={"pregunta": "pregunta dos"}).status_code == 200  # 0,20 $
        r = c.post("/api/preguntar", json={"pregunta": "pregunta tres"})
        assert r.status_code == 503 and "presupuesto" in r.json()["detail"]
        assert len(caro.preguntas) == 2
        # lo ya respondido se sigue sirviendo gratis
        assert c.post("/api/preguntar", json={"pregunta": "pregunta uno"}).status_code == 200


def test_presupuesto_se_reinicia_cada_dia():
    p = web.PresupuestoDiario(0.15)
    p.registrar(0.2, hoy="2026-11-26")
    assert p.agotado(hoy="2026-11-26")
    assert not p.agotado(hoy="2026-11-27")


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
