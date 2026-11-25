"""Pipeline completo: descarga → limpieza → modelado en DuckDB.

Uso:
    uv run construir-datos                 # usa la caché de data/raw si existe
    uv run construir-datos --refrescar     # vuelve a descargar los datos del INE y la foto de precios
    uv run construir-datos --semanas 12    # semanas de histórico de carburantes
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta

import duckdb

from datos_abiertos_mcp.config import DB_PATH
from datos_abiertos_mcp.pipeline import carburantes, ine, modelos
from datos_abiertos_mcp.pipeline.esquema import FUENTES, TABLA_FUENTES, TABLAS, ddl

log = logging.getLogger("pipeline")

ANIO_INICIO = 2002


def lunes_anteriores(hasta: date, semanas: int) -> list[date]:
    """Los `semanas` lunes más recientes estrictamente anteriores a `hasta`."""
    ultimo = hasta - timedelta(days=1)
    ultimo -= timedelta(days=ultimo.weekday())
    return [ultimo - timedelta(weeks=i) for i in range(semanas)][::-1]


def _insertar(con: duckdb.DuckDBPyConnection, tabla: str, filas: list[tuple]) -> None:
    if not filas:
        raise RuntimeError(f"La tabla {tabla} se quedaría vacía; revisa la descarga")
    marcadores = ", ".join("?" * len(filas[0]))
    con.executemany(f"INSERT INTO {tabla} VALUES ({marcadores})", filas)
    log.info("  %-20s %8d filas", tabla, len(filas))


def construir(*, refrescar: bool = False, semanas: int = 26, hoy: date | None = None) -> None:
    hoy = hoy or date.today()
    inicio = time.perf_counter()
    anio_fin = hoy.year

    log.info("Descargando INE (EPA, población, IPC)...")
    rango = f"{ANIO_INICIO}0101:{anio_fin}1231"
    epa = ine.descargar_tabla(ine.TABLA_EPA_PARO, fecha=rango, nombre="epa_paro", refrescar=refrescar)
    ipc = ine.descargar_tabla(ine.TABLA_IPC_TASAS, fecha=rango, nombre="ipc_tasas", refrescar=refrescar)
    poblacion = []
    for anio in range(ANIO_INICIO, anio_fin + 1):
        try:
            poblacion += ine.descargar_tabla(
                ine.TABLA_ECP_POBLACION,
                fecha=f"{anio}0101",
                nombre=f"ecp_poblacion_{anio}",
                refrescar=refrescar and anio >= anio_fin - 1,
            )
        except RuntimeError as exc:  # el año en curso puede no estar publicado aún
            if anio < anio_fin:
                raise
            log.warning("Población %s no disponible: %s", anio, exc)

    log.info("Descargando carburantes (foto actual + %d lunes)...", semanas)
    actual = carburantes.descargar_actual(refrescar=refrescar)
    dias = [carburantes.descargar_dia(d) for d in lunes_anteriores(hoy, semanas)]

    log.info("Transformando y cargando en DuckDB...")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporal = DB_PATH.with_suffix(".tmp.duckdb")
    temporal.unlink(missing_ok=True)
    con = duckdb.connect(str(temporal))
    try:
        for tabla in [*TABLAS, TABLA_FUENTES]:
            for sentencia in ddl(tabla):
                con.execute(sentencia)

        _insertar(con, "comunidades", modelos.filas_comunidades())
        _insertar(con, "paro_epa", modelos.filas_paro(ine.observaciones(epa)))
        _insertar(con, "poblacion", modelos.filas_poblacion(ine.observaciones(poblacion)))
        _insertar(con, "ipc", modelos.filas_ipc(ine.observaciones(ipc)))
        _insertar(con, "gasolineras", carburantes.filas_estaciones(actual))

        # Histórico de carburantes: staging en formato largo y agregación en SQL.
        con.execute(
            "CREATE TEMP TABLE stg_precios (fecha DATE, codigo_ccaa VARCHAR, carburante VARCHAR, precio DOUBLE)"
        )
        for dia in dias:
            con.executemany("INSERT INTO stg_precios VALUES (?, ?, ?, ?)", carburantes.filas_precios_dia(dia))
        con.execute(
            """
            INSERT INTO carburantes_semanal
            SELECT fecha, COALESCE(codigo_ccaa, '00') AS codigo_ccaa, carburante,
                   round(avg(precio), 3), round(median(precio), 3),
                   min(precio), max(precio), count(*)::INTEGER
            FROM stg_precios
            GROUP BY GROUPING SETS ((fecha, carburante, codigo_ccaa), (fecha, carburante))
            ORDER BY ALL
            """
        )
        log.info(
            "  %-20s %8d filas",
            "carburantes_semanal",
            con.execute("SELECT count(*) FROM carburantes_semanal").fetchone()[0],
        )

        _cargar_fuentes(con)
        _validar(con)
        con.execute("CHECKPOINT")
    finally:
        con.close()

    os.replace(temporal, DB_PATH)
    log.info("Base de datos lista en %s (%.1f s)", DB_PATH, time.perf_counter() - inicio)


def _cargar_fuentes(con: duckdb.DuckDBPyConnection) -> None:
    hasta = {
        "paro_epa": "SELECT max(periodo) FROM paro_epa",
        "poblacion": "SELECT '1 de enero de ' || max(anio) FROM poblacion",
        "ipc": "SELECT strftime(max(fecha), '%Y-%m') FROM ipc",
        "gasolineras": "SELECT max(fecha_precios)::VARCHAR FROM gasolineras",
        "carburantes_semanal": "SELECT max(fecha)::VARCHAR FROM carburantes_semanal",
    }
    ahora = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for tabla in TABLAS:
        if tabla.fuente is None:
            continue
        f = FUENTES[tabla.fuente]
        datos_hasta = con.execute(hasta[tabla.nombre]).fetchone()[0]
        con.execute(
            "INSERT INTO fuentes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                tabla.nombre,
                f.organismo,
                f.conjunto,
                f.url_dataset,
                f.url_api,
                f.licencia,
                f.cita,
                datos_hasta,
                ahora,
            ],
        )


def _validar(con: duckdb.DuckDBPyConnection) -> None:
    """Comprobaciones de calidad: si alguna falla, el pipeline se detiene."""
    comprobaciones = {
        "códigos de comunidad desconocidos": """
            SELECT count(*) FROM (
              SELECT codigo_ccaa FROM paro_epa UNION ALL SELECT codigo_ccaa FROM poblacion
              UNION ALL SELECT codigo_ccaa FROM ipc UNION ALL SELECT codigo_ccaa FROM gasolineras
            ) WHERE codigo_ccaa NOT IN (SELECT codigo_ccaa FROM comunidades)""",
        "grupos de edad que no suman el total de población": """
            SELECT count(*) FROM (
              SELECT anio, codigo_ccaa, sexo,
                     sum(habitantes) FILTER (WHERE grupo_edad <> 'Total') AS suma,
                     max(habitantes) FILTER (WHERE grupo_edad = 'Total') AS total
              FROM poblacion GROUP BY ALL
            ) WHERE abs(suma - total) > 1""",
        "comunidades que no suman la población nacional": """
            SELECT count(*) FROM (
              SELECT anio, sum(habitantes) FILTER (WHERE codigo_ccaa <> '00') AS suma,
                     max(habitantes) FILTER (WHERE codigo_ccaa = '00') AS total
              FROM poblacion WHERE sexo = 'Total' AND grupo_edad = 'Total' GROUP BY anio
            ) WHERE abs(suma - total) > 1""",
        "tasas de paro fuera de rango": "SELECT count(*) FROM paro_epa WHERE tasa_paro NOT BETWEEN 0 AND 100",
        "precios de carburante absurdos": """
            SELECT count(*) FROM carburantes_semanal WHERE precio_medio NOT BETWEEN 0.3 AND 5""",
    }
    for descripcion, sql in comprobaciones.items():
        errores = con.execute(sql).fetchone()[0]
        if errores:
            raise RuntimeError(f"Validación fallida: {descripcion} ({errores} casos)")
    log.info("Validaciones de calidad superadas (%d)", len(comprobaciones))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--refrescar", action="store_true", help="vuelve a descargar los datos")
    parser.add_argument("--semanas", type=int, default=26, help="semanas de histórico de carburantes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    construir(refrescar=args.refrescar, semanas=args.semanas)


if __name__ == "__main__":
    main()
