-- =============================================================================
-- 04_api_views.sql — Vistas de gold para la API (Fase 11)
-- Proyecto: Shrimp Intelligence Ecuador
--
-- Una vista por endpoint. La API solo hace SELECT ... WHERE sobre estas vistas:
-- la lógica de negocio vive en SQL (un solo lugar), no en Python.
--   api_exports_monthly  → GET /exports?country=&year=
--   api_kpis_annual      → GET /kpis?year=
--   api_prices           → GET /prices?year=&market=
--   api_concentration    → GET /concentration?year=
--   api_pond_ranking     → GET /ponds/ranking?farm=      (finca SIMULADA)
--   api_reconciliation   → GET /reconciliation?level=
--
-- Columnas en inglés (convención de APIs REST); los cálculos son los de las
-- consultas validadas en la Fase 9 (sql/queries/q03, q04, q05, q07, q08, q11, q12).
-- Montos en USD sin redondear a millones: la API decide cómo mostrarlos.
--
-- Ejecutar en Workbench como root (el usuario del ETL no tiene CREATE VIEW).
-- Es idempotente: CREATE OR REPLACE VIEW no borra datos, solo redefine la vista.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. api_exports_monthly — mes × país × fuente, solo cifras preferidas
--    Incluye "Sin reporte por país" (iso3 = 'XXX'): sumando todo el mes da el BCE.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.api_exports_monthly AS
SELECT d.period_label                AS period,          -- '2025-03'
       d.calendar_year               AS year,
       d.calendar_month              AS month,
       c.iso3,
       c.country_name_es             AS country,
       c.cna_market_group            AS market_group,
       s.source_code                 AS source,
       s.valuation_basis,                               -- FOB | CIF
       SUM(f.value_usd)              AS value_usd,
       SUM(f.weight_lb)              AS weight_lb
FROM gold.fact_exports AS f
JOIN gold.dim_date     AS d ON d.date_key    = f.date_key
JOIN gold.dim_country  AS c ON c.country_key = f.country_key
JOIN gold.dim_source   AS s ON s.source_key  = f.source_key
WHERE f.is_preferred = 1
GROUP BY d.period_label, d.calendar_year, d.calendar_month,
         c.iso3, c.country_name_es, c.cna_market_group,
         s.source_code, s.valuation_basis;


-- -----------------------------------------------------------------------------
-- 2. api_kpis_annual — un registro por año (base: q04 + cobertura de q03)
--    value_usd = total BCE del año · value_yoy_pct compara los MISMOS meses
--    usd_per_lb solo con filas que tienen país y peso · price_yoy_comparable = 0
--    si cambia la fuente del precio (FOB vs CIF).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.api_kpis_annual AS
WITH monthly AS (
    SELECT d.calendar_year AS yr, d.calendar_month AS mon,
           SUM(f.value_usd)                                                  AS usd,
           SUM(CASE WHEN f.country_key <> 9999 THEN f.value_usd ELSE 0 END)  AS usd_with_country
    FROM gold.fact_exports AS f
    JOIN gold.dim_date AS d ON d.date_key = f.date_key
    WHERE f.is_preferred = 1
    GROUP BY d.calendar_year, d.calendar_month
),
yearly AS (
    SELECT yr, COUNT(*) AS months, MAX(mon) AS last_month,
           SUM(usd) AS usd, SUM(usd_with_country) AS usd_with_country
    FROM monthly
    GROUP BY yr
),
same_period_previous AS (
    SELECT y.yr, SUM(m.usd) AS usd_previous
    FROM yearly AS y
    JOIN monthly AS m ON m.yr = y.yr - 1 AND m.mon <= y.last_month
    GROUP BY y.yr
),
price AS (
    SELECT d.calendar_year                                    AS yr,
           SUM(f.value_usd)                                   AS usd_with_weight,
           SUM(f.weight_lb)                                   AS lb,
           CASE WHEN MIN(s.source_code) = MAX(s.source_code)
                THEN MIN(s.source_code) ELSE 'MIXTA' END      AS price_source
    FROM gold.fact_exports AS f
    JOIN gold.dim_date   AS d ON d.date_key   = f.date_key
    JOIN gold.dim_source AS s ON s.source_key = f.source_key
    WHERE f.is_preferred = 1
      AND f.country_key <> 9999
      AND f.weight_lb > 0
    GROUP BY d.calendar_year
),
combined AS (
    SELECT y.yr, y.months, y.usd, y.usd_with_country, sp.usd_previous,
           p.lb, p.usd_with_weight, p.price_source,
           p.usd_with_weight / NULLIF(p.lb, 0)                            AS usd_lb,
           LAG(p.usd_with_weight / NULLIF(p.lb, 0)) OVER (ORDER BY y.yr)  AS usd_lb_previous,
           LAG(p.price_source)                      OVER (ORDER BY y.yr)  AS price_source_previous
    FROM yearly AS y
    LEFT JOIN same_period_previous AS sp ON sp.yr = y.yr
    LEFT JOIN price                AS p  ON p.yr  = y.yr
)
SELECT yr                                                        AS year,
       months,
       ROUND(usd, 2)                                             AS value_usd,
       ROUND(100 * (usd / NULLIF(usd_previous, 0) - 1), 1)       AS value_yoy_pct,
       ROUND(lb, 0)                                              AS weight_lb_identified,
       ROUND(usd_lb, 3)                                          AS usd_per_lb,
       ROUND(100 * (usd_lb / NULLIF(usd_lb_previous, 0) - 1), 1) AS price_yoy_pct,
       price_source,
       CASE WHEN price_source_previous IS NULL       THEN NULL
            WHEN price_source = price_source_previous THEN 1
            ELSE 0 END                                           AS price_yoy_comparable,
       ROUND(100 * usd_with_country / usd, 1)                    AS country_coverage_pct,
       ROUND(100 * usd_with_weight / usd, 1)                     AS weight_coverage_pct
FROM combined;


-- -----------------------------------------------------------------------------
-- 3. api_prices — año × grupo de mercado, USD por libra (base: q05)
--    SOLO lo que declara Ecuador (UN Comtrade X, FOB): el espejo es CIF y no se
--    mezcla. Incluye una fila 'Total' por año. vs_china_pct = cuánto más paga
--    ese mercado que China (Σ valor ÷ Σ libras, nunca promedio de precios).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.api_prices AS
WITH base AS (
    SELECT d.calendar_year AS yr, f.date_key, c.cna_market_group AS market_group,
           f.value_usd, f.weight_lb
    FROM gold.fact_exports AS f
    JOIN gold.dim_date    AS d ON d.date_key    = f.date_key
    JOIN gold.dim_country AS c ON c.country_key = f.country_key
    JOIN gold.dim_source  AS s ON s.source_key  = f.source_key
    WHERE s.source_code = 'UN_COMTRADE'          -- Ecuador como exportador (FOB)
      AND f.weight_lb > 0
),
grouped AS (
    SELECT yr, market_group,
           COUNT(DISTINCT date_key) AS months,
           SUM(value_usd)           AS usd,
           SUM(weight_lb)           AS lb
    FROM base
    GROUP BY yr, market_group
    UNION ALL
    SELECT yr, 'Total',
           COUNT(DISTINCT date_key), SUM(value_usd), SUM(weight_lb)
    FROM base
    GROUP BY yr
)
SELECT yr                                              AS year,
       market_group,
       months,
       ROUND(usd, 2)                                   AS value_usd,
       ROUND(lb, 0)                                    AS weight_lb,
       ROUND(usd / lb, 3)                              AS usd_per_lb,
       ROUND(100 * ((usd / lb)
             / MAX(CASE WHEN market_group = 'China' THEN usd / lb END)
                   OVER (PARTITION BY yr) - 1), 1)     AS vs_china_pct
FROM grouped;


-- -----------------------------------------------------------------------------
-- 4. api_concentration — HHI por año (base: q03)
--    HHI = Σ (participación %)² sobre países identificados.
--    is_comparable = 0 si menos del 80 % del valor tiene país (2025–2026: falta China).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.api_concentration AS
WITH by_country AS (
    SELECT d.calendar_year AS yr, c.country_name_es AS country, SUM(f.value_usd) AS usd
    FROM gold.fact_exports AS f
    JOIN gold.dim_date     AS d ON d.date_key    = f.date_key
    JOIN gold.dim_country  AS c ON c.country_key = f.country_key
    WHERE f.is_preferred = 1
      AND f.country_key NOT IN (0, 9999)
    GROUP BY d.calendar_year, c.country_name_es
),
shares AS (
    SELECT yr, country,
           100 * usd / SUM(usd) OVER (PARTITION BY yr)              AS share_pct,
           ROW_NUMBER() OVER (PARTITION BY yr ORDER BY usd DESC)    AS position
    FROM by_country
),
hhi AS (
    SELECT yr,
           SUM(share_pct * share_pct)                               AS hhi,
           COUNT(*)                                                 AS countries,
           MAX(CASE WHEN position = 1 THEN country END)             AS top_country,
           MAX(CASE WHEN position = 1 THEN share_pct END)           AS top_country_pct,
           SUM(CASE WHEN position <= 3 THEN share_pct END)          AS top3_pct
    FROM shares
    GROUP BY yr
),
coverage AS (
    SELECT d.calendar_year AS yr,
           100 * SUM(CASE WHEN f.country_key <> 9999 THEN f.value_usd ELSE 0 END)
               / SUM(f.value_usd)                                   AS coverage_pct
    FROM gold.fact_exports AS f
    JOIN gold.dim_date AS d ON d.date_key = f.date_key
    WHERE f.is_preferred = 1
    GROUP BY d.calendar_year
)
SELECT h.yr                                                         AS year,
       ROUND(h.hhi, 0)                                              AS hhi,
       CASE WHEN h.hhi < 1500 THEN 'Poco concentrado'
            WHEN h.hhi < 2500 THEN 'Moderado'
            ELSE 'Muy concentrado' END                              AS level,
       h.top_country,
       ROUND(h.top_country_pct, 1)                                  AS top_country_pct,
       ROUND(h.top3_pct, 1)                                         AS top3_pct,
       h.countries,
       ROUND(cv.coverage_pct, 1)                                    AS coverage_pct,
       CASE WHEN cv.coverage_pct < 80 THEN 0 ELSE 1 END             AS is_comparable
FROM hhi AS h
JOIN coverage AS cv ON cv.yr = h.yr;


-- -----------------------------------------------------------------------------
-- 5. api_pond_ranking — una fila por piscina (base: q07 + q08) — DATOS SIMULADOS
--    margin_quartile 1 = 25 % más rentable · fcr_rank 1 = mejor conversión
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.api_pond_ranking AS
WITH pond AS (
    SELECT p.farm_code, p.farm_name, p.pond_code, p.area_band, p.is_simulated,
           COUNT(*)                    AS cycles,
           SUM(f.revenue_usd)          AS revenue,
           SUM(f.total_cost_usd)       AS cost,
           SUM(f.harvested_lb)         AS lb,
           SUM(f.harvested_kg)         AS kg,
           SUM(f.feed_kg)              AS feed_kg,
           SUM(f.harvested_animals)    AS animals,
           SUM(f.larvae_stocked)       AS larvae
    FROM gold.fact_production AS f
    JOIN gold.dim_pond        AS p ON p.pond_key = f.pond_key
    GROUP BY p.farm_code, p.farm_name, p.pond_code, p.area_band, p.is_simulated
)
SELECT farm_code,
       farm_name,
       pond_code,
       area_band,
       cycles,
       ROUND(revenue, 2)                                              AS revenue_usd,
       ROUND(revenue - cost, 2)                                       AS profit_usd,
       ROUND(100 * (revenue - cost) / revenue, 1)                     AS margin_pct,
       NTILE(4) OVER (ORDER BY (revenue - cost) / revenue DESC)       AS margin_quartile,
       ROUND(cost / lb, 3)                                            AS cost_per_lb,
       ROUND(revenue / lb, 3)                                         AS price_per_lb,
       ROUND(feed_kg / kg, 3)                                         AS fcr,
       DENSE_RANK() OVER (ORDER BY ROUND(feed_kg / kg, 3))            AS fcr_rank,
       ROUND(100 * animals / larvae, 1)                               AS survival_pct,
       is_simulated
FROM pond;


-- -----------------------------------------------------------------------------
-- 6. api_reconciliation — conciliación de fuentes en dos niveles (base: q11 + q12)
--    level = 'year'    : cada fuente (X o espejo) vs BCE, por año
--    level = 'country' : espejo (lo que declara el comprador) vs Ecuador (X), por país,
--                        solo meses comparables y países con ≥ 12 meses.
--    gap_pct = valor ÷ referencia − 1 (ponderado). Las causas son HIPÓTESIS (ADR-006).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.api_reconciliation AS
WITH year_level AS (
    SELECT 'year'                                                   AS level,
           YEAR(period_date)                                        AS yr,
           'TOT'                                                    AS iso3,
           'Total nacional'                                         AS country,
           'Total nacional'                                         AS market_group,
           source_code                                              AS source,
           MAX(reference_source_code)                               AS reference_source,
           CAST(SUM(value_usd IS NOT NULL) AS SIGNED)               AS months,
           SUM(value_usd)                                           AS value_usd,
           SUM(CASE WHEN value_usd IS NOT NULL THEN reference_value_usd END) AS reference_value_usd,
           NULL                                                     AS median_gap_pct,
           MIN(gap_pct)                                             AS worst_month_gap_pct,
           MAX(gap_pct)                                             AS best_month_gap_pct
    FROM gold.v_reconciliation
    WHERE comparison_level = 'total'
    GROUP BY YEAR(period_date), source_code
),
comparable AS (
    SELECT iso3, country_name_es, cna_market_group, source_code, reference_source_code,
           gap_pct, value_usd, reference_value_usd
    FROM gold.v_reconciliation
    WHERE comparison_level = 'country'
      AND is_comparable = 1
),
ordered AS (
    SELECT c.*,
           ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY gap_pct) AS rn,
           COUNT(*)     OVER (PARTITION BY iso3)                  AS n
    FROM comparable AS c
),
median AS (
    SELECT iso3, AVG(gap_pct) AS median_gap
    FROM ordered
    WHERE rn IN (FLOOR((n + 1) / 2), FLOOR((n + 2) / 2))
    GROUP BY iso3
),
country_level AS (
    SELECT 'country'                       AS level,
           NULL                            AS yr,
           c.iso3,
           MAX(c.country_name_es)          AS country,
           MAX(c.cna_market_group)         AS market_group,
           MAX(c.source_code)              AS source,
           MAX(c.reference_source_code)    AS reference_source,
           COUNT(*)                        AS months,
           SUM(c.value_usd)                AS value_usd,
           SUM(c.reference_value_usd)      AS reference_value_usd,
           MAX(m.median_gap)               AS median_gap_pct,
           MIN(c.gap_pct)                  AS worst_month_gap_pct,
           MAX(c.gap_pct)                  AS best_month_gap_pct
    FROM comparable AS c
    JOIN median AS m ON m.iso3 = c.iso3
    GROUP BY c.iso3
    HAVING COUNT(*) >= 12
),
both_levels AS (
    SELECT * FROM year_level
    UNION ALL
    SELECT * FROM country_level
)
SELECT level,
       yr                                                            AS year,
       iso3,
       country,
       market_group,
       source,
       reference_source,
       months,
       ROUND(value_usd, 2)                                           AS value_usd,
       ROUND(reference_value_usd, 2)                                 AS reference_value_usd,
       ROUND(100 * (value_usd / NULLIF(reference_value_usd, 0) - 1), 1) AS gap_pct,
       ROUND(median_gap_pct, 1)                                      AS median_gap_pct,
       ROUND(worst_month_gap_pct, 1)                                 AS worst_month_gap_pct,
       ROUND(best_month_gap_pct, 1)                                  AS best_month_gap_pct,
       CASE WHEN level = 'year' THEN NULL
            WHEN ABS(100 * (value_usd / reference_value_usd - 1)) <= 5 THEN 'Coinciden (±5 %)'
            WHEN 100 * (value_usd / reference_value_usd - 1) > 15   THEN 'Espejo mucho mayor (asimetría)'
            WHEN 100 * (value_usd / reference_value_usd - 1) > 5    THEN 'Espejo mayor, coherente con CIF'
            ELSE 'Espejo menor (asimetría)' END                      AS classification
FROM both_levels;


-- -----------------------------------------------------------------------------
-- Verificación (resultado esperado: 6 vistas y los totales del BCE por año)
-- -----------------------------------------------------------------------------
-- SELECT TABLE_NAME FROM information_schema.VIEWS
-- WHERE TABLE_SCHEMA = 'gold' AND TABLE_NAME LIKE 'api\_%' ORDER BY TABLE_NAME;
-- SELECT * FROM gold.api_kpis_annual ORDER BY year;
