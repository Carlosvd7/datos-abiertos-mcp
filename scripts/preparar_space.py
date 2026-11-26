"""Prepara una carpeta lista para subir como Space de Hugging Face (SDK Docker).

Copia los archivos versionados en git (nunca .env, datos ni resultados locales sin commit) y
antepone al README la cabecera YAML que Hugging Face necesita para saber que es un Space
Docker. El README del repositorio de GitHub no se modifica.

Uso:
    uv run python scripts/preparar_space.py <carpeta_destino>
    cd <carpeta_destino> && git push <remoto_del_space> main
"""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
from pathlib import Path

CABECERA = """---
title: Habla con los datos públicos de España
emoji: 📊
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Agente con servidor MCP sobre datos del INE y el Ministerio
---

"""


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    destino = Path(sys.argv[1]).resolve()
    if destino.exists() and any(p.name != ".git" for p in destino.iterdir()):
        sys.exit(f"{destino} no está vacía")
    raiz = Path(__file__).resolve().parents[1]
    archivo = subprocess.run(["git", "archive", "HEAD"], cwd=raiz, check=True, capture_output=True).stdout
    destino.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archivo)) as tar:
        tar.extractall(destino, filter="data")
    readme = destino / "README.md"
    readme.write_text(CABECERA + readme.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Space preparado en {destino}")


if __name__ == "__main__":
    main()
