"""Base de datos DuckDB mínima con el esquema real, para tests sin red."""

from datetime import date

import duckdb
import pytest

from datos_abiertos_mcp.almacen import Almacen
from datos_abiertos_mcp.pipeline import modelos
from datos_abiertos_mcp.pipeline.esquema import TABLA_FUENTES, TABLAS, ddl


@pytest.fixture(scope="session")
def ruta_db(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("db") / "test.duckdb"
    con = duckdb.connect(str(ruta))
    for tabla in [*TABLAS, TABLA_FUENTES]:
        for sentencia in ddl(tabla):
            con.execute(sentencia)
    con.executemany("INSERT INTO comunidades VALUES (?, ?, ?)", modelos.filas_comunidades())
    filas_paro = []
    for anio in range(2002, 2027):
        for t in range(1, 5):
            for ccaa in ["00", "01", "13"]:
                for sexo in ["Total", "Hombres", "Mujeres"]:
                    tasa = 10 + (anio % 7) + t / 10 + (5 if ccaa == "01" else 0)
                    filas_paro.append(
                        (f"{anio}T{t}", anio, t, date(anio, 3 * t - 2, 1), ccaa, sexo, "Total", tasa)
                    )
    con.executemany("INSERT INTO paro_epa VALUES (?, ?, ?, ?, ?, ?, ?, ?)", filas_paro)
    con.executemany(
        "INSERT INTO poblacion VALUES (?, ?, ?, ?, ?)",
        [
            (2026, "00", "Total", "Total", 3000),
            (2026, "01", "Total", "Total", 2000),
            (2026, "13", "Total", "Total", 1000),
        ],
    )
    con.execute(
        "INSERT INTO fuentes VALUES ('paro_epa', 'INE', 'EPA', 'https://www.ine.es', 'https://api', "
        "'CC BY 4.0', 'Fuente: INE', '2026T4', now()::TIMESTAMP)"
    )
    con.close()
    return ruta


@pytest.fixture()
def almacen(ruta_db):
    a = Almacen(ruta_db)
    yield a
    a.cerrar()
