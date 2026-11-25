"""Ingesta de tablas del INE a través de su API JSON (servicios.ine.es/wstempus).

Cada serie del INE viene con metadatos (`tip=AM`): una lista de variables (territorio, sexo,
edad...) con su nombre y código. Aquí se convierten en filas planas listas para cargar.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from datos_abiertos_mcp.pipeline.descarga import carpeta, descargar_json

API = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA"

# Tablas usadas (id de tabla del INE)
TABLA_EPA_PARO = 65334  # EPA: tasas de paro por sexo, grupo de edad y comunidad autónoma
TABLA_ECP_POBLACION = 56940  # ECP: población residente por fecha, sexo y edad (CCAA)
TABLA_IPC_TASAS = 76140  # IPC: tasas de variación del índice general por CCAA

VARIABLES_TERRITORIO = {
    "Total Nacional",
    "Totales Territoriales",
    "Comunidades y Ciudades Autónomas",
}


@dataclass(frozen=True)
class Observacion:
    codigo_serie: str
    dimensiones: dict[str, tuple[str, str]]  # variable -> (nombre, código)
    fecha: date
    anio: int
    periodo: str
    valor: float | None

    @property
    def codigo_ccaa(self) -> str:
        for variable, (_, codigo) in self.dimensiones.items():
            if variable in VARIABLES_TERRITORIO:
                return codigo
        raise ValueError(f"La serie {self.codigo_serie} no tiene dimensión territorial")

    def dim(self, *variables: str) -> tuple[str, str] | None:
        for variable in variables:
            if variable in self.dimensiones:
                return self.dimensiones[variable]
        return None


def url_tabla(tabla: int, *, fecha: str | None = None, nult: int | None = None) -> str:
    params = ["tip=AM"]
    if fecha:
        params.append(f"date={fecha}")
    if nult:
        params.append(f"nult={nult}")
    return f"{API}/{tabla}?{'&'.join(params)}"


def descargar_tabla(tabla: int, *, fecha: str, nombre: str, refrescar: bool = False) -> Any:
    return descargar_json(
        url_tabla(tabla, fecha=fecha), carpeta("ine") / f"{nombre}.json", refrescar=refrescar
    )


def observaciones(series: Iterable[dict[str, Any]]) -> Iterator[Observacion]:
    """Aplana la respuesta del INE: una `Observacion` por serie y periodo."""
    for serie in series:
        dimensiones = {
            m["T3_Variable"]: (m["Nombre"].strip(), (m.get("Codigo") or "").strip())
            for m in serie["MetaData"]
        }
        for dato in serie.get("Data", []):
            valor = None if dato.get("Secreto") else dato.get("Valor")
            yield Observacion(
                codigo_serie=serie["COD"],
                dimensiones=dimensiones,
                fecha=date.fromisoformat(dato["Fecha"][:10]),
                anio=int(dato["Anyo"]),
                periodo=str(dato.get("T3_Periodo", "")).strip(),
                valor=None if valor is None else float(valor),
            )


def normalizar_sexo(nombre: str) -> str:
    return "Total" if nombre in {"Ambos sexos", "Total"} else nombre
