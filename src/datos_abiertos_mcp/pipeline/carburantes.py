"""Precios de carburantes por gasolinera (Ministerio para la Transición Ecológica y el Reto Demográfico).

API REST pública: una foto con todas las estaciones terrestres y sus precios en vigor,
y un histórico por día (`EstacionesTerrestresHist/{dd-mm-aaaa}`).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
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


def asegurar_dia(dia: date) -> Path:
    """Descarga (si falta) el histórico de un día y devuelve la ruta del JSON en disco."""
    ruta = carpeta("carburantes") / f"hist_{dia:%Y-%m-%d}.json"
    if not ruta.exists():
        descargar_json(f"{API}/EstacionesTerrestresHist/{dia:%d-%m-%Y}", ruta, tls=contexto_tls_heredado())
    return ruta


def sql_semanal(rutas: list[Path]) -> str:
    """SQL que agrega los JSON diarios (≈11.500 gasolineras/día) directamente con DuckDB.

    Solo gasolineras de venta al público; los precios vienen como texto con coma decimal.
    Devuelve una fila por día, comunidad y carburante, más el total nacional ('00').
    """
    lista = ", ".join("'" + str(r).replace("\\", "/").replace("'", "''") + "'" for r in rutas)
    columnas = ", ".join(
        f"TRY_CAST(replace(e.\"{campo}\", ',', '.') AS DOUBLE) AS {nombre}"
        for campo, nombre in CARBURANTES.items()
    )
    return f"""
        WITH estaciones AS (
            SELECT strptime(split_part(Fecha, ' ', 1), '%d/%m/%Y')::DATE AS fecha,
                   e."IDCCAA" AS codigo_ccaa, {columnas}
            FROM (
                SELECT Fecha, unnest(ListaEESSPrecio) AS e
                FROM read_json([{lista}], maximum_object_size = 268435456)
            )
            WHERE e."Tipo Venta" = 'P'
        ),
        largo AS (
            UNPIVOT estaciones ON {", ".join(CARBURANTES.values())}
            INTO NAME carburante VALUE precio
        )
        SELECT fecha, COALESCE(codigo_ccaa, '00') AS codigo_ccaa, carburante,
               round(avg(precio), 3), round(median(precio), 3),
               min(precio), max(precio), count(*)::INTEGER
        FROM largo
        WHERE precio > 0
        GROUP BY GROUPING SETS ((fecha, carburante, codigo_ccaa), (fecha, carburante))
        ORDER BY ALL
    """


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
