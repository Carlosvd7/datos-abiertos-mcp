"""Rutas y constantes compartidas por el pipeline, el servidor MCP y la demo."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DATOS_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("DATOS_DB", DATA_DIR / "datos.duckdb"))

USER_AGENT = "datos-abiertos-mcp/0.1 (+https://github.com/Carlosvd7/datos-abiertos-mcp)"
