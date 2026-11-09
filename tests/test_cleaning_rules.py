"""
test_cleaning_rules.py — Pruebas UNITARIAS de las funciones de limpieza (Fase 6, Paso 6.1).

Prueban el CÓDIGO, no los datos: cada función recibe un texto sucio inventado y debe
devolver el valor limpio esperado (o None si el texto no se puede interpretar).
No usan MySQL, así que corren en segundos y en cualquier PC.

Las pruebas sobre los DATOS ya cargados en silver van en otro archivo (marcadas con
@pytest.mark.db) en el Paso 6.2.

Ejecutar: python -m pytest tests/test_cleaning_rules.py -v
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.catalogs import normalize_alias, normalize_size
from src.clean_farm import mode_or_first, parse_date, parse_decimal
from src.reconcile import parse_ec_number, parse_month_es


# ------------------------------------------------------------------ fechas (clean_farm)
# Regla del proyecto: en los formatos sucios el DÍA va antes que el MES.
@pytest.mark.parametrize("text, expected_date, expected_fmt", [
    ("2024-03-15", date(2024, 3, 15), "%Y-%m-%d"),
    ("2024/03/15", date(2024, 3, 15), "%Y/%m/%d"),
    ("15/03/2024", date(2024, 3, 15), "%d/%m/%Y"),
    ("15-03-2024", date(2024, 3, 15), "%d-%m-%Y"),
    (" 2024-03-15 ", date(2024, 3, 15), "%Y-%m-%d"),     # espacios alrededor
])
def test_parse_date_formatos_validos(text, expected_date, expected_fmt):
    assert parse_date(text) == (expected_date, expected_fmt)


def test_parse_date_dia_antes_que_mes():
    # '03/04/2024' es 3 de abril (no 4 de marzo)
    assert parse_date("03/04/2024")[0] == date(2024, 4, 3)


@pytest.mark.parametrize("text", [None, np.nan, "", "15 de marzo", "2024-13-01", "31/02/2024"])
def test_parse_date_invalida_devuelve_none(text):
    assert parse_date(text) == (None, None)


# ------------------------------------------------------------------ números (clean_farm)
@pytest.mark.parametrize("text, expected", [
    ("9,95", 9.95),
    ("9.95", 9.95),
    (" 12 ", 12.0),
    (7, 7.0),
    ("-3,5", -3.5),       # el signo se conserva; la regla de negocio decide qué hacer
])
def test_parse_decimal_validos(text, expected):
    assert parse_decimal(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [None, np.nan, "", "abc", "1.234,5"])
def test_parse_decimal_invalidos(text):
    # '1.234,5' no se acepta: la simulación no usa separador de miles
    assert parse_decimal(text) is None


# ------------------------------------------------------------------ formato ecuatoriano (reconcile)
@pytest.mark.parametrize("text, expected", [
    ("596.248,67089", 596248.67089),
    ("1.234", 1234.0),          # el punto es separador de MILES en el BCE
    ("0,5", 0.5),
    ("744000", 744000.0),
])
def test_parse_ec_number_validos(text, expected):
    assert parse_ec_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [None, np.nan, "", "n.d."])
def test_parse_ec_number_invalidos(text):
    assert parse_ec_number(text) is None


# ------------------------------------------------------------------ meses en español (reconcile)
@pytest.mark.parametrize("label, expected", [
    ("Ene 2025", date(2025, 1, 1)),
    ("dic 2018", date(2018, 12, 1)),
    ("Sep 2024", date(2024, 9, 1)),
    ("Set 2024", date(2024, 9, 1)),       # variante usada en algunos cuadros
    (" Ago   2026 ", date(2026, 8, 1)),
])
def test_parse_month_es_validos(label, expected):
    assert parse_month_es(label) == expected


@pytest.mark.parametrize("label", ["Xyz 2025", "Enero", "2025", "Ene 25x", ""])
def test_parse_month_es_invalidos(label):
    assert parse_month_es(label) is None


# ------------------------------------------------------------------ tallas (catalogs)
@pytest.mark.parametrize("text, expected", [
    ("20/30", "20/30"),
    ("20-30", "20/30"),
    ("20 30", "20/30"),
    (" 21 / 25 ", "21/25"),
    ("100/120", "100/120"),
])
def test_normalize_size_validos(text, expected):
    assert normalize_size(text) == expected


@pytest.mark.parametrize("text", [None, np.nan, "", "grande", "20", "20/30/40", "2/3"])
def test_normalize_size_invalidos(text):
    assert normalize_size(text) is None


# ------------------------------------------------------------------ alias de países (catalogs)
@pytest.mark.parametrize("text, expected", [
    ("  U.S.A. ", "u.s.a."),
    ("CHINA", "china"),
    ("china ", "china"),
    ("España", "espana"),
    ("Estados   Unidos de América", "estados unidos de america"),
])
def test_normalize_alias(text, expected):
    assert normalize_alias(text) == expected


def test_normalize_alias_variantes_iguales():
    # Lo importante: todas las variantes sucias terminan en la MISMA llave
    variants = ["China", "CHINA", "china", "China ", " china"]
    assert {normalize_alias(v) for v in variants} == {"china"}


@pytest.mark.parametrize("text", [None, np.nan])
def test_normalize_alias_nulo(text):
    assert normalize_alias(text) is None


# ------------------------------------------------------------------ valor más frecuente (clean_farm)
def test_mode_or_first_mas_frecuente():
    assert mode_or_first(pd.Series(["Taura", "Taura", "Tauraa"])) == "Taura"


def test_mode_or_first_ignora_nulos():
    assert mode_or_first(pd.Series([None, "El Oro", None, None])) == "El Oro"


def test_mode_or_first_todo_nulo():
    assert mode_or_first(pd.Series([None, None], dtype=object)) is None