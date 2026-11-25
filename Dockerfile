# Demo web + servidor MCP + datos, en una sola imagen.
# La base de datos se construye al crear la imagen descargando los datos oficiales,
# así que cada despliegue trae datos recientes. La clave de Claude NO va en la imagen:
# se pasa en tiempo de ejecución como variable de entorno (ANTHROPIC_API_KEY).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

# Usuario sin privilegios (Hugging Face Spaces exige uid 1000)
RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /home/app/proyecto

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/proyecto/.venv/bin:$PATH"

COPY --chown=app:app pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app src ./src
COPY --chown=app:app eval ./eval
RUN uv sync --frozen --no-dev

# Descarga y modelado de los datos (INE + Ministerio). Se borra la caché de descargas.
RUN construir-datos --semanas 26 && rm -rf data/raw

ENV PORT=7860 \
    DEMO_CONFIAR_PROXY=1
EXPOSE 7860
CMD ["sh", "-c", "demo --host 0.0.0.0 --port ${PORT}"]
