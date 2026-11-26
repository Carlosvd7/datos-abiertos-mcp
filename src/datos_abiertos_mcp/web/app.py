"""Demo web: una página para preguntar y una API que ejecuta el agente en el servidor.

La clave de la API de Claude solo existe en el servidor (variable de entorno). El navegador
habla con /api/preguntar, que protege el saldo con varias capas:

- 1 pregunta propia por visitante (IP) y día; las respuestas ya calculadas (como las de las
  preguntas de ejemplo) se sirven desde caché, sin coste y sin gastar el cupo.
- Presupuesto diario en dólares, calculado con los tokens reales de cada respuesta: al
  superarlo, la demo se pausa hasta el día siguiente (UTC), aunque alguien cambie de IP.
- Tope global de preguntas al día y peor caso por pregunta acotado (pasos y longitud).

Uso local:
    uv run demo            # http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
            if self.por_dia_ip == 1:
                return "Ya has hecho tu pregunta de hoy."
            return f"Has alcanzado el máximo de {self.por_dia_ip} preguntas por día."
        if ultima_hora >= self.por_hora_ip:
            if self.por_hora_ip == 1:
                return "Ya has hecho tu pregunta de esta hora."
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


class PresupuestoDiario:
    """Suma el coste real (USD) de las respuestas del día UTC y avisa al superar el límite."""

    def __init__(self, limite_usd: float):
        self.limite_usd = limite_usd
        self._dia = ""
        self.gastado = 0.0

    def _al_dia(self, hoy: str | None = None) -> None:
        hoy = hoy or datetime.now(UTC).date().isoformat()
        if hoy != self._dia:
            self._dia, self.gastado = hoy, 0.0

    def agotado(self, hoy: str | None = None) -> bool:
        self._al_dia(hoy)
        return self.gastado >= self.limite_usd

    def registrar(self, coste_usd: float, hoy: str | None = None) -> None:
        self._al_dia(hoy)
        self.gastado += coste_usd


class CacheRespuestas:
    """Respuestas ya calculadas, por pregunta normalizada (los datos no cambian hasta redesplegar)."""

    def __init__(self, maximo: int = 300):
        self.maximo = maximo
        self._datos: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @staticmethod
    def clave(pregunta: str) -> str:
        return re.sub(r"[\s¿?¡!.,;:]+", " ", pregunta.lower()).strip()

    def obtener(self, pregunta: str) -> dict[str, Any] | None:
        clave = self.clave(pregunta)
        if clave in self._datos:
            self._datos.move_to_end(clave)
            return self._datos[clave]
        return None

    def guardar(self, pregunta: str, respuesta: dict[str, Any]) -> None:
        self._datos[self.clave(pregunta)] = respuesta
        while len(self._datos) > self.maximo:
            self._datos.popitem(last=False)


def _decimal(nombre: str, defecto: float) -> float:
    try:
        return float(os.environ.get(nombre, defecto))
    except ValueError:
        return defecto


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=3, max_length=300)


def _ip_cliente(request: Request) -> str:
    if os.environ.get("DEMO_CONFIAR_PROXY") == "1":
        reenviada = request.headers.get("x-forwarded-for", "")
        if reenviada:
            return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def carga_respuesta(r: agente.Respuesta) -> dict[str, Any]:
    """Lo que la página necesita de una respuesta del agente (sin la trayectoria completa)."""
    filas = (r.resultado_final or {}).get("filas", [])
    return {
        "respuesta": r.respuesta,
        "sql": r.sql,
        "fuentes": r.fuentes,
        "grafica": r.grafica,
        "tabla": r.resultado_final
        and {
            "columnas": r.resultado_final["columnas"],
            "filas": filas[:50],
            "truncado": r.resultado_final["truncado"] or len(filas) > 50,
        },
        "pasos": [{"herramienta": p.herramienta, "ok": p.ok} for p in r.pasos],
        "segundos": r.segundos,
        "estado": r.estado,
    }


def crear_app(*, cliente_mcp: Client | None = None, agente_: agente.Agente | None = None) -> FastAPI:
    """Crea la app. En tests se pueden inyectar el cliente MCP y el agente."""
    limitador = LimitadorDeUso(
        _entero("DEMO_PREGUNTAS_POR_HORA_IP", 1),
        _entero("DEMO_PREGUNTAS_POR_DIA_IP", 1),
        _entero("DEMO_PREGUNTAS_POR_DIA_TOTAL", 50),
    )
    presupuesto = PresupuestoDiario(_decimal("DEMO_PRESUPUESTO_DIA_USD", 0.05))
    cache = CacheRespuestas()
    claves_ejemplos = {CacheRespuestas.clave(e) for e in EJEMPLOS}
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
            # En la demo, peor caso acotado: menos pasos y respuestas más cortas.
            estado["agente"] = agente.Agente(
                mcp,
                max_pasos=_entero("DEMO_MAX_PASOS", 6),
                max_tokens=_entero("DEMO_MAX_TOKENS", 4000),
            )
            await estado["agente"].herramientas()
            yield

    app = FastAPI(title="Habla con los datos públicos de España", lifespan=ciclo_de_vida, docs_url=None)

    @app.middleware("http")
    async def cabeceras_seguridad(request: Request, call_next):
        respuesta = await call_next(request)
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; "
            # Se puede incrustar en Hugging Face Spaces (que la muestra en un iframe) y en el portfolio.
            "frame-ancestors 'self' https://huggingface.co https://carlosmanuelvelazquez.netlify.app"
        )
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        return respuesta

    @app.get("/")
    async def inicio() -> FileResponse:
        # Sin caché: así los cambios de app.js/estilos.css (versionados con ?v=) llegan siempre.
        return FileResponse(ESTATICOS / "index.html", headers={"Cache-Control": "no-cache"})

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
        pregunta = cuerpo.pregunta.strip()
        guardada = cache.obtener(pregunta)
        if guardada is not None:  # sin coste: no cuenta para el cupo ni el presupuesto
            return JSONResponse({**guardada, "de_cache": True, "restantes": limitador.restantes(ip)})
        if presupuesto.agotado():
            raise HTTPException(
                status_code=503,
                detail="La demo ha agotado su presupuesto de hoy. Prueba las preguntas de ejemplo "
                "o vuelve mañana.",
            )
        # Los ejemplos no gastan el cupo del visitante: se calculan una vez y quedan en caché.
        es_ejemplo = cache.clave(pregunta) in claves_ejemplos
        motivo = None if es_ejemplo else limitador.comprobar(ip)
        if motivo:
            raise HTTPException(
                status_code=429, detail=f"{motivo} Puedes seguir probando las preguntas de ejemplo."
            )
        async with concurrencia:  # como mucho N preguntas a la vez; el resto espera turno
            try:
                r = await asyncio.wait_for(estado["agente"].preguntar(pregunta), 120)
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail="La respuesta ha tardado demasiado.") from exc
            except Exception as exc:  # errores de la API de Claude, red...
                log.exception("Error al responder una pregunta")
                raise HTTPException(
                    status_code=502, detail="No se ha podido consultar el modelo. Inténtalo más tarde."
                ) from exc
        presupuesto.registrar(r.coste_usd)
        log.info(
            "Pregunta respondida: %.5f USD (hoy %.4f / %.2f)",
            r.coste_usd,
            presupuesto.gastado,
            presupuesto.limite_usd,
        )
        datos = carga_respuesta(r)
        if r.estado == "ok":
            cache.guardar(pregunta, datos)
        return JSONResponse({**datos, "de_cache": False, "restantes": limitador.restantes(ip)})

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Demo web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(crear_app(), host=args.host, port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
