"""Transformaciones: de observaciones del INE a filas de las tablas finales."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from datos_abiertos_mcp.pipeline.ine import Observacion, normalizar_sexo

# Código INE de comunidad -> (nombre oficial INE, nombre de uso común)
COMUNIDADES: dict[str, tuple[str, str]] = {
    "00": ("Total Nacional", "España"),
    "01": ("Andalucía", "Andalucía"),
    "02": ("Aragón", "Aragón"),
    "03": ("Asturias, Principado de", "Asturias"),
    "04": ("Balears, Illes", "Islas Baleares"),
    "05": ("Canarias", "Canarias"),
    "06": ("Cantabria", "Cantabria"),
    "07": ("Castilla y León", "Castilla y León"),
    "08": ("Castilla - La Mancha", "Castilla-La Mancha"),
    "09": ("Cataluña", "Cataluña"),
    "10": ("Comunitat Valenciana", "Comunidad Valenciana"),
    "11": ("Extremadura", "Extremadura"),
    "12": ("Galicia", "Galicia"),
    "13": ("Madrid, Comunidad de", "Comunidad de Madrid"),
    "14": ("Murcia, Región de", "Región de Murcia"),
    "15": ("Navarra, Comunidad Foral de", "Navarra"),
    "16": ("País Vasco", "País Vasco"),
    "17": ("Rioja, La", "La Rioja"),
    "18": ("Ceuta", "Ceuta"),
    "19": ("Melilla", "Melilla"),
}

GRUPOS_EDAD_POBLACION = [
    ("0 a 15", 0, 15),
    ("16 a 24", 16, 24),
    ("25 a 44", 25, 44),
    ("45 a 64", 45, 64),
    ("65 y más", 65, 200),
]


def filas_comunidades() -> list[tuple[str, str, str]]:
    return [(codigo, oficial, comun) for codigo, (oficial, comun) in COMUNIDADES.items()]


def _trimestre(periodo: str) -> int:
    if len(periodo) == 2 and periodo[0] == "T" and periodo[1] in "1234":
        return int(periodo[1])
    raise ValueError(f"Periodo trimestral inesperado: {periodo!r}")


def filas_paro(obs: Iterable[Observacion]) -> list[tuple]:
    """(periodo, anio, trimestre, fecha_inicio, codigo_ccaa, sexo, grupo_edad, tasa_paro)"""
    filas = []
    for o in obs:
        sexo = o.dim("Sexo")
        edad = o.dim("Totales de edad", "Semiintervalos de edad", "Grupos de edad")
        if sexo is None or edad is None or o.valor is None:
            continue
        trimestre = _trimestre(o.periodo)
        filas.append(
            (
                f"{o.anio}T{trimestre}",
                o.anio,
                trimestre,
                o.fecha,
                o.codigo_ccaa,
                normalizar_sexo(sexo[0]),
                edad[0],
                round(o.valor, 2),
            )
        )
    return filas


def _edad_simple(o: Observacion) -> int | None:
    """Edad en años de una serie de edad simple; 100 para '100 y más'; None si no aplica."""
    simple = o.dim("Valores simples de edad")
    if simple is not None:
        return int(simple[1].removeprefix("Y"))
    semi = o.dim("Semiintervalos de edad")
    if semi is not None and semi[1] == "Y-GE100":
        return 100
    return None


def grupo_edad(edad: int) -> str:
    for nombre, desde, hasta in GRUPOS_EDAD_POBLACION:
        if desde <= edad <= hasta:
            return nombre
    raise ValueError(edad)


def filas_poblacion(obs: Iterable[Observacion]) -> list[tuple]:
    """(anio, codigo_ccaa, sexo, grupo_edad, habitantes) a 1 de enero.

    Los grupos de edad se construyen sumando las edades simples (0..99 y '100 y más');
    'Total' es el total publicado por el INE para todas las edades.
    """
    acumulado: dict[tuple, float] = defaultdict(float)
    for o in obs:
        if o.fecha.month != 1 or o.fecha.day != 1 or o.valor is None:
            continue
        sexo = normalizar_sexo(o.dim("Sexo")[0])
        if o.dim("Totales de edad") is not None:
            acumulado[(o.anio, o.codigo_ccaa, sexo, "Total")] += o.valor
            continue
        edad = _edad_simple(o)
        if edad is None:  # p. ej. '85 y más', que solaparía con las edades simples
            continue
        acumulado[(o.anio, o.codigo_ccaa, sexo, grupo_edad(edad))] += o.valor
    return [(*clave, int(round(valor))) for clave, valor in sorted(acumulado.items())]


TASAS_IPC = {"1": "variacion_mensual", "2": "variacion_anual", "5": "variacion_acumulada"}


def filas_ipc(obs: Iterable[Observacion]) -> list[tuple]:
    """(anio, mes, fecha, codigo_ccaa, variacion_mensual, variacion_anual, variacion_acumulada)"""
    por_mes: dict[tuple, dict[str, float | None]] = defaultdict(dict)
    for o in obs:
        tasa = o.dim("Índices y Tasas")
        if tasa is None or tasa[1] not in TASAS_IPC:
            continue
        mes = int(o.periodo.removeprefix("M"))
        por_mes[(o.anio, mes, o.fecha, o.codigo_ccaa)][TASAS_IPC[tasa[1]]] = o.valor
    return [
        (
            *clave,
            valores.get("variacion_mensual"),
            valores.get("variacion_anual"),
            valores.get("variacion_acumulada"),
        )
        for clave, valores in sorted(por_mes.items())
    ]
