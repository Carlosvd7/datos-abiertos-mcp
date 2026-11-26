"""Demo web: una página para preguntar y una API que ejecuta el agente en el servidor.

La clave de la API de Claude solo existe en el servidor (variable de entorno). El navegador
habla con /api/preguntar, que aplica límites de uso por IP y un tope diario global.

Uso local:
    uv run demo            # http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp import Client
from pydantic import BaseModel, Field

from datos_abiertos_mcp import agente
from datos_abiertos_mcp.almacen import Almacen

ESTATICOS = Path(__file__).parent / "static"
log = logging.getLogger("demo")

EJEMPLOS = [
    "¿Cuál es la tasa de paro en España según el último dato de la EPA?",
    "¿Qué comunidades tienen más paro? Muéstramelo en una gráfica.",
    "¿Cómo ha evolucionado la inflación en España desde 2020?",
    "¿En qué comunidad es más barata la gasolina 95 esta semana?",
    "¿Qué porcentaje de la población de España tiene 65 años o más?",
    "¿Cuántas gasolineras hay por cada 100.000 habitantes en cada comunidad?",
]


def _entero(nombre: str, defecto: int) -> int:
    try:
        return int(os.environ.get(nombre, defecto))
    except ValueError:
        return defecto


class LimitadorDeUso:
    """Ventanas deslizantes en memoria: por IP (hora y día) y global (día).

    Suficiente para una única instancia de la demo; con varias réplicas habría que
    llevarlo a un almacén compartido (p. ej. Redis).
    """

    def __init__(self, por_hora_ip: int, por_dia_ip: int, por_dia_total: int):
        self.por_hora_ip, self.por_dia_ip, self.por_dia_total = por_hora_ip, por_dia_ip, por_dia_total
        self._ip: dict[str, deque[float]] = defaultdict(deque)
        self._total: deque[float] = deque()

    @staticmethod
    def _purgar(cola: deque[float], ventana: float, ahora: float) -> None:
        while cola and cola[0] <= ahora - ventana:
            cola.popleft()

    def comprobar(self, ip: str, ahora: float | None = None) -> str | None:
        """Registra la petición y devuelve None, o el motivo si supera algún límite."""
        ahora = time.time() if ahora is None else ahora
        cola = self._ip[ip]
        self._purgar(cola, 86400, ahora)
        self._purgar(self._total, 86400, ahora)
        ultima_hora = sum(1 for t in cola if t > ahora - 3600)
        if len(self._total) >= self.por_dia_total:
            return "La demo ha alcanzado el máximo de preguntas de hoy. Vuelve mañana."
        if len(cola) >= self.por_dia_ip:
            return f"Has alcanzado el máximo de {self.por_dia_ip} preguntas por día."
        if ultima_hora >= self.por_hora_ip:
            return f"Has alcanzado el máximo de {self.por_hora_ip} preguntas por hora."
        cola.append(ahora)
        self._total.append(ahora)
        return None

    def restantes(self, ip: str, ahora: float | None = None) -> dict[str, int]:
        ahora = time.time() if ahora is None else ahora
        cola = self._ip.get(ip, deque())
        hora = sum(1 for t in cola if t > ahora - 3600)
        dia = sum(1 for t in cola if t > ahora - 86400)
        return {"hora": max(0, self.por_hora_ip - hora), "dia": max(0, self.por_dia_ip - dia)}


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=3, max_length=300)


def _ip_cliente(request: Request) -> str:
    if os.environ.get("DEMO_CONFIAR_PROXY") == "1":
        reenviada = request.headers.get("x-forwarded-for", "")
        if reenviada:
            return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def crear_app(*, cliente_mcp: Client | None = None, agente_: agente.Agente | None = None) -> FastAPI:
    """Crea la app. En tests se pueden inyectar el cliente MCP y el agente."""
    limitador = LimitadorDeUso(
        _entero("DEMO_PREGUNTAS_POR_HORA_IP", 5),
        _entero("DEMO_PREGUNTAS_POR_DIA_IP", 20),
        _entero("DEMO_PREGUNTAS_POR_DIA_TOTAL", 50),
    )
    concurrencia = asyncio.Semaphore(_entero("DEMO_CONCURRENCIA", 3))
    estado: dict[str, Any] = {}

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        almacen = Almacen()
        estado["tablas"] = almacen.listar_tablas()
        almacen.cerrar()
        if agente_ is not None:
            estado["agente"] = agente_
            yield
            return
        # Un único servidor MCP (subproceso stdio) para toda la demo, igual que en la CLI.
        async with cliente_mcp or Client(agente.parametros_servidor_stdio()) as mcp:
            estado["agente"] = agente.Agente(mcp)
            await estado["agente"].herramientas()
            yield

    app = FastAPI(title="Habla con los datos públicos de España", lifespan=ciclo_de_vida, docs_url=None)

    @app.middleware("http")
    async def cabeceras_seguridad(request: Request, call_next):
        respuesta = await call_next(request)
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'self' https://carlosmanuelvelazquez.netlify.app"
        )
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        return respuesta

    @app.get("/")
    async def inicio() -> FileResponse:
        return FileResponse(ESTATICOS / "index.html")

    @app.get("/api/info")
    async def info(request: Request) -> dict[str, Any]:
        return {
            "modelo": agente.MODELO,
            "ejemplos": EJEMPLOS,
            "tablas": estado.get("tablas", []),
            "restantes": limitador.restantes(_ip_cliente(request)),
        }

    @app.get("/api/salud")
    async def salud() -> dict[str, str]:
        return {"estado": "ok"}

    @app.post("/api/preguntar")
    async def preguntar(cuerpo: Pregunta, request: Request) -> JSONResponse:
        ip = _ip_cliente(request)
        motivo = limitador.comprobar(ip)
        if motivo:
            raise HTTPException(status_code=429, detail=motivo)
        async with concurrencia:  # como mucho N preguntas a la vez; el resto espera turno
            try:
                r = await asyncio.wait_for(estado["agente"].preguntar(cuerpo.pregunta.strip()), 120)
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail="La respuesta ha tardado demasiado.") from exc
            except Exception as exc:  # errores de la API de Claude, red...
                log.exception("Error al responder una pregunta")
                raise HTTPException(
                    status_code=502, detail="No se ha podido consultar el modelo. Inténtalo más tarde."
                ) from exc
        return JSONResponse(
            {
                "respuesta": r.respuesta,
                "sql": r.sql,
                "fuentes": r.fuentes,
                "grafica": r.grafica,
                "tabla": r.resultado_final
                and {
                    "columnas": r.resultado_final["columnas"],
                    "filas": r.resultado_final["filas"][:50],
                    "truncado": r.resultado_final["truncado"] or len(r.resultado_final["filas"]) > 50,
                },
                "pasos": [{"herramienta": p.herramienta, "ok": p.ok} for p in r.pasos],
                "segundos": r.segundos,
                "estado": r.estado,
                "restantes": limitador.restantes(ip),
            }
        )

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Demo web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()
    uvicorn.run(crear_app(), host=args.host, port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
