"""El SQL generado por el modelo no debe poder escribir, leer ficheros ni salir del almacén."""

import pytest

from datos_abiertos_mcp.almacen import ConsultaRechazada, ErrorConsulta, validar_sql

TABLAS = {"paro_epa", "poblacion", "comunidades", "fuentes"}

PERMITIDAS = [
    "SELECT * FROM paro_epa",
    "select tasa_paro from paro_epa where codigo_ccaa = '13' order by periodo desc limit 1;",
    "WITH ult AS (SELECT max(periodo) p FROM paro_epa) SELECT * FROM paro_epa, ult WHERE periodo = ult.p",
    "SELECT c.nombre, p.habitantes FROM poblacion p JOIN comunidades c USING (codigo_ccaa)",
    "SELECT * FROM paro_epa UNION ALL SELECT * FROM paro_epa",
    "FROM paro_epa SELECT periodo",
    "SELECT * FROM main.paro_epa",
    "SELECT avg(tasa_paro) OVER (PARTITION BY codigo_ccaa ORDER BY periodo ROWS 3 PRECEDING) FROM paro_epa",
]

RECHAZADAS = [
    ("DROP TABLE paro_epa", "lectura"),
    ("DELETE FROM paro_epa", "lectura"),
    ("UPDATE paro_epa SET tasa_paro = 0", "lectura"),
    ("INSERT INTO paro_epa SELECT * FROM paro_epa", "lectura"),
    ("CREATE TABLE x AS SELECT 1", "lectura"),
    ("SELECT 1; DROP TABLE paro_epa", "una sentencia"),
    ("COPY paro_epa TO 'fuera.csv'", "lectura"),
    ("ATTACH 'otra.db' AS otra", "lectura"),
    ("INSTALL httpfs", "lectura"),
    ("PRAGMA database_list", "lectura"),
    ("SET threads = 64", "lectura"),
    ("SELECT * FROM read_csv('C:/Windows/win.ini')", "funciones de tabla"),
    ("SELECT * FROM 'secreto.csv'", "no existe"),
    ("SELECT * FROM glob('*')", "funciones de tabla"),
    ("SELECT * FROM duckdb_settings()", "funciones de tabla"),
    ("SELECT getenv('ANTHROPIC_API_KEY')", "getenv"),
    ("SELECT current_setting('home_directory')", "current_setting"),
    ("SELECT * FROM information_schema.tables", "Esquema no permitido"),
    ("SELECT * FROM otra_bd.main.paro_epa", "Esquema no permitido"),
    ("SELECT * FROM tabla_inventada", "no existe"),
    ("SELECT (SELECT count(*) FROM read_text('x'))", "funciones de tabla"),
    ("WITH x AS (DELETE FROM paro_epa RETURNING *) SELECT * FROM x", None),
    ("", "vacía"),
    ("SELEC * FRM paro_epa", None),
]


@pytest.mark.parametrize("sql", PERMITIDAS)
def test_consultas_de_lectura_permitidas(sql):
    validar_sql(sql, TABLAS)


@pytest.mark.parametrize(("sql", "motivo"), RECHAZADAS)
def test_consultas_peligrosas_rechazadas(sql, motivo):
    with pytest.raises(ConsultaRechazada, match=motivo):
        validar_sql(sql, TABLAS)


def test_devuelve_tablas_usadas_sin_ctes():
    _, tablas = validar_sql(
        "WITH t AS (SELECT * FROM paro_epa) SELECT * FROM t JOIN comunidades USING (codigo_ccaa)", TABLAS
    )
    assert tablas == ["comunidades", "paro_epa"]


def test_consulta_real_y_tablas_usadas(almacen):
    r = almacen.consultar(
        "SELECT c.nombre, p.tasa_paro FROM paro_epa p JOIN comunidades c USING (codigo_ccaa) "
        "WHERE periodo = '2026T4' AND sexo = 'Total' ORDER BY tasa_paro DESC"
    )
    assert r.columnas == ["nombre", "tasa_paro"]
    assert r.filas[0][0] == "Andalucía"
    assert r.tablas == ["comunidades", "paro_epa"]
    assert not r.truncado


def test_limite_de_filas(almacen):
    r = almacen.consultar("SELECT * FROM paro_epa", max_filas=10)
    assert len(r.filas) == 10 and r.truncado
    # no se puede pedir más que el máximo del servidor
    r = almacen.consultar("SELECT * FROM paro_epa", max_filas=10**9)
    assert len(r.filas) == almacen.max_filas


def test_timeout_interrumpe_consultas_lentas(ruta_db):
    from datos_abiertos_mcp.almacen import Almacen

    lento = Almacen(ruta_db, timeout_s=0.2)
    try:
        with pytest.raises(ErrorConsulta, match="límite"):
            lento.consultar("SELECT count(*) FROM paro_epa a, paro_epa b, paro_epa c, paro_epa d")
    finally:
        lento.cerrar()


def test_la_conexion_es_de_solo_lectura_aunque_se_salte_el_validador(almacen):
    with pytest.raises(Exception, match="(?i)read-only|read only"):
        almacen._con.execute("CREATE TABLE x (a INT)")
    with pytest.raises(Exception):  # noqa: B017 - cualquier error vale: lo importante es que no lea
        almacen._con.execute("SELECT * FROM read_text('pyproject.toml')").fetchall()
    with pytest.raises(Exception):  # noqa: B017
        almacen._con.execute("SET enable_external_access = true")


def test_errores_de_duckdb_se_explican(almacen):
    with pytest.raises(ErrorConsulta, match="DuckDB"):
        almacen.consultar("SELECT columna_que_no_existe FROM paro_epa")


def test_describir_tabla_incluye_valores_y_fuente(almacen):
    d = almacen.describir_tabla("paro_epa")
    columnas = {c["columna"]: c for c in d["columnas"]}
    assert columnas["sexo"]["valores"] == ["Hombres", "Mujeres", "Total"]
    assert columnas["anio"]["rango"] == [2002, 2026]
    assert "'00'" in columnas["codigo_ccaa"]["descripcion"]
    assert d["fuente"]["licencia"] == "CC BY 4.0"
    with pytest.raises(ConsultaRechazada):
        almacen.describir_tabla("duckdb_settings")
