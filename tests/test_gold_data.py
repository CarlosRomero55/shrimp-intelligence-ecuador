"""
test_gold_data.py — Pruebas de DATOS sobre gold (Fase 7, Paso 7.6).

Convierten los controles de src/build_gold.py en pruebas permanentes: si alguien
cambia la carga de gold y rompe una garantía del modelo, pytest lo detecta.

Garantías que protegen:
  1. fact_exports: en cada mes del BCE, cifras preferidas = total oficial del BCE.
  2. A lo más UNA cifra preferida por mes, país y partida; nada preferido fuera del BCE.
  3. Solo 'Sin reporte por país' (9999) puede ser negativo.
  4. Gold = silver: detalle de exportaciones y totales de la finca.
  5. Ningún hecho apunta al miembro 'Desconocido' (0) de una dimensión.
  6. La vista de conciliación muestra todas las filas de silver.

Todas solo LEEN. Marcadas como "db": se saltan si MySQL no está disponible.

Ejecutar: python -m pytest tests/test_gold_data.py -v
"""
from __future__ import annotations

import pytest

from src.db import read_sql

pytestmark = pytest.mark.db          # todas las pruebas de este archivo usan MySQL

SRC_X, SRC_BCE, SRC_MIRROR = 1, 3, 5
NO_REPORT_KEY = 9999
BCE_TOLERANCE_USD = 1.0
BCE_MONTHS = """
    SELECT DISTINCT YEAR(period_date) * 10000 + MONTH(period_date) * 100 + 1 AS date_key,
           reference_value_usd AS bce_usd
    FROM silver.source_reconciliation
    WHERE comparison_level = 'total' AND source_id = 1
"""


def assert_no_rows(sql: str, what: str, params: dict | None = None) -> None:
    """Falla si la consulta devuelve filas y muestra hasta 10 como evidencia."""
    bad = read_sql(sql, params)
    assert bad.empty, f"{what}: {len(bad)} filas\n{bad.head(10).to_string(index=False)}"


# ================================================================== fact_exports
def test_preferidas_cuadran_con_bce_cada_mes():
    assert_no_rows(
        f"""
        SELECT b.date_key, b.bce_usd, p.preferred_usd
        FROM ({BCE_MONTHS}) AS b
        LEFT JOIN (SELECT date_key, SUM(value_usd) AS preferred_usd
                   FROM gold.fact_exports WHERE is_preferred = 1
                   GROUP BY date_key) AS p ON p.date_key = b.date_key
        WHERE p.preferred_usd IS NULL OR ABS(p.preferred_usd - b.bce_usd) > :tol
        """,
        "meses en que las cifras preferidas no suman el total del BCE",
        {"tol": BCE_TOLERANCE_USD},
    )


def test_una_sola_cifra_preferida():
    assert_no_rows(
        "SELECT date_key, country_key, product_key, COUNT(*) AS n FROM gold.fact_exports "
        "WHERE is_preferred = 1 GROUP BY date_key, country_key, product_key HAVING COUNT(*) > 1",
        "combinaciones mes-país-partida con más de una cifra preferida",
    )


def test_nada_preferido_fuera_de_meses_bce():
    assert_no_rows(
        f"""
        SELECT f.date_key, COUNT(*) AS filas
        FROM gold.fact_exports AS f
        LEFT JOIN ({BCE_MONTHS}) AS b ON b.date_key = f.date_key
        WHERE f.is_preferred = 1 AND b.date_key IS NULL
        GROUP BY f.date_key
        """,
        "cifras preferidas en meses que el BCE no ha publicado",
    )


def test_negativos_solo_en_sin_reporte():
    assert_no_rows(
        "SELECT date_key, country_key, source_key, value_usd FROM gold.fact_exports "
        "WHERE value_usd < 0 AND country_key <> :nr",
        "valores negativos fuera de 'Sin reporte por país'",
        {"nr": NO_REPORT_KEY},
    )


def test_sin_reporte_una_fila_por_mes_bce():
    counts = read_sql(
        f"""
        SELECT (SELECT COUNT(*) FROM ({BCE_MONTHS}) AS b)                 AS meses_bce,
               (SELECT COUNT(*) FROM gold.fact_exports
                WHERE country_key = :nr AND source_key = :bce)          AS filas_sin_reporte
        """,
        {"nr": NO_REPORT_KEY, "bce": SRC_BCE},
    ).iloc[0]
    assert counts["meses_bce"] == counts["filas_sin_reporte"], counts.to_dict()


@pytest.mark.parametrize("source_id", [SRC_X, SRC_MIRROR], ids=["comtrade_x", "espejo"])
def test_detalle_igual_a_silver(source_id):
    """Mismas filas y mismo valor total por fuente en silver y en gold."""
    row = read_sql(
        """
        SELECT (SELECT COUNT(*)       FROM silver.export_monthly WHERE source_id = :s) AS s_filas,
               (SELECT COUNT(*)       FROM gold.fact_exports     WHERE source_key = :s) AS g_filas,
               (SELECT SUM(value_usd) FROM silver.export_monthly WHERE source_id = :s) AS s_usd,
               (SELECT SUM(value_usd) FROM gold.fact_exports     WHERE source_key = :s) AS g_usd
        """,
        {"s": source_id},
    ).iloc[0]
    assert row["s_filas"] == row["g_filas"], row.to_dict()
    assert abs(float(row["s_usd"]) - float(row["g_usd"])) < 0.01, row.to_dict()


# ================================================================== fact_production
def test_produccion_igual_a_silver():
    row = read_sql(
        """
        SELECT (SELECT COUNT(*)            FROM silver.production_cycles) AS s_ciclos,
               (SELECT COUNT(*)            FROM gold.fact_production)    AS g_ciclos,
               (SELECT SUM(harvested_kg)   FROM silver.harvests)         AS s_kg,
               (SELECT SUM(harvested_kg)   FROM gold.fact_production)    AS g_kg,
               (SELECT SUM(amount_usd)     FROM silver.cycle_costs)      AS s_costo,
               (SELECT SUM(total_cost_usd) FROM gold.fact_production)    AS g_costo,
               (SELECT SUM(revenue_usd)    FROM silver.sales)            AS s_ingreso,
               (SELECT SUM(revenue_usd)    FROM gold.fact_production)    AS g_ingreso
        """
    ).iloc[0]
    for name in ["ciclos", "kg", "costo", "ingreso"]:
        s, g = float(row[f"s_{name}"]), float(row[f"g_{name}"])
        assert abs(s - g) < 0.01, f"{name}: silver {s} vs gold {g}"


def test_produccion_animales_no_superan_larvas():
    """Supervivencia ≤ 100 %: no se pueden cosechar más animales de los sembrados."""
    assert_no_rows(
        "SELECT cycle_code, larvae_stocked, harvested_animals FROM gold.fact_production "
        "WHERE harvested_animals > larvae_stocked OR harvested_animals = 0",
        "ciclos con supervivencia imposible",
    )


# ================================================================== dimensiones
def test_hechos_sin_miembro_desconocido():
    """El miembro 0 existe para no dejar FK nulas, pero hoy ningún hecho debería usarlo."""
    assert_no_rows(
        """
        SELECT 'fact_exports' AS tabla, COUNT(*) AS filas FROM gold.fact_exports
        WHERE country_key = 0 OR product_key = 0 OR source_key = 0
        HAVING COUNT(*) > 0
        UNION ALL
        SELECT 'fact_production', COUNT(*) FROM gold.fact_production
        WHERE pond_key = 0 OR size_key = 0
        HAVING COUNT(*) > 0
        """,
        "hechos asignados a 'Desconocido'",
    )


# ================================================================== vista de conciliación
def test_vista_conciliacion_completa():
    row = read_sql(
        """
        SELECT (SELECT COUNT(*) FROM silver.source_reconciliation) AS silver_filas,
               (SELECT COUNT(*) FROM gold.v_reconciliation)        AS vista_filas,
               (SELECT COUNT(*) FROM gold.v_reconciliation
                WHERE is_comparable NOT IN (0, 1))                 AS marcas_invalidas
        """
    ).iloc[0]
    assert row["silver_filas"] == row["vista_filas"], row.to_dict()
    assert row["marcas_invalidas"] == 0, row.to_dict()