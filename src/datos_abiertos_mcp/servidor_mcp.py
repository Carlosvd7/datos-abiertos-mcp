"""Servidor MCP «datos-abiertos-es»: datos públicos de España en DuckDB, solo lectura.

Herramientas:
- listar_tablas: qué datos hay, de dónde vienen y hasta qué fecha.
- describir_tabla: columnas, significado, valores posibles, rango y ejemplo.
- consultar_sql: ejecuta un SELECT validado (sin escritura, sin ficheros, con límites).
- datos_grafica: ejecuta un SELECT y devuelve los datos listos para una gráfica.

Uso:
    uv run servidor-mcp                    # stdio (Claude Desktop, Claude Code)
    uv run servidor-mcp --http --port 8001 # Streamable HTTP en http://127.0.0.1:8001/mcp
"""

from __future__ import annotations

import argparse
from functools import lru_cache
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from datos_abiertos_mcp.almacen import Almacen, ConsultaRechazada, ErrorConsulta

INSTRUCCIONES = """\
Datos públicos oficiales de España (INE y Ministerio para la Transición Ecológica) en DuckDB.
Flujo recomendado: listar_tablas → describir_tabla (de las tablas que vayas a usar) → consultar_sql.
Reglas de los datos:
- codigo_ccaa '00' es el total nacional; no lo sumes con las comunidades. Usa la tabla
  comunidades para traducir códigos a nombres.
- Las tasas (paro, IPC) no se suman ni se promedian sin ponderar; usa el dato de '00' para España.
- Filtra sexo = 'Total' y grupo_edad = 'Total' cuando la pregunta no especifique.
- Si la pregunta no dice el periodo, usa el más reciente disponible y dilo en la respuesta.
Cita siempre la fuente (organismo y periodo) que devuelve consultar_sql.
"""

SOLO_LECTURA = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

servidor = MCPServer(
    name="datos-abiertos-es",
    title="Datos públicos de España",
    instructions=INSTRUCCIONES,
    version="0.1.0",
)


@lru_cache(maxsize=1)
def almacen() -> Almacen:
    return Almacen()


def _ejecutar(sql: str, max_filas: int) -> dict[str, Any]:
    try:
        resultado = almacen().consultar(sql, max_filas=max_filas)
    except (ConsultaRechazada, ErrorConsulta) as exc:
        raise ToolError(str(exc)) from exc
    return {
        "sql": resultado.sql,
        **resultado.como_dict(),
        "fuentes": almacen().fuentes(resultado.tablas),
    }


@servidor.tool(annotations=SOLO_LECTURA)
def listar_tablas() -> dict[str, Any]:
    """Lista las tablas disponibles con su descripción, tamaño aproximado, fuente y último periodo."""
    return {"tablas": almacen().listar_tablas()}


@servidor.tool(annotations=SOLO_LECTURA)
def describir_tabla(tabla: str) -> dict[str, Any]:
    """Describe una tabla: columnas, tipos, significado, valores posibles (texto), rangos
    (números y fechas), 3 filas de ejemplo y su fuente/licencia. Úsala antes de escribir SQL."""
    try:
        return almacen().describir_tabla(tabla)
    except ConsultaRechazada as exc:
        raise ToolError(str(exc)) from exc


@servidor.tool(annotations=SOLO_LECTURA)
def consultar_sql(sql: str, max_filas: int = 100) -> dict[str, Any]:
    """Ejecuta UNA consulta SQL de solo lectura (dialecto DuckDB: SELECT o WITH ... SELECT)
    sobre las tablas publicadas. Límite: 10 s y como mucho 500 filas (`max_filas`, por defecto
    100). Devuelve columnas, filas, si se truncó el resultado y las fuentes de las tablas usadas.
    Cualquier escritura, lectura de ficheros o tabla no publicada se rechaza."""
    return _ejecutar(sql, max_filas)


@servidor.tool(annotations=SOLO_LECTURA)
def datos_grafica(
    sql: str,
    eje_x: str,
    series: list[str],
    tipo: Literal["barras", "lineas"] = "barras",
    titulo: str = "",
) -> dict[str, Any]:
    """Ejecuta una consulta de solo lectura y devuelve los datos con forma de gráfica:
    `eje_x` es la columna de categorías o fechas y `series` las columnas numéricas a dibujar
    (máximo 5). Úsala para evoluciones temporales (lineas) o comparaciones (barras)."""
    if not 1 <= len(series) <= 5:
        raise ToolError("Indica entre 1 y 5 columnas en `series`.")
    datos = _ejecutar(sql, 500)
    columnas = datos["columnas"]
    faltan = [c for c in [eje_x, *series] if c not in columnas]
    if faltan:
        raise ToolError(f"Columnas no encontradas en el resultado: {faltan}. Columnas: {columnas}")
    ix = columnas.index(eje_x)
    return {
        "tipo": tipo,
        "titulo": titulo,
        "eje_x": eje_x,
        "etiquetas": [fila[ix] for fila in datos["filas"]],
        "series": [
            {"nombre": s, "valores": [fila[columnas.index(s)] for fila in datos["filas"]]} for s in series
        ],
        "truncado": datos["truncado"],
        "sql": datos["sql"],
        "fuentes": datos["fuentes"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--http", action="store_true", help="servir por Streamable HTTP en vez de stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    almacen()  # falla pronto y con un mensaje claro si no existe la base de datos
    if args.http:
        servidor.run("streamable-http", host=args.host, port=args.port, stateless_http=True)
    else:
        servidor.run()


if __name__ == "__main__":
    main()
