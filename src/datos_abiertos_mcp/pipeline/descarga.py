"""Descarga con caché en disco y manifiesto (URL, fecha, hash) para que el pipeline sea reproducible."""

from __future__ import annotations

import hashlib
import json
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from datos_abiertos_mcp.config import RAW_DIR, USER_AGENT

MANIFIESTO = "manifiesto.json"


def contexto_tls_heredado() -> ssl.SSLContext:
    """TLS 1.2 con cifrados RSA sin ECDHE, para servidores antiguos (p. ej. sedeaplicaciones.minetur).

    La verificación del certificado y del nombre del servidor sigue activa; solo se amplía
    la lista de cifrados aceptados.
    """
    ctx = ssl.create_default_context()
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers("DEFAULT:AES256-GCM-SHA384:AES128-GCM-SHA256:@SECLEVEL=1")
    return ctx


def _cliente(verify: ssl.SSLContext | bool = True) -> httpx.Client:
    return httpx.Client(
        verify=verify,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(180.0, connect=20.0),
        follow_redirects=True,
    )


def descargar_json(
    url: str,
    destino: Path,
    *,
    refrescar: bool = False,
    intentos: int = 4,
    tls: ssl.SSLContext | bool = True,
) -> Any:
    """Descarga `url` a `destino` (si no existe ya) y devuelve el JSON parseado.

    Guarda una entrada en el manifiesto de la carpeta con la URL, la fecha de descarga
    y el SHA-256, para saber exactamente qué datos alimentan la base de datos.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and not refrescar:
        return json.loads(destino.read_text(encoding="utf-8-sig"))

    ultimo_error: Exception | None = None
    with _cliente(tls) as cliente:
        for intento in range(intentos):
            try:
                resp = cliente.get(url)
                resp.raise_for_status()
                contenido = resp.content
                datos = json.loads(contenido.decode("utf-8-sig"))
                if isinstance(datos, dict) and "status" in datos and len(datos) == 1:
                    # El INE responde 200 con {"status": "..."} cuando rechaza la consulta.
                    raise RuntimeError(f"El servidor rechazó la consulta: {datos['status']}")
                break
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                ultimo_error = exc
                time.sleep(2 * (intento + 1))
        else:
            raise RuntimeError(f"No se pudo descargar {url}: {ultimo_error}") from ultimo_error

    destino.write_bytes(contenido)
    _registrar(destino, url, contenido)
    return datos


def _registrar(destino: Path, url: str, contenido: bytes) -> None:
    ruta_manifiesto = destino.parent / MANIFIESTO
    manifiesto = json.loads(ruta_manifiesto.read_text(encoding="utf-8")) if ruta_manifiesto.exists() else {}
    manifiesto[destino.name] = {
        "url": url,
        "descargado": datetime.now(UTC).isoformat(timespec="seconds"),
        "bytes": len(contenido),
        "sha256": hashlib.sha256(contenido).hexdigest(),
    }
    ruta_manifiesto.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")


def carpeta(fuente: str) -> Path:
    return RAW_DIR / fuente
