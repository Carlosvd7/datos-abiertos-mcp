"""Genera el escaparate estático (GitHub Pages) con respuestas REALES del agente.

Ejecuta el agente, con la misma configuración que la demo, sobre las preguntas de ejemplo y
guarda en `docs/` la página en modo estático, sus estilos y scripts, y las respuestas en JSON.
La página publicada no tiene servidor ni clave de API: nadie puede gastar saldo con ella.

Uso (necesita la base de datos construida y ANTHROPIC_API_KEY; ~0,01 $ con Haiku 5.5):
    uv run python scripts/generar_escaparate.py
    uv run python scripts/generar_escaparate.py --solo-pagina   # copia la página, sin llamar a la API
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from mcp import Client

from datos_abiertos_mcp import agente
from datos_abiertos_mcp.almacen import Almacen
from datos_abiertos_mcp.web.app import EJEMPLOS, ESTATICOS, carga_respuesta

DESTINO = Path(__file__).resolve().parents[1] / "docs"


async def generar() -> dict[str, dict]:
    respuestas: dict[str, dict] = {}
    async with Client(agente.parametros_servidor_stdio()) as mcp:
        ag = agente.Agente(mcp, max_pasos=6, max_tokens=4000)  # igual que la demo
        for pregunta in EJEMPLOS:
            r = await ag.preguntar(pregunta)
            if r.estado != "ok":
                sys.exit(f"La pregunta «{pregunta}» terminó con estado {r.estado}; no se publica")
            respuestas[pregunta] = carga_respuesta(r)
            print(f"  OK  {pregunta} ({r.segundos} s, {r.coste_usd:.4f} $)")
    return respuestas


def copiar_pagina() -> None:
    (DESTINO / "static").mkdir(parents=True, exist_ok=True)
    for nombre in ("app.js", "estilos.css"):
        shutil.copyfile(ESTATICOS / nombre, DESTINO / "static" / nombre)
    html = (ESTATICOS / "index.html").read_text(encoding="utf-8")
    html = html.replace('<html lang="es">', '<html lang="es" data-modo="estatico">', 1)
    html = html.replace('"/static/', '"static/')
    (DESTINO / "index.html").write_text(html, encoding="utf-8")
    (DESTINO / ".nojekyll").write_text("", encoding="utf-8")


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if "--solo-pagina" in sys.argv:
        copiar_pagina()
        print(f"Página copiada en {DESTINO}")
        return
    respuestas = asyncio.run(generar())

    almacen = Almacen()
    info = {
        "modelo": agente.MODELO,
        "generado": date.today().strftime("%d/%m/%Y"),
        "ejemplos": EJEMPLOS,
        "tablas": almacen.listar_tablas(),
    }
    almacen.cerrar()

    copiar_pagina()
    for nombre, datos in (("info.json", info), ("respuestas.json", respuestas)):
        (DESTINO / nombre).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Escaparate listo en {DESTINO}")


if __name__ == "__main__":
    main()
