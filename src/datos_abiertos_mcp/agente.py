"""Agente text-to-SQL: Claude responde preguntas usando las herramientas del servidor MCP.

El bucle es manual (en vez del tool runner del SDK) para registrar cada llamada a las
herramientas: así la respuesta incluye el SQL exacto que se ejecutó, sus fuentes y los
datos de la gráfica, y se puede limitar el número de pasos.

Uso:
    uv run preguntar "¿Qué comunidad tiene más paro?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from anthropic import AsyncAnthropic
from mcp import Client, StdioServerParameters

MODELO = os.environ.get("CLAUDE_MODELO", "claude-opus-5-5")
ESFUERZO = os.environ.get("CLAUDE_ESFUERZO", "medium")
MAX_PASOS = 10
BETAS = ["server-side-fallback-2026-07-01"]

SISTEMA = """\
Eres un analista de datos que responde preguntas sobre datos públicos oficiales de España
usando SOLO las herramientas del servidor MCP «datos-abiertos-es» (DuckDB).

Cómo trabajar:
1. Si no conoces ya las tablas, llama a listar_tablas y describe las que vayas a usar.
2. Escribe SQL de DuckDB y ejecútalo con consultar_sql. Si da error, corrígelo y reintenta.
3. Si la pregunta pide una evolución o una comparación entre varias categorías, llama además
   a datos_grafica con la consulta que mejor la muestre.
4. Responde con los números que devolvió la consulta, nunca con datos de memoria. Si los datos
   no permiten responder, dilo claramente y explica qué sí hay.

Reglas de los datos:
- codigo_ccaa '00' es España (total nacional): no lo sumes con las comunidades; úsalo para el
  dato nacional. Para nombres, une con la tabla comunidades (columna nombre).
- Las tasas (paro, IPC) no se suman ni se promedian sin ponderar.
- Sin más detalle en la pregunta: sexo = 'Total', grupo_edad = 'Total' y el periodo más reciente
  disponible (di cuál es).
- Precios de carburantes en €/litro; «gasolina» sin más = gasolina_95, «diésel» = gasoleo_a.

Formato de la respuesta final (en español, breve, sin repetir el SQL):
- 1 a 4 frases con la respuesta y las cifras clave (con su unidad y periodo).
- Si hay varias filas relevantes, una lista corta o una tabla Markdown de 10 filas como mucho.
- Una última línea «Fuente: ...» con el organismo y el periodo de los datos.
"""


@dataclass
class Paso:
    herramienta: str
    argumentos: dict[str, Any]
    ok: bool
    resultado: Any


@dataclass
class Respuesta:
    pregunta: str
    respuesta: str
    sql: list[str] = field(default_factory=list)
    fuentes: list[dict[str, Any]] = field(default_factory=list)
    grafica: dict[str, Any] | None = None
    pasos: list[Paso] = field(default_factory=list)
    resultado_final: dict[str, Any] | None = None  # último resultado de consultar_sql correcto
    tokens_entrada: int = 0  # total de entrada (sin caché + lecturas + escrituras de caché)
    tokens_salida: int = 0
    tokens_cache_lectura: int = 0
    tokens_cache_escritura: int = 0
    segundos: float = 0.0
    modelo: str = MODELO
    modelos_servidos: list[str] = field(default_factory=list)  # por si actúa el fallback
    llamadas_modelo: int = 0
    estado: str = "ok"  # ok | rechazo | truncado | max_pasos

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


def _herramientas_anthropic(herramientas_mcp: list[Any]) -> list[dict[str, Any]]:
    """Convierte las herramientas MCP al formato de herramientas de la API de Claude."""
    return [
        {"name": h.name, "description": h.description or "", "input_schema": h.input_schema}
        for h in herramientas_mcp
    ]


def _contenido_resultado(resultado: Any) -> tuple[bool, Any, str]:
    """(ok, datos estructurados, texto para el modelo) a partir de un CallToolResult."""
    texto = "\n".join(getattr(c, "text", "") for c in resultado.content or [])
    if resultado.is_error:
        return False, texto, f"Error: {texto}"
    datos = resultado.structured_content
    if datos is None:
        return True, texto, texto
    return True, datos, json.dumps(datos, ensure_ascii=False, default=str)


class Agente:
    def __init__(self, mcp: Client, *, cliente: AsyncAnthropic | None = None, modelo: str = MODELO):
        self.mcp = mcp
        self.cliente = cliente or AsyncAnthropic()
        self.modelo = modelo
        self._herramientas: list[dict[str, Any]] | None = None

    async def herramientas(self) -> list[dict[str, Any]]:
        if self._herramientas is None:
            self._herramientas = _herramientas_anthropic((await self.mcp.list_tools()).tools)
        return self._herramientas

    async def preguntar(self, pregunta: str) -> Respuesta:
        inicio = time.perf_counter()
        r = Respuesta(pregunta=pregunta, respuesta="", modelo=self.modelo)
        mensajes: list[dict[str, Any]] = [{"role": "user", "content": pregunta}]
        herramientas = await self.herramientas()
        fuentes: dict[str, dict[str, Any]] = {}

        for _ in range(MAX_PASOS):
            mensaje = await self.cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=16000,
                system=SISTEMA,
                tools=herramientas,
                messages=mensajes,
                output_config={"effort": ESFUERZO},
                cache_control={"type": "ephemeral"},
                betas=BETAS,
                fallbacks="default",
            )
            lectura = mensaje.usage.cache_read_input_tokens or 0
            escritura = mensaje.usage.cache_creation_input_tokens or 0
            r.tokens_entrada += mensaje.usage.input_tokens + lectura + escritura
            r.tokens_cache_lectura += lectura
            r.tokens_cache_escritura += escritura
            r.tokens_salida += mensaje.usage.output_tokens
            r.llamadas_modelo += 1
            servido = getattr(mensaje, "model", self.modelo)
            if servido not in r.modelos_servidos:
                r.modelos_servidos.append(servido)
            mensajes.append({"role": "assistant", "content": mensaje.content})

            if mensaje.stop_reason == "refusal":
                r.respuesta, r.estado = "No puedo responder a esta pregunta.", "rechazo"
                break
            if mensaje.stop_reason == "max_tokens":
                r.respuesta, r.estado = "La respuesta se cortó por longitud.", "truncado"
                break
            if mensaje.stop_reason == "pause_turn":
                continue

            usos = [b for b in mensaje.content if b.type == "tool_use"]
            if not usos:
                r.respuesta = "\n".join(b.text for b in mensaje.content if b.type == "text").strip()
                break

            resultados = []
            for uso in usos:
                try:
                    llamada = await self.mcp.call_tool(uso.name, uso.input)
                    ok, datos, texto = _contenido_resultado(llamada)
                except Exception as exc:  # error de transporte: se informa al modelo
                    ok, datos, texto = False, str(exc), f"Error: {exc}"
                r.pasos.append(Paso(uso.name, dict(uso.input), ok, datos))
                if ok and isinstance(datos, dict) and "sql" in datos:
                    r.sql.append(datos["sql"])
                    for f in datos.get("fuentes", []):
                        fuentes[f["tabla"]] = f
                    if uso.name == "datos_grafica":
                        r.grafica = datos
                    else:
                        r.resultado_final = datos
                resultados.append(
                    {"type": "tool_result", "tool_use_id": uso.id, "content": texto, "is_error": not ok}
                )
            mensajes.append({"role": "user", "content": resultados})
        else:
            r.respuesta = "No he podido terminar el análisis en el número máximo de pasos."
            r.estado = "max_pasos"

        r.fuentes = list(fuentes.values())
        r.segundos = round(time.perf_counter() - inicio, 2)
        return r


def parametros_servidor_stdio() -> StdioServerParameters:
    """Lanza el servidor MCP de este paquete como subproceso (protocolo MCP por stdio).

    El SDK solo hereda variables de entorno seguras; se añaden las DATOS_* (ruta de la base de
    datos). La clave de la API de Claude nunca llega al servidor MCP: no la necesita.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "datos_abiertos_mcp.servidor_mcp"],
        env={k: v for k, v in os.environ.items() if k.startswith("DATOS_")},
    )


async def _main(pregunta: str, como_json: bool) -> None:
    async with Client(parametros_servidor_stdio()) as mcp:
        respuesta = await Agente(mcp).preguntar(pregunta)
    if como_json:
        print(json.dumps(respuesta.como_dict(), ensure_ascii=False, indent=2, default=str))
        return
    print(respuesta.respuesta)
    print("\n--- SQL ejecutado ---")
    for sql in respuesta.sql:
        print(sql + ";\n")
    print(
        f"--- {len(respuesta.pasos)} llamadas MCP · {respuesta.tokens_entrada} tokens de entrada · "
        f"{respuesta.tokens_salida} de salida · {respuesta.segundos} s ---"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Pregunta a los datos públicos de España.")
    parser.add_argument("pregunta")
    parser.add_argument("--json", action="store_true", help="salida completa en JSON")
    args = parser.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main(args.pregunta, args.json))


if __name__ == "__main__":
    main()
