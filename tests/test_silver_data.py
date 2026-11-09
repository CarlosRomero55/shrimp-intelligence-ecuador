"""
test_silver_data.py — Pruebas de DATOS sobre silver (Fase 6, Paso 6.2).

Prueban el RESULTADO de la limpieza, no el código: consultan las tablas ya cargadas.
Todas quedan marcadas como "db" (ver conftest.py) y solo LEEN: no modifican nada.

Patrón: cada consulta busca las filas que ROMPEN la regla. La prueba pasa si no
encuentra ninguna; si falla, muestra las primeras filas culpables.

Rangos de negocio: FCR y supervivencia NO se guardan en silver (ADR-002); aquí se
calculan desde los hechos medidos (alimento, kg, peso promedio y larvas sembradas).

Ejecutar: python -m pytest tests/test_silver_data.py -v
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.catalogs import SOURCE_IDS
from src.db import read_sql

FCR_MIN, FCR_MAX = 0.8, 3.0
SURVIVAL_MIN, SURVIVAL_MAX = 0, 100
MAX_WEIGHT_G = 60
EXPECTED_CYCLES = 510                    # simulación: 525 filas − 15 duplicados exactos
COST_TYPES = 4                           # larvae, feed, energy, labor


def assert_no_rows(sql: str, what: str, params: dict | None = None) -> None:
    """Falla si la consulta devuelve filas y muestra hasta 10 como evidencia."""
    bad = read_sql(sql, params)
    assert bad.empty, f"{what}: {len(bad)} filas\n{bad.head(10).to_string(index=False)}"


# ================================================================== 1. Unicidad
# Las llaves UNIQUE ya lo impiden en MySQL; la prueba documenta la regla y detecta si
# alguien borra la llave (como pasaba con sales antes del paso 5.8).
UNIQUE_KEYS = {
    "export_monthly": "source_id, country_id, product_id, period_date",
    "production_cycles": "cycle_code",
    "harvests": "cycle_id, harvest_date",
    "sales": "harvest_id",
    "cycle_costs": "cycle_id, cost_type",
    "countries": "iso3",
    "country_aliases": "alias_text",
    "source_reconciliation": "period_date, comparison_level, country_key, source_id",
}


@pytest.mark.parametrize("table, key", UNIQUE_KEYS.items(), ids=list(UNIQUE_KEYS))
def test_llave_de_negocio_unica(table, key):
    assert_no_rows(
        f"SELECT {key}, COUNT(*) AS veces FROM silver.{table} "
        f"GROUP BY {key} HAVING COUNT(*) > 1",
        f"duplicados en silver.{table} ({key})",
    )


# ================================================================== 2. Integridad (sin huérfanos)
ORPHANS = {
    "cosecha_sin_ciclo": """
        SELECT h.harvest_id FROM silver.harvests AS h
        LEFT JOIN silver.production_cycles AS c ON c.cycle_id = h.cycle_id
        WHERE c.cycle_id IS NULL""",
    "venta_sin_cosecha": """
        SELECT s.sale_id FROM silver.sales AS s
        LEFT JOIN silver.harvests AS h ON h.harvest_id = s.harvest_id
        WHERE h.harvest_id IS NULL""",
    "ciclo_sin_piscina": """
        SELECT c.cycle_code FROM silver.production_cycles AS c
        LEFT JOIN silver.ponds AS p ON p.pond_id = c.pond_id
        WHERE p.pond_id IS NULL""",
    "pais_sin_region": """
        SELECT c.iso3 FROM silver.countries AS c
        LEFT JOIN silver.regions AS r ON r.region_id = c.region_id
        WHERE r.region_id IS NULL""",
    "exportacion_sin_pais_o_producto": """
        SELECT e.source_id, e.period_date FROM silver.export_monthly AS e
        LEFT JOIN silver.countries AS c ON c.country_id = e.country_id
        LEFT JOIN silver.products  AS p ON p.product_id = e.product_id
        WHERE c.country_id IS NULL OR p.product_id IS NULL""",
    "cosecha_sin_talla": """
        SELECT h.harvest_id FROM silver.harvests AS h
        LEFT JOIN silver.sizes AS z ON z.size_id = h.size_id
        WHERE z.size_id IS NULL""",
}


@pytest.mark.parametrize("name, sql", ORPHANS.items(), ids=list(ORPHANS))
def test_sin_huerfanos(name, sql):
    assert_no_rows(sql, name)


# ================================================================== 3. Rangos de negocio
def test_fcr_en_rango():
    """FCR = alimento ÷ kg cosechados (por ciclo). Ciclos sin alimento registrado no aplican."""
    assert_no_rows(
        """
        SELECT c.cycle_code, c.feed_kg, SUM(h.harvested_kg) AS kg,
               ROUND(c.feed_kg / SUM(h.harvested_kg), 2) AS fcr
        FROM silver.production_cycles AS c
        JOIN silver.harvests AS h ON h.cycle_id = c.cycle_id
        WHERE c.feed_kg IS NOT NULL
        GROUP BY c.cycle_id, c.cycle_code, c.feed_kg
        HAVING fcr < :lo OR fcr > :hi
        """,
        f"FCR fuera de [{FCR_MIN}, {FCR_MAX}]",
        {"lo": FCR_MIN, "hi": FCR_MAX},
    )


def test_supervivencia_en_rango():
    """Supervivencia % = piezas cosechadas ÷ larvas sembradas × 100;
    piezas = kg × 1000 ÷ peso promedio (g)."""
    assert_no_rows(
        """
        SELECT c.cycle_code, c.larvae_stocked,
               ROUND(SUM(h.harvested_kg * 1000 / h.avg_weight_g)
                     / c.larvae_stocked * 100, 1) AS supervivencia_pct
        FROM silver.production_cycles AS c
        JOIN silver.harvests AS h ON h.cycle_id = c.cycle_id
        GROUP BY c.cycle_id, c.cycle_code, c.larvae_stocked
        HAVING supervivencia_pct <= :lo OR supervivencia_pct > :hi
        """,
        f"supervivencia fuera de ({SURVIVAL_MIN}, {SURVIVAL_MAX}] %",
        {"lo": SURVIVAL_MIN, "hi": SURVIVAL_MAX},
    )


def test_peso_promedio_en_rango():
    assert_no_rows(
        "SELECT harvest_id, avg_weight_g FROM silver.harvests "
        "WHERE avg_weight_g <= 0 OR avg_weight_g > :mx",
        f"peso promedio fuera de (0, {MAX_WEIGHT_G}] g",
        {"mx": MAX_WEIGHT_G},
    )


def test_montos_positivos():
    assert_no_rows(
        """
        SELECT 'venta' AS tabla, sale_id AS id, price_usd_lb AS valor FROM silver.sales
        WHERE price_usd_lb <= 0 OR sold_lb <= 0
        UNION ALL
        SELECT 'costo', cycle_id, amount_usd FROM silver.cycle_costs WHERE amount_usd < 0
        UNION ALL
        SELECT 'exportacion', source_id, value_usd FROM silver.export_monthly WHERE value_usd <= 0
        """,
        "montos no positivos",
    )


def test_cosecha_despues_de_siembra():
    assert_no_rows(
        "SELECT cycle_code, stocking_date, final_harvest_date FROM silver.production_cycles "
        "WHERE final_harvest_date <= stocking_date",
        "cosecha antes o el mismo día de la siembra",
    )


# ================================================================== 4. Completitud
def test_numero_de_ciclos():
    n = read_sql("SELECT COUNT(*) AS n FROM silver.production_cycles")["n"].iloc[0]
    assert n == EXPECTED_CYCLES, f"se esperaban {EXPECTED_CYCLES} ciclos y hay {n}"


def test_cuatro_costos_por_ciclo():
    assert_no_rows(
        """
        SELECT c.cycle_code, COUNT(k.cost_type) AS tipos
        FROM silver.production_cycles AS c
        LEFT JOIN silver.cycle_costs AS k ON k.cycle_id = c.cycle_id
        GROUP BY c.cycle_id, c.cycle_code
        HAVING COUNT(k.cost_type) <> :n
        """,
        f"ciclos sin exactamente {COST_TYPES} costos",
        {"n": COST_TYPES},
    )


def test_una_venta_por_cosecha_y_libras_cuadran():
    """Toda cosecha tiene venta y se vende exactamente lo cosechado (±0,01 lb)."""
    assert_no_rows(
        """
        SELECT h.harvest_id, h.harvested_lb, s.sold_lb
        FROM silver.harvests AS h
        LEFT JOIN silver.sales AS s ON s.harvest_id = h.harvest_id
        WHERE s.sale_id IS NULL OR ABS(h.harvested_lb - s.sold_lb) > 0.01
        """,
        "cosechas sin venta o con libras distintas",
    )


def test_exportaciones_dia_uno_y_fuentes_validas():
    assert_no_rows(
        """
        SELECT source_id, period_date FROM silver.export_monthly
        WHERE DAYOFMONTH(period_date) <> 1 OR source_id NOT IN (:x, :m)
        """,
        "periodo que no es día 1 o fuente que no es de detalle",
        {"x": SOURCE_IDS["UN_COMTRADE"], "m": SOURCE_IDS["COMTRADE_MIRROR"]},
    )


# ================================================================== 5. Conciliación
def test_conciliacion_niveles_y_referencias():
    """total: BCE como referencia y sin país · country: Ecuador (X) como referencia y con país."""
    assert_no_rows(
        """
        SELECT period_date, comparison_level, country_id, source_id, reference_source_id
        FROM silver.source_reconciliation
        WHERE NOT (
              (comparison_level = 'total'   AND country_id IS NULL
                                            AND reference_source_id = :bce)
           OR (comparison_level = 'country' AND country_id IS NOT NULL
                                            AND source_id = :m AND reference_source_id = :x)
        )
        """,
        "filas de conciliación con nivel, país o referencia incoherentes",
        {"bce": SOURCE_IDS["BCE"], "x": SOURCE_IDS["UN_COMTRADE"],
         "m": SOURCE_IDS["COMTRADE_MIRROR"]},
    )


def test_conciliacion_tiene_ambos_niveles():
    counts = read_sql(
        "SELECT comparison_level, COUNT(*) AS n FROM silver.source_reconciliation "
        "GROUP BY comparison_level"
    )
    levels = dict(zip(counts["comparison_level"], counts["n"]))
    assert levels.get("total", 0) > 0 and levels.get("country", 0) > 0, levels