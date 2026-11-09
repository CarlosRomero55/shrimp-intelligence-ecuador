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

Paso 7.2: dim_source, dim_product, dim_size.

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

# (nombre, tabla de gold, tabla de silver, SQL, parámetros)
DIMENSIONS = [
    ("dim_source", "gold.dim_source", "silver.data_sources", DIM_SOURCE_SQL, {}),
    ("dim_product", "gold.dim_product", "silver.products", DIM_PRODUCT_SQL, {}),
    ("dim_size", "gold.dim_size", "silver.sizes", DIM_SIZE_SQL, {"g_per_lb": GRAMS_PER_LB}),
]


# ------------------------------------------------------------------ ejecución
def _count(conn: Connection, table: str) -> int:
    # table viene de la lista fija DIMENSIONS (no de un usuario): es seguro en el texto
    return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def load_dimension(conn: Connection, name: str, gold_table: str, silver_table: str,
                   sql: str, params: dict) -> dict:
    """Ejecuta el upsert de una dimensión y verifica que no falte ninguna fila de silver."""
    before = _count(conn, gold_table)
    conn.execute(text(sql), params)
    after = _count(conn, gold_table)
    expected = _count(conn, silver_table)

    # Control: toda fila de silver debe tener su llave en gold
    key = {"dim_source": ("source_key", "source_id"),
           "dim_product": ("product_key", "product_id"),
           "dim_size": ("size_key", "size_id")}[name]
    missing = conn.execute(text(
        f"SELECT COUNT(*) FROM {silver_table} AS s "
        f"LEFT JOIN {gold_table} AS g ON g.{key[0]} = s.{key[1]} "
        f"WHERE g.{key[0]} IS NULL"
    )).scalar_one()
    if missing:
        raise ValueError(f"{name}: {missing} filas de {silver_table} no llegaron a gold "
                         "(¿count_basis desconocido en sizes?)")

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


def run() -> pd.DataFrame:
    return build_dimensions()


# Ejecutar: python -m src.build_gold
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    summary = run()
    print("\nDimensiones de gold:")
    print(summary.to_string(index=False))

    print("\ndim_source:")
    print(read_sql("SELECT source_key, source_code, valuation_basis, is_simulated, "
                   "is_reference FROM gold.dim_source ORDER BY source_key").to_string(index=False))
    print("\ndim_product:")
    print(read_sql("SELECT product_key, hs_code, hs_heading, category_name_es, product_name_es "
                   "FROM gold.dim_product ORDER BY product_key").to_string(index=False))
    print("\ndim_size:")
    print(read_sql("SELECT size_key, size_code, min_weight_g, max_weight_g, size_group, "
                   "sort_order FROM gold.dim_size ORDER BY sort_order").to_string(index=False))