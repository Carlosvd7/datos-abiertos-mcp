"""Tests del pipeline con datos de ejemplo en el formato real de las APIs (sin red)."""

from datetime import date

from datos_abiertos_mcp.pipeline import carburantes, ine, modelos
from datos_abiertos_mcp.pipeline.construir import lunes_anteriores
from datos_abiertos_mcp.pipeline.esquema import TABLAS, ddl


def _serie(cod, metadata, datos):
    return {
        "COD": cod,
        "MetaData": [{"T3_Variable": v, "Nombre": n, "Codigo": c} for v, n, c in metadata],
        "Data": [
            {"Fecha": f, "T3_Periodo": p, "Anyo": int(f[:4]), "Valor": val, "Secreto": False}
            for f, p, val in datos
        ],
    }


def test_paro_normaliza_sexo_edad_y_trimestre():
    serie = _serie(
        "EPA1",
        [
            ("Tipo de dato", "Tasa de paro de la población", ""),
            ("Sexo", "Ambos sexos", ""),
            ("Comunidades y Ciudades Autónomas", "Andalucía", "01"),
            ("Totales de edad", "Total", ""),
        ],
        [("2026-07-01T00:00:00.000+02:00", "T3", 15.234), ("2026-04-01T00:00:00.000+02:00", "T2", 16.0)],
    )
    filas = modelos.filas_paro(ine.observaciones([serie]))
    assert filas[0] == ("2026T3", 2026, 3, date(2026, 7, 1), "01", "Total", "Total", 15.23)
    assert filas[1][0] == "2026T2"


def test_poblacion_agrega_edades_y_descarta_solapes():
    base = [("Sexo", "Total", ""), ("Total Nacional", "Total Nacional", "00")]
    fecha = [("2025-01-01T00:00:00.000+01:00", "1 de enero de", None)]

    def con_valor(meta, valor):
        return _serie("X", base + [meta], [(fecha[0][0], fecha[0][1], valor)])

    series = [
        con_valor(("Totales de edad", "Todas las edades", ""), 1000),
        con_valor(("Valores simples de edad", "10 años", "Y10"), 300),
        con_valor(("Valores simples de edad", "20 años", "Y20"), 200),
        con_valor(("Valores simples de edad", "70 años", "Y70"), 450),
        con_valor(("Semiintervalos de edad", "100 y más años", "Y-GE100"), 50),
        con_valor(("Semiintervalos de edad", "85 y más años", "Y-GE85"), 999),  # se ignora
    ]
    filas = {f[3]: f[4] for f in modelos.filas_poblacion(ine.observaciones(series))}
    assert filas == {"Total": 1000, "0 a 15": 300, "16 a 24": 200, "65 y más": 500}
    assert sum(v for k, v in filas.items() if k != "Total") == filas["Total"]


def test_poblacion_ignora_fechas_que_no_son_1_de_enero():
    serie = _serie(
        "X",
        [
            ("Sexo", "Hombres", "1"),
            ("Comunidades y Ciudades Autónomas", "Ceuta", "18"),
            ("Totales de edad", "Todas las edades", ""),
        ],
        [("2025-07-01T00:00:00.000+02:00", "1 de julio de", 10)],
    )
    assert modelos.filas_poblacion(ine.observaciones([serie])) == []


def test_ipc_pivota_las_tres_tasas():
    meta = [("Comunidades y Ciudades Autónomas", "Galicia", "12"), ("Grupos ECOICOP", "Índice general", "00")]
    f = "2026-10-01T00:00:00.000+02:00"
    series = [
        _serie("A", meta + [("Índices y Tasas", "Variación mensual", "1")], [(f, "M10", 0.6)]),
        _serie("B", meta + [("Índices y Tasas", "Variación anual", "2")], [(f, "M10", 2.4)]),
        _serie("C", meta + [("Índices y Tasas", "Variación en lo que va de año", "5")], [(f, "M10", 1.9)]),
    ]
    assert modelos.filas_ipc(ine.observaciones(series)) == [
        (2026, 10, date(2026, 10, 1), "12", 0.6, 2.4, 1.9)
    ]


def test_valores_secretos_se_cargan_como_nulos():
    serie = _serie(
        "S",
        [("Sexo", "Total", ""), ("Total Nacional", "Total Nacional", "00")],
        [("2026-01-01T00:00:00.000+01:00", "T1", 5.0)],
    )
    serie["Data"][0]["Secreto"] = True
    assert next(ine.observaciones([serie])).valor is None


RESPUESTA_CARBURANTES = {
    "Fecha": "25/11/2026 23:42:33",
    "ListaEESSPrecio": [
        {
            "IDEESS": "1",
            "Rótulo": "REPSOL ",
            "Dirección": "CALLE A, 1",
            "Municipio": "Abengibre",
            "Provincia": "ALBACETE",
            "IDCCAA": "08",
            "C.P.": "02250",
            "Latitud": "39,211417",
            "Longitud (WGS84)": "-1,539167",
            "Horario": "L-D: 24H",
            "Tipo Venta": "P",
            "Precio Gasolina 95 E5": "1,399",
            "Precio Gasoleo A": "1,699",
            "Precio Gasolina 98 E5": "",
            "Precio Gasoleo Premium": "",
            "Precio Gases licuados del petróleo": "",
        },
        {"IDEESS": "2", "IDCCAA": "13", "Tipo Venta": "R", "Precio Gasoleo A": "1,500"},
    ],
}


def test_carburantes_convierte_decimales_con_coma_y_vacios():
    fila = carburantes.filas_estaciones(RESPUESTA_CARBURANTES)[0]
    assert fila[1] == date(2026, 11, 25)
    assert fila[2] == "REPSOL"
    assert fila[8:10] == (39.211417, -1.539167)
    assert fila[11] == "público"
    assert fila[12:] == (1.399, None, 1.699, None, None)


def test_agregado_semanal_en_sql_solo_venta_al_publico(tmp_path):
    import json

    import duckdb

    otra = {
        "IDEESS": "3",
        "IDCCAA": "08",
        "Tipo Venta": "P",
        "Precio Gasolina 95 E5": "1,501",
        "Precio Gasoleo A": "",
        "Precio Gasolina 98 E5": "",
    }
    datos = {**RESPUESTA_CARBURANTES, "ListaEESSPrecio": [*RESPUESTA_CARBURANTES["ListaEESSPrecio"], otra]}
    ruta = tmp_path / "hist_2026-11-25.json"
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    filas = duckdb.connect().execute(carburantes.sql_semanal([ruta])).fetchall()
    por_clave = {(f[1], f[2]): f[3:] for f in filas}
    # la gasolinera de venta restringida ('R', comunidad 13) no cuenta
    assert ("13", "gasoleo_a") not in por_clave
    assert por_clave[("08", "gasolina_95")] == (1.45, 1.45, 1.399, 1.501, 2)
    assert por_clave[("08", "gasoleo_a")] == (1.699, 1.699, 1.699, 1.699, 1)
    # total nacional
    assert por_clave[("00", "gasolina_95")][-1] == 2
    assert all(f[0] == date(2026, 11, 25) for f in filas)


def test_lunes_anteriores():
    lunes = lunes_anteriores(date(2026, 11, 25), 3)  # miércoles
    assert lunes == [date(2026, 11, 9), date(2026, 11, 16), date(2026, 11, 23)]
    assert all(d.weekday() == 0 for d in lunes)


def test_todas_las_columnas_tienen_descripcion():
    for tabla in TABLAS:
        assert tabla.descripcion
        assert all(c.descripcion for c in tabla.columnas), tabla.nombre
        assert ddl(tabla)[0].startswith(f"CREATE TABLE {tabla.nombre}")
