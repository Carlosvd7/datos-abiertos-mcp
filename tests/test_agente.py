"""El bucle del agente, con un Claude simulado (sin red ni API key) y el servidor MCP real."""

from types import SimpleNamespace as NS

import pytest
from mcp import Client

from datos_abiertos_mcp import servidor_mcp
from datos_abiertos_mcp.agente import Agente
from datos_abiertos_mcp.almacen import Almacen


def _uso(id_, nombre, entrada):
    return NS(type="tool_use", id=id_, name=nombre, input=entrada)


def _msg(contenido, stop):
    return NS(
        content=contenido,
        stop_reason=stop,
        usage=NS(
            input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0
        ),
    )


class ClaudeSimulado:
    def __init__(self, guion):
        self.guion = list(guion)
        self.peticiones = []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kwargs):
        self.peticiones.append(kwargs)
        return self.guion.pop(0)


@pytest.fixture()
def servidor(ruta_db, monkeypatch):
    a = Almacen(ruta_db)
    monkeypatch.setattr(servidor_mcp, "almacen", lambda: a)
    yield servidor_mcp.servidor
    a.cerrar()


SQL = "SELECT tasa_paro FROM paro_epa WHERE periodo='2026T4' AND codigo_ccaa='00' AND sexo='Total'"


async def test_registra_sql_fuentes_y_reintenta_tras_error(servidor):
    claude = ClaudeSimulado(
        [
            _msg([_uso("t1", "consultar_sql", {"sql": "DELETE FROM paro_epa"})], "tool_use"),
            _msg([_uso("t2", "consultar_sql", {"sql": SQL})], "tool_use"),
            _msg([NS(type="text", text="La tasa de paro es del 13,4 %.\nFuente: INE")], "end_turn"),
        ]
    )
    async with Client(servidor) as mcp:
        r = await Agente(mcp, cliente=claude).preguntar("¿Cuál es la tasa de paro en España?")

    assert r.respuesta.startswith("La tasa de paro es del 13,4 %")
    assert r.sql == [SQL]  # solo el SQL que se ejecutó con éxito
    assert [p.ok for p in r.pasos] == [False, True]
    assert r.resultado_final["filas"] == [[13.4]]
    assert r.fuentes[0]["tabla"] == "paro_epa"
    assert r.tokens_entrada == 300 and r.tokens_salida == 60

    # el error de seguridad se devolvió al modelo como tool_result con is_error
    resultado_error = claude.peticiones[1]["messages"][2]["content"][0]
    assert resultado_error["is_error"] and "lectura" in resultado_error["content"]
    # las herramientas que ve Claude son las del servidor MCP
    nombres = {h["name"] for h in claude.peticiones[0]["tools"]}
    assert nombres == {"listar_tablas", "describir_tabla", "consultar_sql", "datos_grafica"}


async def test_para_tras_el_maximo_de_pasos(servidor, monkeypatch):
    monkeypatch.setattr("datos_abiertos_mcp.agente.MAX_PASOS", 2)
    claude = ClaudeSimulado([_msg([_uso(f"t{i}", "listar_tablas", {})], "tool_use") for i in range(2)])
    async with Client(servidor) as mcp:
        r = await Agente(mcp, cliente=claude).preguntar("bucle")
    assert "máximo de pasos" in r.respuesta and r.estado == "max_pasos"
