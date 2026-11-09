"""
build_gold.py — Construye la capa GOLD (modelo estrella) desde silver.

Decisiones de la Fase 7 (2026-11-09):
  1. Gold se construye con SQL dentro de MySQL (INSERT ... SELECT desde silver):
     los datos no salen de la base.
  2. Llaves de las dimensiones = IDs de silver (deterministas): las relaciones de
     Power BI no se rompen al recargar. Llave 0 = 'Desconocido'; 9999 = 'Sin reporte
     por país' / 'total sin desglose' (ADR-006). Ambas vienen de sql/03_gold.sql.
  3. Dimensiones: upsert (INSERT ... ON DUPLICATE KEY UPDATE), sin borrar.
     Hechos: reconstrucción completa en una transacción (pasos 7.4 y 7.5).

Paso 7.2: dim_source, dim_product, dim_size · Paso 7.3: dim_country, dim_pond.
Paso 7.4: fact_exports (Comtrade X + espejo + diferencia contra el BCE).
Paso 7.5: fact_production (un ciclo de la finca SIMULADA por fila).

Ejecutar: python -m src.build_gold
"""
from __future__ import annotations

import logging
import time

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.db import get_engine, read_sql

logger = logging.getLogger(__name__)

# Fuentes (IDs fijos de silver.data_sources, ver catalogs.py)
SRC_X, SRC_BCE, SRC_MIRROR = 1, 3, 5
# Miembros especiales de 03_gold.sql: la parte del total BCE sin detalle por país ni partida
NO_REPORT_KEY = 9999
# Tolerancia del control "detalle preferido + sin reporte = BCE" (centavos de redondeo)
BCE_TOLERANCE_USD = 1.0

# Gramos por libra: para tallas expresadas en piezas por libra (hoy todas son por kg)
GRAMS_PER_LB = 453.592

# ------------------------------------------------------------------ SQL de cada dimensión
# Patrón INSERT ... SELECT con UPSERT (MySQL 8.0.19+):
#   INSERT INTO destino (cols)
#   SELECT * FROM (SELECT ... FROM silver...) AS src      ← la consulta va en una subconsulta
#   ON DUPLICATE KEY UPDATE col = src.col                 ← para poder nombrar sus columnas
# Si la llave ya existe, actualiza; si no, inserta. Re-ejecutar no duplica (idempotente).

DIM_SOURCE_SQL = """
INSERT INTO gold.dim_source
    (source_key, source_code, source_name, publisher,
     weight_unit, valuation_basis, is_simulated, is_reference)
SELECT * FROM (
    SELECT s.source_id                         AS source_key,
           s.source_code,
           s.source_name,
           s.publisher,
           s.weight_unit,
           s.valuation_basis,
           s.is_simulated,
           -- ADR-006: el BCE es la REFERENCIA oficial del total nacional
           (s.source_code = 'BCE')             AS is_reference
    FROM silver.data_sources AS s
) AS src
ON DUPLICATE KEY UPDATE
    source_code     = src.source_code,
    source_name     = src.source_name,
    publisher       = src.publisher,
    weight_unit     = src.weight_unit,
    valuation_basis = src.valuation_basis,
    is_simulated    = src.is_simulated,
    is_reference    = src.is_reference
"""

DIM_PRODUCT_SQL = """
INSERT INTO gold.dim_product
    (product_key, hs_code, hs_heading, product_category, category_name_es,
     product_name_es, product_name_en)
SELECT * FROM (
    SELECT p.product_id                        AS product_key,
           p.hs_code,
           p.hs_heading,                       -- en silver es columna generada; aquí se copia
           p.product_category,
           CASE p.product_category
                WHEN 'frozen'   THEN 'Congelado'
                WHEN 'prepared' THEN 'Preparado'
                ELSE 'Otro'
           END                                 AS category_name_es,
           p.product_name_es,
           p.product_name_en
    FROM silver.products AS p
) AS src
ON DUPLICATE KEY UPDATE
    hs_code          = src.hs_code,
    hs_heading       = src.hs_heading,
    product_category = src.product_category,
    category_name_es = src.category_name_es,
    product_name_es  = src.product_name_es,
    product_name_en  = src.product_name_en
"""

# Talla "20/30" = de 20 a 30 piezas por kg → pieza de 1000/30 a 1000/20 gramos.
# Grupo comercial (convención del proyecto, piezas por kg): Grande hasta 40,
# Mediana hasta 70, Pequeña más de 70.
DIM_SIZE_SQL = """
INSERT INTO gold.dim_size
    (size_key, size_code, min_count, max_count, min_weight_g, max_weight_g,
     size_group, sort_order)
SELECT * FROM (
    SELECT z.size_id                                              AS size_key,
           z.size_code,
           z.min_count,
           z.max_count,
           ROUND(g.grams_per_unit / z.max_count, 2)               AS min_weight_g,
           ROUND(g.grams_per_unit / z.min_count, 2)               AS max_weight_g,
           CASE WHEN z.max_count <= 40 THEN 'Grande'
                WHEN z.max_count <= 70 THEN 'Mediana'
                ELSE 'Pequeña'
           END                                                    AS size_group,
           z.sort_order
    FROM silver.sizes AS z
    -- Piezas por kg (1000 g) o por libra (453,592 g), según count_basis
    JOIN (SELECT 'pieces_per_kg' AS count_basis, 1000      AS grams_per_unit
          UNION ALL
          SELECT 'pieces_per_lb',                :g_per_lb) AS g
      ON g.count_basis = z.count_basis
) AS src
ON DUPLICATE KEY UPDATE
    size_code    = src.size_code,
    min_count    = src.min_count,
    max_count    = src.max_count,
    min_weight_g = src.min_weight_g,
    max_weight_g = src.max_weight_g,
    size_group   = src.size_group,
    sort_order   = src.sort_order
"""

# País con su región COPIADA dentro (desnormalización: Power BI no necesita otra tabla)
# y la agrupación de mercados que usan los reportes del sector camaronero.
DIM_COUNTRY_SQL = """
INSERT INTO gold.dim_country
    (country_key, iso3, m49_code, country_name_es, country_name_en,
     region_code_m49, region_name_es, region_name_en, cna_market_group)
SELECT * FROM (
    SELECT c.country_id                                    AS country_key,
           c.iso3,
           c.m49_code,
           c.country_name_es,
           c.country_name_en,
           COALESCE(r.m49_region_code, '000')              AS region_code_m49,
           COALESCE(r.region_name_es, 'Desconocida')       AS region_name_es,
           COALESCE(r.region_name_en, 'Unknown')           AS region_name_en,
           CASE
                WHEN c.iso3 = 'CHN'                         THEN 'China'
                WHEN c.iso3 = 'USA'                         THEN 'EE. UU.'
                WHEN r.m49_region_code = '150'              THEN 'Europa'
                WHEN r.m49_region_code = '142'              THEN 'Resto de Asia'
                WHEN r.m49_region_code IN ('419', '021')    THEN 'Resto de América'
                WHEN r.m49_region_code = '002'              THEN 'África'
                WHEN r.m49_region_code = '009'              THEN 'Oceanía'
                ELSE 'Otros'
           END                                             AS cna_market_group
    FROM silver.countries AS c
    LEFT JOIN silver.regions AS r ON r.region_id = c.region_id
) AS src
ON DUPLICATE KEY UPDATE
    iso3             = src.iso3,
    m49_code         = src.m49_code,
    country_name_es  = src.country_name_es,
    country_name_en  = src.country_name_en,
    region_code_m49  = src.region_code_m49,
    region_name_es   = src.region_name_es,
    region_name_en   = src.region_name_en,
    cna_market_group = src.cna_market_group
"""

# Piscina con los datos de su finca COPIADOS dentro (SIMULADO)
DIM_POND_SQL = """
INSERT INTO gold.dim_pond
    (pond_key, pond_code, farm_code, farm_name, province, area_ha, area_band, is_simulated)
SELECT * FROM (
    SELECT p.pond_id                                       AS pond_key,
           p.pond_code,
           f.farm_code,
           f.farm_name,
           f.province,
           p.area_ha,
           CASE WHEN p.area_ha IS NULL THEN 'Desconocida'
                WHEN p.area_ha < 5     THEN '< 5 ha'
                WHEN p.area_ha <= 10   THEN '5-10 ha'
                ELSE '> 10 ha'
           END                                             AS area_band,
           TRUE                                            AS is_simulated
    FROM silver.ponds AS p
    JOIN silver.farms AS f ON f.farm_id = p.farm_id
) AS src
ON DUPLICATE KEY UPDATE
    pond_code    = src.pond_code,
    farm_code    = src.farm_code,
    farm_name    = src.farm_name,
    province     = src.province,
    area_ha      = src.area_ha,
    area_band    = src.area_band,
    is_simulated = src.is_simulated
"""

# (nombre, tabla gold, tabla silver, (llave gold, llave silver), SQL, parámetros)
# El orden importa poco aquí (las dimensiones no dependen entre sí), pero TODAS deben
# cargarse antes que los hechos por las llaves foráneas.
DIMENSIONS = [
    ("dim_source", "gold.dim_source", "silver.data_sources", ("source_key", "source_id"),
     DIM_SOURCE_SQL, {}),
    ("dim_product", "gold.dim_product", "silver.products", ("product_key", "product_id"),
     DIM_PRODUCT_SQL, {}),
    ("dim_size", "gold.dim_size", "silver.sizes", ("size_key", "size_id"),
     DIM_SIZE_SQL, {"g_per_lb": GRAMS_PER_LB}),
    ("dim_country", "gold.dim_country", "silver.countries", ("country_key", "country_id"),
     DIM_COUNTRY_SQL, {}),
    ("dim_pond", "gold.dim_pond", "silver.ponds", ("pond_key", "pond_id"),
     DIM_POND_SQL, {}),
]


# ------------------------------------------------------------------ fact_exports (Paso 7.4)
# Reconstrucción completa (decisión 3 de la Fase 7): se borra y se vuelve a insertar
# en UNA transacción. Si un control falla, ROLLBACK: gold queda como estaba.

# 1) Detalle por país de las dos fuentes. Regla is_preferred (ADR-006):
#    - Comtrade X (Ecuador) es la preferida en todos sus meses;
#    - el espejo es preferido SOLO en los meses en que Ecuador no publicó Y que el BCE
#      ya publicó (un mes sin total oficial sería parcial: no va al dashboard).
#    date_key = AAAAMM01 calculado con aritmética (sin DATE_FORMAT).
FACT_EXPORTS_DETAIL_SQL = """
INSERT INTO gold.fact_exports
    (date_key, country_key, product_key, source_key, is_preferred,
     value_usd, weight_kg, weight_lb, source_batch_id)
SELECT YEAR(e.period_date) * 10000 + MONTH(e.period_date) * 100 + 1   AS date_key,
       e.country_id                                                  AS country_key,
       e.product_id                                                  AS product_key,
       e.source_id                                                   AS source_key,
       CASE WHEN e.source_id = :x       THEN 1
            WHEN xm.period_date IS NULL
             AND bm.period_date IS NOT NULL THEN 1  -- mes sin X: el espejo cubre el hueco
            ELSE 0
       END                                                           AS is_preferred,
       e.value_usd,
       e.weight_kg,
       ROUND(e.weight_lb, 3)                                         AS weight_lb,
       e.source_batch_id
FROM silver.export_monthly AS e
LEFT JOIN (SELECT DISTINCT period_date
           FROM silver.export_monthly
           WHERE source_id = :x) AS xm
       ON xm.period_date = e.period_date
LEFT JOIN (SELECT DISTINCT period_date
           FROM silver.source_reconciliation
           WHERE comparison_level = 'total') AS bm
       ON bm.period_date = e.period_date
WHERE e.source_id IN (:x, :mirror)
"""

# 2) 'Sin reporte por país' = total BCE − detalle preferido del mes (puede ser negativo,
#    ver chk_fexp_value). Solo meses que el BCE publicó. Va con la fuente BCE.
FACT_EXPORTS_NO_REPORT_SQL = """
INSERT INTO gold.fact_exports
    (date_key, country_key, product_key, source_key, is_preferred,
     value_usd, weight_kg, weight_lb, source_batch_id)
SELECT b.date_key,
       :no_report, :no_report, :bce, 1,
       ROUND(b.bce_usd - COALESCE(d.detail_usd, 0), 2),
       NULL, NULL,                                  -- el cuadro del BCE no trae peso
       :bce_batch
FROM (SELECT YEAR(period_date) * 10000 + MONTH(period_date) * 100 + 1 AS date_key,
             reference_value_usd                                    AS bce_usd
      FROM silver.source_reconciliation
      WHERE comparison_level = 'total' AND source_id = :x) AS b
LEFT JOIN (SELECT date_key, SUM(value_usd) AS detail_usd
           FROM gold.fact_exports
           WHERE is_preferred = 1
           GROUP BY date_key) AS d
       ON d.date_key = b.date_key
"""

# Lote vigente del BCE (para trazabilidad de la fila 'Sin reporte por país')
BCE_BATCH_SQL = """
SELECT batch_id FROM bronze.bce_raw
WHERE table_code = '3.1.2' AND series_name = 'Camarón'
ORDER BY extracted_at DESC, raw_id DESC
LIMIT 1
"""

# Controles después de cargar (cada uno debe devolver 0 filas o el número esperado)
CHECK_BCE_SQL = """
SELECT b.date_key, b.bce_usd, p.preferred_usd
FROM (SELECT YEAR(period_date) * 10000 + MONTH(period_date) * 100 + 1 AS date_key,
             reference_value_usd AS bce_usd
      FROM silver.source_reconciliation
      WHERE comparison_level = 'total' AND source_id = :x) AS b
LEFT JOIN (SELECT date_key, SUM(value_usd) AS preferred_usd
           FROM gold.fact_exports WHERE is_preferred = 1 GROUP BY date_key) AS p
       ON p.date_key = b.date_key
WHERE p.preferred_usd IS NULL OR ABS(p.preferred_usd - b.bce_usd) > :tol
"""

# Ninguna cifra preferida en meses sin total del BCE (el total anual debe ser el del BCE)
CHECK_OUTSIDE_BCE_SQL = """
SELECT f.date_key, COUNT(*) AS filas
FROM gold.fact_exports AS f
LEFT JOIN (SELECT DISTINCT YEAR(period_date) * 10000 + MONTH(period_date) * 100 + 1 AS date_key
           FROM silver.source_reconciliation
           WHERE comparison_level = 'total') AS b
       ON b.date_key = f.date_key
WHERE f.is_preferred = 1 AND b.date_key IS NULL
GROUP BY f.date_key
"""

CHECK_ONE_PREFERRED_SQL = """
SELECT date_key, country_key, product_key, COUNT(*) AS preferidas
FROM gold.fact_exports
WHERE is_preferred = 1
GROUP BY date_key, country_key, product_key
HAVING COUNT(*) > 1
"""


def build_fact_exports() -> pd.DataFrame:
    """Reconstruye gold.fact_exports y verifica que cuadre con silver y con el BCE."""
    params = {"x": SRC_X, "mirror": SRC_MIRROR, "bce": SRC_BCE,
              "no_report": NO_REPORT_KEY, "tol": BCE_TOLERANCE_USD}
    start = time.perf_counter()
    with get_engine().begin() as conn:
        params["bce_batch"] = conn.execute(text(BCE_BATCH_SQL)).scalar_one()

        deleted = conn.execute(text("DELETE FROM gold.fact_exports")).rowcount
        detail = conn.execute(text(FACT_EXPORTS_DETAIL_SQL), params).rowcount
        no_report = conn.execute(text(FACT_EXPORTS_NO_REPORT_SQL), params).rowcount
        logger.info("fact_exports | borradas %s | detalle %s | sin reporte %s",
                    deleted, detail, no_report)

        # Control 1: cada fila de silver (fuentes X y espejo) llegó a gold
        silver_rows = conn.execute(text(
            "SELECT COUNT(*) FROM silver.export_monthly WHERE source_id IN (:x, :mirror)"
        ), params).scalar_one()
        if detail != silver_rows:
            raise ValueError(f"fact_exports: {detail} filas de detalle vs {silver_rows} en silver")

        # Control 2: en cada mes del BCE, detalle preferido + sin reporte = total BCE
        bad = conn.execute(text(CHECK_BCE_SQL), params).all()
        if bad:
            raise ValueError(f"fact_exports: {len(bad)} meses no cuadran con el BCE, "
                             f"ej. {bad[:3]}")

        # Control 3: nada preferido fuera de los meses del BCE
        outside = conn.execute(text(CHECK_OUTSIDE_BCE_SQL)).all()
        if outside:
            raise ValueError(f"fact_exports: cifras preferidas en meses sin BCE: {outside[:3]}")

        # Control 4: a lo más UNA cifra preferida por mes, país y partida
        dup = conn.execute(text(CHECK_ONE_PREFERRED_SQL)).all()
        if dup:
            raise ValueError(f"fact_exports: {len(dup)} combinaciones con 2 cifras preferidas")

    logger.info("fact_exports reconstruida y verificada en %.2f s", time.perf_counter() - start)
    return pd.DataFrame([{"tabla": "fact_exports", "borradas": deleted, "detalle": detail,
                          "sin_reporte": no_report, "total": detail + no_report}])


# ------------------------------------------------------------------ fact_production (Paso 7.5)
# Grano: UN ciclo de cultivo (SIMULADO). Solo hechos ADITIVOS: FCR, supervivencia,
# lb/ha, costo/lb y margen se calculan como medidas (Σ numerador ÷ Σ denominador).
# Fechas AAAAMMDD por aritmética: dim_date cumple dos roles (siembra y cosecha).
FACT_PRODUCTION_SQL = """
INSERT INTO gold.fact_production
    (cycle_key, cycle_code, pond_key, stocking_date_key, harvest_date_key, size_key,
     days_of_culture, num_harvests, area_ha, larvae_stocked, harvested_animals,
     harvested_kg, harvested_lb, feed_kg,
     cost_larvae_usd, cost_feed_usd, cost_energy_usd, cost_labor_usd, total_cost_usd,
     revenue_usd)
SELECT c.cycle_id                                                          AS cycle_key,
       c.cycle_code,
       c.pond_id                                                           AS pond_key,
       YEAR(c.stocking_date) * 10000 + MONTH(c.stocking_date) * 100
           + DAY(c.stocking_date)                                          AS stocking_date_key,
       YEAR(c.final_harvest_date) * 10000 + MONTH(c.final_harvest_date) * 100
           + DAY(c.final_harvest_date)                                     AS harvest_date_key,
       COALESCE(fh.size_id, 0)                                             AS size_key,
       c.days_of_culture,                                  -- columna generada en silver
       h.num_harvests,
       p.area_ha,
       c.larvae_stocked,
       h.harvested_animals,
       h.harvested_kg,
       h.harvested_lb,
       c.feed_kg,
       COALESCE(k.larvae, 0), COALESCE(k.feed, 0), COALESCE(k.energy, 0), COALESCE(k.labor, 0),
       COALESCE(k.larvae, 0) + COALESCE(k.feed, 0)
           + COALESCE(k.energy, 0) + COALESCE(k.labor, 0)                  AS total_cost_usd,
       COALESCE(r.revenue_usd, 0)                                          AS revenue_usd
FROM silver.production_cycles AS c
JOIN silver.ponds AS p ON p.pond_id = c.pond_id
-- Cosechas del ciclo: piezas = kg × 1000 ÷ peso promedio (g)
JOIN (SELECT cycle_id,
             COUNT(*)                                        AS num_harvests,
             ROUND(SUM(harvested_kg * 1000 / avg_weight_g))  AS harvested_animals,
             SUM(harvested_kg)                               AS harvested_kg,
             SUM(harvested_lb)                               AS harvested_lb
      FROM silver.harvests
      GROUP BY cycle_id) AS h ON h.cycle_id = c.cycle_id
-- Talla de la cosecha FINAL
LEFT JOIN silver.harvests AS fh
       ON fh.cycle_id = c.cycle_id AND fh.harvest_type = 'final'
-- Pivote de costos: 4 filas (largo) → 4 columnas (ancho)
LEFT JOIN (SELECT cycle_id,
                  SUM(CASE WHEN cost_type = 'larvae' THEN amount_usd ELSE 0 END) AS larvae,
                  SUM(CASE WHEN cost_type = 'feed'   THEN amount_usd ELSE 0 END) AS feed,
                  SUM(CASE WHEN cost_type = 'energy' THEN amount_usd ELSE 0 END) AS energy,
                  SUM(CASE WHEN cost_type = 'labor'  THEN amount_usd ELSE 0 END) AS labor
           FROM silver.cycle_costs
           GROUP BY cycle_id) AS k ON k.cycle_id = c.cycle_id
-- Ingreso del ciclo (revenue_usd ya lo calculó MySQL en silver: libras × precio)
LEFT JOIN (SELECT hv.cycle_id, SUM(sa.revenue_usd) AS revenue_usd
           FROM silver.sales AS sa
           JOIN silver.harvests AS hv ON hv.harvest_id = sa.harvest_id
           GROUP BY hv.cycle_id) AS r ON r.cycle_id = c.cycle_id
"""

# Control: los totales de gold deben ser idénticos a los de silver
PRODUCTION_TOTALS_SQL = """
SELECT (SELECT COUNT(*)                FROM silver.production_cycles) AS s_ciclos,
       (SELECT COUNT(*)                FROM gold.fact_production)    AS g_ciclos,
       (SELECT SUM(harvested_kg)       FROM silver.harvests)         AS s_kg,
       (SELECT SUM(harvested_kg)       FROM gold.fact_production)    AS g_kg,
       (SELECT SUM(amount_usd)         FROM silver.cycle_costs)      AS s_costo,
       (SELECT SUM(total_cost_usd)     FROM gold.fact_production)    AS g_costo,
       (SELECT SUM(revenue_usd)        FROM silver.sales)            AS s_ingreso,
       (SELECT SUM(revenue_usd)        FROM gold.fact_production)    AS g_ingreso
"""


def build_fact_production() -> pd.DataFrame:
    """Reconstruye gold.fact_production y verifica que cuadre con silver."""
    start = time.perf_counter()
    with get_engine().begin() as conn:
        deleted = conn.execute(text("DELETE FROM gold.fact_production")).rowcount
        inserted = conn.execute(text(FACT_PRODUCTION_SQL)).rowcount
        t = conn.execute(text(PRODUCTION_TOTALS_SQL)).mappings().one()

        checks = {"ciclos": (t["s_ciclos"], t["g_ciclos"]), "kg": (t["s_kg"], t["g_kg"]),
                  "costo": (t["s_costo"], t["g_costo"]),
                  "ingreso": (t["s_ingreso"], t["g_ingreso"])}
        bad = {k: v for k, v in checks.items() if abs(float(v[0] or 0) - float(v[1] or 0)) > 0.01}
        if bad:
            raise ValueError(f"fact_production no cuadra con silver (silver, gold): {bad}")

    logger.info("fact_production | borradas %s | insertadas %s | verificada en %.2f s",
                deleted, inserted, time.perf_counter() - start)
    return pd.DataFrame([{"tabla": "fact_production", "borradas": deleted, "detalle": inserted,
                          "sin_reporte": 0, "total": inserted}])


# ------------------------------------------------------------------ ejecución
def _count(conn: Connection, table: str) -> int:
    # table viene de la lista fija DIMENSIONS (no de un usuario): es seguro en el texto
    return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def load_dimension(conn: Connection, name: str, gold_table: str, silver_table: str,
                   key: tuple[str, str], sql: str, params: dict) -> dict:
    """Ejecuta el upsert de una dimensión y verifica que no falte ninguna fila de silver."""
    before = _count(conn, gold_table)
    conn.execute(text(sql), params)
    after = _count(conn, gold_table)
    expected = _count(conn, silver_table)

    # Control: toda fila de silver debe tener su llave en gold
    missing = conn.execute(text(
        f"SELECT COUNT(*) FROM {silver_table} AS s "
        f"LEFT JOIN {gold_table} AS g ON g.{key[0]} = s.{key[1]} "
        f"WHERE g.{key[0]} IS NULL"
    )).scalar_one()
    if missing:
        raise ValueError(f"{name}: {missing} filas de {silver_table} no llegaron a gold "
                         "(¿count_basis desconocido en sizes o piscina sin finca?)")

    result = {"dimension": name, "silver": expected, "gold_total": after,
              "nuevas": after - before}
    logger.info("%-11s | silver %3s | gold %3s (incluye miembros 0/9999) | nuevas %s",
                name, expected, after, after - before)
    return result


def build_dimensions() -> pd.DataFrame:
    """Carga las dimensiones en UNA transacción: si una falla, no queda ninguna a medias."""
    start = time.perf_counter()
    with get_engine().begin() as conn:
        results = [load_dimension(conn, *dim) for dim in DIMENSIONS]
    logger.info("Dimensiones cargadas en %.2f s", time.perf_counter() - start)
    return pd.DataFrame(results)


def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    # Primero las dimensiones (los hechos dependen de ellas por las llaves foráneas)
    dims = build_dimensions()
    facts = pd.concat([build_fact_exports(), build_fact_production()], ignore_index=True)
    return dims, facts


# Ejecutar: python -m src.build_gold
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    summary, facts = run()
    print("\nDimensiones de gold:")
    print(summary.to_string(index=False))
    print("\nHechos de gold (reconstrucción completa):")
    print(facts.to_string(index=False))

    print("\nfact_exports por año — cifras PREFERIDAS (lo que verá el dashboard), millones USD:")
    print(read_sql(
        """
        SELECT d.calendar_year                                                  AS anio,
               ROUND(SUM(CASE WHEN f.source_key = 1 THEN f.value_usd END) / 1e6, 1) AS ecuador_x,
               ROUND(SUM(CASE WHEN f.source_key = 5 THEN f.value_usd END) / 1e6, 1) AS espejo,
               ROUND(SUM(CASE WHEN f.source_key = 3 THEN f.value_usd END) / 1e6, 1) AS sin_reporte,
               ROUND(SUM(f.value_usd) / 1e6, 1)                                 AS total,
               CAST(SUM(f.source_key = 3 AND f.value_usd < 0) AS SIGNED)        AS meses_neg
        FROM gold.fact_exports AS f
        JOIN gold.dim_date AS d ON d.date_key = f.date_key
        WHERE f.is_preferred = 1
        GROUP BY d.calendar_year
        ORDER BY anio
        """
    ).to_string(index=False))

    print("\nfact_production por año de cosecha (SIMULADO) — ratios calculados como Σ/Σ:")
    print(read_sql(
        """
        SELECT d.calendar_year                                              AS anio,
               CAST(COUNT(*) AS SIGNED)                                     AS ciclos,
               ROUND(SUM(f.harvested_lb) / 1e6, 2)                          AS millones_lb,
               ROUND(SUM(f.total_cost_usd) / 1e6, 2)                        AS costo_musd,
               ROUND(SUM(f.revenue_usd) / 1e6, 2)                           AS ingreso_musd,
               ROUND((SUM(f.revenue_usd) - SUM(f.total_cost_usd))
                     / SUM(f.revenue_usd) * 100, 1)                         AS margen_pct,
               ROUND(SUM(f.feed_kg) / SUM(f.harvested_kg), 2)               AS fcr,
               ROUND(SUM(f.harvested_animals) / SUM(f.larvae_stocked) * 100, 1)
                                                                            AS supervivencia_pct,
               ROUND(SUM(f.harvested_lb) / SUM(f.area_ha), 0)               AS lb_por_ha_ciclo
        FROM gold.fact_production AS f
        JOIN gold.dim_date AS d ON d.date_key = f.harvest_date_key
        GROUP BY d.calendar_year
        ORDER BY anio
        """
    ).to_string(index=False))

    print("\ndim_source:")
    print(read_sql("SELECT source_key, source_code, valuation_basis, is_simulated, "
                   "is_reference FROM gold.dim_source ORDER BY source_key").to_string(index=False))
    print("\ndim_product:")
    print(read_sql("SELECT product_key, hs_code, hs_heading, category_name_es, product_name_es "
                   "FROM gold.dim_product ORDER BY product_key").to_string(index=False))
    print("\ndim_size:")
    print(read_sql("SELECT size_key, size_code, min_weight_g, max_weight_g, size_group, "
                   "sort_order FROM gold.dim_size ORDER BY sort_order").to_string(index=False))

    print("\ndim_country — países por grupo de mercado (sin miembros 0/9999):")
    print(read_sql(
        "SELECT cna_market_group AS grupo, CAST(COUNT(*) AS SIGNED) AS paises, "
        "GROUP_CONCAT(iso3 ORDER BY iso3 SEPARATOR ' ') AS ejemplos "
        "FROM gold.dim_country WHERE country_key NOT IN (0, 9999) "
        "GROUP BY cna_market_group ORDER BY paises DESC"
    ).assign(ejemplos=lambda d: d["ejemplos"].str.slice(0, 60)).to_string(index=False))

    print("\ndim_pond — piscinas por finca y rango de área (SIMULADO):")
    print(read_sql(
        "SELECT farm_code, province, area_band, CAST(COUNT(*) AS SIGNED) AS piscinas, "
        "ROUND(SUM(area_ha), 2) AS hectareas "
        "FROM gold.dim_pond WHERE pond_key <> 0 "
        "GROUP BY farm_code, province, area_band ORDER BY farm_code, area_band"
    ).to_string(index=False))