"""Acceso seguro y de solo lectura al almacén DuckDB.

Defensa en profundidad para SQL escrito por un modelo de lenguaje:

1. **Base de datos**: se abre en modo solo lectura, sin acceso a ficheros/red externos
   (`enable_external_access = false`), sin instalar ni cargar extensiones y con la
   configuración bloqueada, memoria y hilos limitados.
2. **Análisis del SQL** (sqlglot): una sola sentencia, que debe ser una consulta
   (SELECT / WITH / UNION...); solo puede leer las tablas publicadas y no puede llamar a
   funciones que lean ficheros, variables de entorno o el estado interno de DuckDB.
3. **Ejecución**: límite de tiempo (se interrumpe la consulta) y de filas devueltas.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

from datos_abiertos_mcp.config import DB_PATH

MAX_FILAS = 500
TIMEOUT_S = 10.0
MAX_LONGITUD_SQL = 8_000

# Prefijos/nombres de funciones prohibidas aunque la base de datos ya bloquee el acceso externo.
FUNCIONES_PROHIBIDAS_PREFIJOS = (
    "read_",
    "parquet_",
    "sniff_",
    "duckdb_",
    "pragma_",
    "glob",
    "getenv",
    "current_setting",
    "query",
    "iceberg_",
    "delta_",
    "load",
    "install",
    "ducklake",
    "postgres_",
    "sqlite_",
    "mysql_",
    "http",
    "s3",
    "checkpoint",
    "force_checkpoint",
    "enable_",
    "disable_",
    "set_",
    "which_secret",
)


class ConsultaRechazada(ValueError):
    """El SQL no cumple las reglas de seguridad. El mensaje explica por qué, en español."""


class ErrorConsulta(RuntimeError):
    """La consulta es válida pero DuckDB devolvió un error (o se agotó el tiempo)."""


@dataclass
class Resultado:
    sql: str
    columnas: list[str]
    filas: list[list[Any]]
    truncado: bool
    milisegundos: int
    tablas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict[str, Any]:
        return {
            "columnas": self.columnas,
            "filas": self.filas,
            "num_filas": len(self.filas),
            "truncado": self.truncado,
            "milisegundos": self.milisegundos,
            "tablas_usadas": self.tablas,
        }


def _a_json(valor: Any) -> Any:
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, float):
        return round(valor, 6)
    return valor


def validar_sql(sql: str, tablas_permitidas: set[str]) -> tuple[str, list[str]]:
    """Devuelve (sql normalizado, tablas leídas) o lanza `ConsultaRechazada`."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise ConsultaRechazada("La consulta está vacía.")
    if len(sql) > MAX_LONGITUD_SQL:
        raise ConsultaRechazada(f"La consulta supera {MAX_LONGITUD_SQL} caracteres.")
    try:
        sentencias = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise ConsultaRechazada(f"SQL no válido: {exc}") from exc
    if len(sentencias) != 1:
        raise ConsultaRechazada("Solo se permite una sentencia por consulta.")
    arbol = sentencias[0]
    if not isinstance(arbol, exp.Query):
        raise ConsultaRechazada(
            f"Solo se permiten consultas de lectura (SELECT/WITH); recibido: {arbol.key.upper()}."
        )

    for nodo in arbol.walk():
        if isinstance(nodo, (exp.DML, exp.DDL, exp.Command, exp.Pragma, exp.Set, exp.Copy)):
            raise ConsultaRechazada(f"Operación no permitida: {nodo.key.upper()}.")

    ctes = {cte.alias_or_name.lower() for cte in arbol.find_all(exp.CTE)}
    tablas: set[str] = set()
    for tabla in arbol.find_all(exp.Table):
        if not isinstance(tabla.this, exp.Identifier):
            raise ConsultaRechazada(
                "No se permiten funciones de tabla (read_csv, glob, range...) en FROM; "
                "consulta solo las tablas publicadas."
            )
        nombre = tabla.name.lower()
        esquema = (tabla.db or "").lower()
        catalogo = (tabla.catalog or "").lower()
        if esquema not in ("", "main") or catalogo not in ("", "datos"):
            raise ConsultaRechazada(f"Esquema no permitido: {tabla.sql(dialect='duckdb')}.")
        if nombre in ctes and not esquema:
            continue
        if nombre not in tablas_permitidas:
            raise ConsultaRechazada(
                f"La tabla '{tabla.name}' no existe o no está permitida. "
                f"Tablas disponibles: {', '.join(sorted(tablas_permitidas))}."
            )
        tablas.add(nombre)

    for funcion in arbol.find_all(exp.Func):
        nombre = (funcion.name if isinstance(funcion, exp.Anonymous) else funcion.sql_name()).lower()
        if nombre.startswith(FUNCIONES_PROHIBIDAS_PREFIJOS) or isinstance(funcion, exp.ReadCSV):
            raise ConsultaRechazada(f"La función {nombre}() no está permitida.")

    return sql, sorted(tablas)


class Almacen:
    """Conexión de solo lectura compartida (DuckDB es seguro entre hilos con cursores)."""

    def __init__(
        self, ruta: Path | str = DB_PATH, *, max_filas: int = MAX_FILAS, timeout_s: float = TIMEOUT_S
    ):
        ruta = Path(ruta)
        if not ruta.exists():
            raise FileNotFoundError(
                f"No existe la base de datos {ruta}. Ejecuta primero: uv run construir-datos"
            )
        self.max_filas = max_filas
        self.timeout_s = timeout_s
        self._con = duckdb.connect(
            str(ruta),
            read_only=True,
            config={
                "enable_external_access": False,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
                "allow_community_extensions": False,
                "max_memory": "512MB",
                "threads": 2,
                "lock_configuration": True,
            },
        )
        self.tablas = {
            fila[0]
            for fila in self._con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main' AND NOT temporary"
            ).fetchall()
        }

    def cerrar(self) -> None:
        self._con.close()

    # --- Consultas del modelo -------------------------------------------------------------
    def consultar(self, sql: str, *, max_filas: int | None = None) -> Resultado:
        sql, tablas = validar_sql(sql, self.tablas)
        limite = min(max_filas or self.max_filas, self.max_filas)
        cursor = self._con.cursor()
        temporizador = threading.Timer(self.timeout_s, cursor.interrupt)
        inicio = time.perf_counter()
        temporizador.start()
        try:
            cursor.execute(sql)
            columnas = [d[0] for d in cursor.description or []]
            filas = cursor.fetchmany(limite + 1)
        except duckdb.InterruptException as exc:
            raise ErrorConsulta(f"La consulta superó el límite de {self.timeout_s:.0f} s.") from exc
        except duckdb.Error as exc:
            raise ErrorConsulta(f"Error de DuckDB: {exc}") from exc
        finally:
            temporizador.cancel()
            cursor.close()
        truncado = len(filas) > limite
        return Resultado(
            sql=sql,
            columnas=columnas,
            filas=[[_a_json(v) for v in fila] for fila in filas[:limite]],
            truncado=truncado,
            milisegundos=int((time.perf_counter() - inicio) * 1000),
            tablas=tablas,
        )

    # --- Metadatos (consultas fijas, no pasan por el validador) ---------------------------
    def _interna(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        cursor = self._con.cursor()
        try:
            return cursor.execute(sql, params or []).fetchall()
        finally:
            cursor.close()

    def listar_tablas(self) -> list[dict[str, Any]]:
        filas = self._interna(
            """
            SELECT t.table_name, t.comment, t.estimated_size, f.organismo, f.datos_hasta
            FROM duckdb_tables() t LEFT JOIN fuentes f ON f.tabla = t.table_name
            WHERE t.schema_name = 'main' AND NOT t.temporary
            ORDER BY t.table_name
            """
        )
        return [
            {"tabla": n, "descripcion": c, "filas_aprox": s, "fuente": o, "datos_hasta": h}
            for n, c, s, o, h in filas
        ]

    def describir_tabla(self, tabla: str) -> dict[str, Any]:
        tabla = tabla.strip().lower()
        if tabla not in self.tablas:
            raise ConsultaRechazada(
                f"La tabla '{tabla}' no existe. Tablas disponibles: {', '.join(sorted(self.tablas))}."
            )
        comentario = self._interna(
            "SELECT comment FROM duckdb_tables() WHERE table_name = ? AND schema_name = 'main'", [tabla]
        )[0][0]
        columnas = self._interna(
            """SELECT column_name, data_type, comment FROM duckdb_columns()
               WHERE table_name = ? AND schema_name = 'main' ORDER BY column_index""",
            [tabla],
        )
        detalle = []
        for nombre, tipo, desc in columnas:
            info: dict[str, Any] = {"columna": nombre, "tipo": tipo, "descripcion": desc}
            if tipo == "VARCHAR":
                distintos = self._interna(f'SELECT count(DISTINCT "{nombre}") FROM {tabla}')[0][0]
                if distintos <= 25:
                    info["valores"] = [
                        v[0] for v in self._interna(f'SELECT DISTINCT "{nombre}" FROM {tabla} ORDER BY 1')
                    ]
            elif tipo in ("INTEGER", "BIGINT", "DOUBLE", "DATE"):
                minimo, maximo = self._interna(f'SELECT min("{nombre}"), max("{nombre}") FROM {tabla}')[0]
                info["rango"] = [_a_json(minimo), _a_json(maximo)]
            detalle.append(info)
        ejemplo = self.consultar(f"SELECT * FROM {tabla} LIMIT 3")
        fuente = self.fuentes([tabla])
        return {
            "tabla": tabla,
            "descripcion": comentario,
            "columnas": detalle,
            "ejemplo": {"columnas": ejemplo.columnas, "filas": ejemplo.filas},
            "fuente": fuente[0] if fuente else None,
        }

    def fuentes(self, tablas: list[str]) -> list[dict[str, Any]]:
        if not tablas:
            return []
        filas = self._interna(
            """SELECT tabla, organismo, conjunto, url_dataset, licencia, cita, datos_hasta
               FROM fuentes WHERE list_contains(?, tabla) ORDER BY tabla""",
            [tablas],
        )
        claves = ["tabla", "organismo", "conjunto", "url", "licencia", "cita", "datos_hasta"]
        return [dict(zip(claves, f, strict=True)) for f in filas]
