"""Precios de carburantes por gasolinera (Ministerio para la Transición Ecológica y el Reto Demográfico).

API REST pública: una foto con todas las estaciones terrestres y sus precios en vigor,
y un histórico por día (`EstacionesTerrestresHist/{dd-mm-aaaa}`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from datos_abiertos_mcp.pipeline.descarga import carpeta, contexto_tls_heredado, descargar_json

API = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes"

# Campo del JSON -> nombre de columna
CARBURANTES = {
    "Precio Gasolina 95 E5": "gasolina_95",
    "Precio Gasolina 98 E5": "gasolina_98",
    "Precio Gasoleo A": "gasoleo_a",
    "Precio Gasoleo Premium": "gasoleo_premium",
    "Precio Gases licuados del petróleo": "glp",
}


def _num(texto: str | None) -> float | None:
    if texto is None:
        return None
    texto = texto.strip().replace(",", ".")
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def _fecha_api(texto: str) -> date:
    # "25/11/2026 23:42:33" o "01/10/2026 0:00:00"
    return datetime.strptime(texto.split(" ")[0], "%d/%m/%Y").date()


def descargar_actual(*, refrescar: bool = False) -> dict[str, Any]:
    return descargar_json(
        f"{API}/EstacionesTerrestres/",
        carpeta("carburantes") / "estaciones_actual.json",
        refrescar=refrescar,
        tls=contexto_tls_heredado(),
    )


def descargar_dia(dia: date) -> dict[str, Any]:
    return descargar_json(
        f"{API}/EstacionesTerrestresHist/{dia:%d-%m-%Y}",
        carpeta("carburantes") / f"hist_{dia:%Y-%m-%d}.json",
        tls=contexto_tls_heredado(),
    )


def filas_estaciones(respuesta: dict[str, Any]) -> list[tuple]:
    """Una fila por gasolinera con sus precios en vigor en la fecha de la foto."""
    fecha = _fecha_api(respuesta["Fecha"])
    filas = []
    for e in respuesta["ListaEESSPrecio"]:
        filas.append(
            (
                e["IDEESS"],
                fecha,
                (e.get("Rótulo") or "").strip(),
                (e.get("Dirección") or "").strip(),
                (e.get("Municipio") or "").strip(),
                (e.get("Provincia") or "").strip(),
                e["IDCCAA"],
                (e.get("C.P.") or "").strip(),
                _num(e.get("Latitud")),
                _num(e.get("Longitud (WGS84)")),
                (e.get("Horario") or "").strip(),
                "público" if e.get("Tipo Venta") == "P" else "restringido",
                *(_num(e.get(campo)) for campo in CARBURANTES),
            )
        )
    return filas


def filas_precios_dia(respuesta: dict[str, Any]) -> list[tuple]:
    """(fecha, codigo_ccaa, carburante, precio) en formato largo, solo venta al público."""
    fecha = _fecha_api(respuesta["Fecha"])
    filas = []
    for e in respuesta["ListaEESSPrecio"]:
        if e.get("Tipo Venta") != "P":
            continue
        for campo, carburante in CARBURANTES.items():
            precio = _num(e.get(campo))
            if precio is not None and precio > 0:
                filas.append((fecha, e["IDCCAA"], carburante, precio))
    return filas
