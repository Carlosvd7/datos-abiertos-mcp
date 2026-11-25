"""Esquema del almacén DuckDB: tablas, descripciones de tablas/columnas y fuentes.

Las descripciones se guardan como COMMENT en DuckDB; el servidor MCP las lee de ahí
para explicarle al modelo qué significa cada columna.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Columna:
    nombre: str
    tipo: str
    descripcion: str


@dataclass(frozen=True)
class Tabla:
    nombre: str
    descripcion: str
    columnas: list[Columna]
    clave: list[str] = field(default_factory=list)
    fuente: str | None = None  # clave en FUENTES


@dataclass(frozen=True)
class Fuente:
    organismo: str
    conjunto: str
    url_dataset: str
    url_api: str
    licencia: str
    cita: str


INE_LICENCIA = "CC BY 4.0 (aviso legal del INE: https://www.ine.es/dyngs/AYU/index.htm?cid=125)"
INE_CITA = "Elaboración propia con datos extraídos del sitio web del INE: www.ine.es"

FUENTES: dict[str, Fuente] = {
    "ine_epa": Fuente(
        organismo="Instituto Nacional de Estadística (INE)",
        conjunto="Encuesta de Población Activa (EPA). Tabla 65334: tasas de paro por "
        "distintos grupos de edad, sexo y comunidad autónoma",
        url_dataset="https://www.ine.es/jaxiT3/Tabla.htm?t=65334",
        url_api="https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/65334",
        licencia=INE_LICENCIA,
        cita=INE_CITA,
    ),
    "ine_ecp": Fuente(
        organismo="Instituto Nacional de Estadística (INE)",
        conjunto="Estadística Continua de Población (ECP). Tabla 56940: población residente "
        "por fecha, sexo y edad (comunidades autónomas)",
        url_dataset="https://www.ine.es/jaxiT3/Tabla.htm?t=56940",
        url_api="https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/56940",
        licencia=INE_LICENCIA,
        cita=INE_CITA,
    ),
    "ine_ipc": Fuente(
        organismo="Instituto Nacional de Estadística (INE)",
        conjunto="Índice de Precios de Consumo (IPC). Tabla 76140: tasa de variación del "
        "índice general por comunidades autónomas",
        url_dataset="https://www.ine.es/jaxiT3/Tabla.htm?t=76140",
        url_api="https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/76140",
        licencia=INE_LICENCIA,
        cita=INE_CITA,
    ),
    "miteco_carburantes": Fuente(
        organismo="Ministerio para la Transición Ecológica y el Reto Demográfico",
        conjunto="Precios de carburantes en las estaciones de servicio (Geoportal de gasolineras)",
        url_dataset="https://geoportalgasolineras.es/",
        url_api="https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/",
        licencia="Información del sector público reutilizable según la Ley 37/2007 "
        "(citar la fuente y la fecha de actualización, sin alterar los datos)",
        cita="Fuente: Ministerio para la Transición Ecológica y el Reto Demográfico, "
        "Geoportal de gasolineras",
    ),
}

C = Columna
CODIGO_CCAA = C(
    "codigo_ccaa",
    "VARCHAR",
    "Código INE de la comunidad autónoma ('01'..'19'). '00' = total nacional (España); "
    "no sumes '00' con las comunidades. Únelo con comunidades.codigo_ccaa para el nombre.",
)

TABLAS: list[Tabla] = [
    Tabla(
        "comunidades",
        "Catálogo de comunidades y ciudades autónomas con su código INE. Incluye '00' = España.",
        [
            C("codigo_ccaa", "VARCHAR", "Código INE de dos cifras. '00' = total nacional."),
            C(
                "nombre_oficial",
                "VARCHAR",
                "Nombre tal como lo publica el INE (p. ej. 'Madrid, Comunidad de').",
            ),
            C(
                "nombre",
                "VARCHAR",
                "Nombre de uso común (p. ej. 'Comunidad de Madrid', 'Islas Baleares', 'España').",
            ),
        ],
        clave=["codigo_ccaa"],
    ),
    Tabla(
        "paro_epa",
        "Tasa de paro trimestral (%) de la Encuesta de Población Activa, por comunidad, sexo y "
        "grupo de edad, desde 2002. Para el total de una comunidad filtra sexo = 'Total' y "
        "grupo_edad = 'Total'. Las tasas no se pueden sumar ni promediar sin ponderar.",
        [
            C("periodo", "VARCHAR", "Trimestre en formato 'AAAATn', p. ej. '2026T3'."),
            C("anio", "INTEGER", "Año."),
            C("trimestre", "INTEGER", "Trimestre (1 a 4)."),
            C("fecha_inicio", "DATE", "Primer día del trimestre."),
            CODIGO_CCAA,
            C("sexo", "VARCHAR", "'Total', 'Hombres' o 'Mujeres'."),
            C(
                "grupo_edad",
                "VARCHAR",
                "'Total', 'Menores de 25 años', '25 y más años', 'De 55 y más años', "
                "'De 16 a 19 años', 'De 20 a 24 años' o 'De 25 a 54 años' (algunos grupos se solapan).",
            ),
            C("tasa_paro", "DOUBLE", "Tasa de paro en porcentaje (parados / activos × 100)."),
        ],
        clave=["periodo", "codigo_ccaa", "sexo", "grupo_edad"],
        fuente="ine_epa",
    ),
    Tabla(
        "poblacion",
        "Población residente a 1 de enero de cada año (desde 2002) por comunidad, sexo y grupo "
        "de edad. Para el total usa sexo = 'Total' y grupo_edad = 'Total'; los grupos de edad "
        "(sin 'Total') suman el total.",
        [
            C("anio", "INTEGER", "Año de referencia (población a 1 de enero de ese año)."),
            CODIGO_CCAA,
            C("sexo", "VARCHAR", "'Total', 'Hombres' o 'Mujeres'."),
            C(
                "grupo_edad",
                "VARCHAR",
                "'Total', '0 a 15', '16 a 24', '25 a 44', '45 a 64' o '65 y más'.",
            ),
            C("habitantes", "BIGINT", "Número de personas residentes."),
        ],
        clave=["anio", "codigo_ccaa", "sexo", "grupo_edad"],
        fuente="ine_ecp",
    ),
    Tabla(
        "ipc",
        "Inflación: tasas de variación (%) del índice general de precios de consumo, mensual, por "
        "comunidad, desde enero de 2002. La inflación interanual es variacion_anual.",
        [
            C("anio", "INTEGER", "Año."),
            C("mes", "INTEGER", "Mes (1 a 12)."),
            C("fecha", "DATE", "Primer día del mes."),
            CODIGO_CCAA,
            C("variacion_mensual", "DOUBLE", "Variación respecto al mes anterior, en %."),
            C(
                "variacion_anual",
                "DOUBLE",
                "Variación respecto al mismo mes del año anterior (inflación interanual), en %.",
            ),
            C(
                "variacion_acumulada",
                "DOUBLE",
                "Variación en lo que va de año (respecto a diciembre anterior), en %.",
            ),
        ],
        clave=["anio", "mes", "codigo_ccaa"],
        fuente="ine_ipc",
    ),
    Tabla(
        "gasolineras",
        "Foto de todas las gasolineras de venta al público de España con sus precios (€/litro) en "
        "vigor en la fecha de descarga. Precio NULL = esa gasolinera no vende ese carburante.",
        [
            C("id_estacion", "VARCHAR", "Identificador de la estación en el Geoportal."),
            C("fecha_precios", "DATE", "Fecha de la foto de precios."),
            C(
                "rotulo",
                "VARCHAR",
                "Marca o rótulo de la gasolinera (p. ej. 'REPSOL', 'CEPSA', 'BALLENOIL').",
            ),
            C("direccion", "VARCHAR", "Dirección."),
            C("municipio", "VARCHAR", "Municipio."),
            C("provincia", "VARCHAR", "Provincia en mayúsculas (p. ej. 'MADRID', 'BARCELONA')."),
            CODIGO_CCAA,
            C("codigo_postal", "VARCHAR", "Código postal."),
            C("latitud", "DOUBLE", "Latitud WGS84."),
            C("longitud", "DOUBLE", "Longitud WGS84."),
            C("horario", "VARCHAR", "Horario tal como lo declara la estación."),
            C("gasolina_95", "DOUBLE", "Precio de la gasolina 95 E5, €/litro."),
            C("gasolina_98", "DOUBLE", "Precio de la gasolina 98 E5, €/litro."),
            C("gasoleo_a", "DOUBLE", "Precio del gasóleo A (diésel normal), €/litro."),
            C("gasoleo_premium", "DOUBLE", "Precio del gasóleo premium, €/litro."),
            C("glp", "DOUBLE", "Precio del gas licuado del petróleo (autogás), €/litro."),
        ],
        clave=["id_estacion"],
        fuente="miteco_carburantes",
    ),
    Tabla(
        "carburantes_semanal",
        "Precio de los carburantes por comunidad, cada lunes de las últimas semanas, calculado "
        "a partir del histórico diario de todas las gasolineras de venta al público.",
        [
            C("fecha", "DATE", "Día de la foto (un lunes)."),
            CODIGO_CCAA,
            C(
                "carburante",
                "VARCHAR",
                "'gasolina_95', 'gasolina_98', 'gasoleo_a', 'gasoleo_premium' o 'glp'.",
            ),
            C("precio_medio", "DOUBLE", "Media simple del precio entre gasolineras, €/litro."),
            C("precio_mediano", "DOUBLE", "Mediana del precio entre gasolineras, €/litro."),
            C("precio_min", "DOUBLE", "Precio mínimo, €/litro."),
            C("precio_max", "DOUBLE", "Precio máximo, €/litro."),
            C("n_estaciones", "INTEGER", "Número de gasolineras con precio para ese carburante."),
        ],
        clave=["fecha", "codigo_ccaa", "carburante"],
        fuente="miteco_carburantes",
    ),
]

TABLA_FUENTES = Tabla(
    "fuentes",
    "Origen, licencia y fecha de descarga de cada tabla. Cítalo al responder.",
    [
        C("tabla", "VARCHAR", "Nombre de la tabla."),
        C("organismo", "VARCHAR", "Organismo que publica los datos."),
        C("conjunto", "VARCHAR", "Conjunto de datos de origen."),
        C("url_dataset", "VARCHAR", "Página pública del conjunto de datos."),
        C("url_api", "VARCHAR", "URL de la API usada para descargarlo."),
        C("licencia", "VARCHAR", "Licencia o condiciones de reutilización."),
        C("cita", "VARCHAR", "Texto de atribución."),
        C("datos_hasta", "VARCHAR", "Último periodo disponible en la tabla."),
        C("descargado", "TIMESTAMP", "Fecha y hora (UTC) de la descarga."),
    ],
    clave=["tabla"],
)


def _sql_literal(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def ddl(tabla: Tabla) -> list[str]:
    columnas = ",\n  ".join(f"{c.nombre} {c.tipo}" for c in tabla.columnas)
    clave = f",\n  PRIMARY KEY ({', '.join(tabla.clave)})" if tabla.clave else ""
    sentencias = [
        f"CREATE TABLE {tabla.nombre} (\n  {columnas}{clave}\n)",
        f"COMMENT ON TABLE {tabla.nombre} IS {_sql_literal(tabla.descripcion)}",
    ]
    sentencias += [
        f"COMMENT ON COLUMN {tabla.nombre}.{c.nombre} IS {_sql_literal(c.descripcion)}"
        for c in tabla.columnas
    ]
    return sentencias
