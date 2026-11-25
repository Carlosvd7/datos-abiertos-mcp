"""Rutas y constantes compartidas por el pipeline, el servidor MCP y la demo."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def cargar_env(ruta: Path = ROOT / ".env") -> None:
    """Carga variables de un fichero .env local (sin sobrescribir las ya definidas).

    Así la clave de la API vive solo en .env (excluido de git) o en las variables del
    servidor de despliegue, nunca en el código ni en el navegador.
    """
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env()

DATA_DIR = Path(os.environ.get("DATOS_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("DATOS_DB", DATA_DIR / "datos.duckdb"))

USER_AGENT = "datos-abiertos-mcp/0.1 (+https://github.com/Carlosvd7/datos-abiertos-mcp)"
