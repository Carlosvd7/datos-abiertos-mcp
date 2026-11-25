"""Pruebas del servidor MCP a través de un cliente MCP real (conexión en proceso)."""

import pytest
from mcp import Client

from datos_abiertos_mcp import servidor_mcp
from datos_abiertos_mcp.almacen import Almacen


@pytest.fixture()
def servidor(ruta_db, monkeypatch):
    a = Almacen(ruta_db)
    monkeypatch.setattr(servidor_mcp, "almacen", lambda: a)
    yield servidor_mcp.servidor
    a.cerrar()


async def test_expone_cuatro_herramientas_de_solo_lectura(servidor):
    async with Client(servidor) as cliente:
        herramientas = (await cliente.list_tools()).tools
    nombres = {h.name for h in herramientas}
    assert nombres == {"listar_tablas", "describir_tabla", "consultar_sql", "datos_grafica"}
    assert all(h.annotations.read_only_hint for h in herramientas)
    assert "codigo_ccaa '00'" in servidor_mcp.INSTRUCCIONES


async def test_consultar_sql_devuelve_resultado_y_fuente(servidor):
    async with Client(servidor) as cliente:
        r = await cliente.call_tool(
            "consultar_sql",
            {
                "sql": "SELECT tasa_paro FROM paro_epa WHERE periodo='2026T4' AND codigo_ccaa='13' AND sexo='Total'"
            },
        )
    assert not r.is_error
    datos = r.structured_content
    assert datos["filas"] == [[13.4]]
    assert datos["fuentes"][0]["tabla"] == "paro_epa"


async def test_sql_peligroso_devuelve_error_explicado(servidor):
    async with Client(servidor) as cliente:
        r = await cliente.call_tool("consultar_sql", {"sql": "DROP TABLE paro_epa"})
    assert r.is_error
    assert "lectura" in r.content[0].text


async def test_datos_grafica(servidor):
    async with Client(servidor) as cliente:
        r = await cliente.call_tool(
            "datos_grafica",
            {
                "sql": "SELECT periodo, tasa_paro FROM paro_epa WHERE codigo_ccaa='00' AND sexo='Total' "
                "AND anio = 2026 ORDER BY periodo",
                "eje_x": "periodo",
                "series": ["tasa_paro"],
                "tipo": "lineas",
            },
        )
    g = r.structured_content
    assert g["etiquetas"] == ["2026T1", "2026T2", "2026T3", "2026T4"]
    assert len(g["series"][0]["valores"]) == 4


async def test_datos_grafica_columna_inexistente(servidor):
    async with Client(servidor) as cliente:
        r = await cliente.call_tool(
            "datos_grafica", {"sql": "SELECT periodo FROM paro_epa", "eje_x": "periodo", "series": ["x"]}
        )
    assert r.is_error and "no encontradas" in r.content[0].text
