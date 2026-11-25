"""El evaluador debe aprobar las respuestas correctas y suspender las incorrectas o vacías."""

import pytest

from datos_abiertos_mcp.config import DB_PATH
from datos_abiertos_mcp.evaluacion import cargar_casos, cita_fuente, comparar, valores_iguales


def test_casos_bien_formados():
    casos = cargar_casos()
    assert 30 <= len(casos) <= 45
    assert {c.categoria for c in casos} >= {"paro", "poblacion", "ipc", "carburantes", "sin_datos"}


@pytest.mark.parametrize(
    ("oro", "obtenido", "esperado"),
    [
        (10.18, 10.18, True),
        (10.18, 10.2, True),  # redondeo a un decimal
        (10.18, 10.3, False),
        (49618622, 49618622.0, True),
        (49618622, 49128297, False),  # el dato del año anterior
        (490325, 490000, False),
        ("Comunidad de Madrid", "Madrid, Comunidad de", True),  # nombre oficial del INE
        ("Castilla y León", "castilla y leon", True),
        ("Asturias", "Asturias, Principado de", True),
        ("Asturias", "Galicia", False),
        ("REPSOL", "Repsol", True),
        (8, 8, True),
        (8, 9, False),
    ],
)
def test_valores_iguales(oro, obtenido, esperado):
    assert valores_iguales(oro, obtenido) is esperado


def test_comparar_valor_exige_primera_fila():
    oro = [["Islas Baleares"]]
    ranking = [["Islas Baleares", 4.63], ["País Vasco", 7.17]]
    assert comparar("valor", oro, ranking)[0]
    # mismo ranking al revés: la respuesta ya no es la primera fila
    assert not comparar("valor", oro, ranking[::-1])[0]


def test_comparar_filas_sin_orden_y_con_columnas_extra():
    oro = [["Asturias"], ["Extremadura"]]
    assert comparar("filas", oro, [["Extremadura", -1.2], ["Asturias", -0.5]])[0]
    assert not comparar("filas", oro, [["Extremadura", -1.2]])[0]  # falta una
    assert not comparar("filas", oro, [["Extremadura"], ["Asturias"], ["Ceuta"]])[0]  # sobra una


def test_comparar_filas_ordenadas():
    oro = [["Andalucía"], ["Extremadura"], ["Comunidad Valenciana"]]
    assert (
        comparar("filas_ordenadas", oro, [["Andalucía", 14.9], ["Extremadura", 13], ["Valenciana"]])[0]
        is False
    )
    assert comparar("filas_ordenadas", oro, [["Andalucía"], ["Extremadura"], ["Comunitat Valenciana"]])[0]
    assert not comparar("filas_ordenadas", oro, [["Extremadura"], ["Andalucía"], ["Comunitat Valenciana"]])[0]


@pytest.mark.parametrize("vacio", [None, []])
def test_sin_resultado_suspende(vacio):
    assert comparar("valor", [[10.18]], vacio)[0] is False


def test_cita_fuente():
    assert cita_fuente("Fuente: INE, EPA 3T 2026")
    assert cita_fuente("Fuente: Ministerio para la Transición Ecológica")
    assert not cita_fuente("La tasa es del 10 %.")


@pytest.mark.skipif(not DB_PATH.exists(), reason="requiere la base de datos real (uv run construir-datos)")
def test_oraculo_y_nulo_sobre_datos_reales():
    """El SQL de referencia aprueba contra sí mismo; un resultado vacío o de otra pregunta suspende."""
    from datos_abiertos_mcp.almacen import Almacen

    almacen = Almacen()
    casos = [c for c in cargar_casos() if c.sql]
    oros = {c.id: almacen.consultar(c.sql).filas for c in casos}
    for c in casos:
        assert oros[c.id], f"{c.id}: el SQL de referencia no devuelve filas"
        assert comparar(c.comparacion, oros[c.id], oros[c.id])[0], c.id
        assert not comparar(c.comparacion, oros[c.id], None)[0], c.id
    # respuesta «plausible pero de otra pregunta»: casi nunca debe aprobar
    falsos = sum(
        comparar(c.comparacion, oros[c.id], oros[otro.id])[0]
        for c, otro in zip(casos, casos[1:] + casos[:1], strict=True)
    )
    assert falsos <= 1
