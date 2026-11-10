-- =============================================================================
-- q02 · Top 10 destinos por año, con cambio de puesto frente al año anterior
-- Pregunta: ¿quiénes son los principales compradores y cómo se mueven?
-- Fuente: gold.fact_exports, cifras PREFERIDAS, solo países identificados
--         (sin "Sin reporte por país" ni "Desconocido").
-- Lectura:
--   * pct_identificado = % del valor CON país identificado (no del total BCE).
--   * cambio > 0 = subió de puesto; NULL = no estaba en el año anterior.
--   * 2022-2023 y 2025 vienen del espejo (CIF, lo que declaran los importadores);
--     en 2025-2026 China no reportó, por eso no aparece aunque sea el 1.º real.
-- SQL: RANK() para el puesto y LAG() para el puesto del año anterior.
-- =============================================================================
WITH pais AS (
    SELECT d.calendar_year         AS anio,
           c.iso3,
           c.country_name_es       AS pais,
           c.cna_market_group      AS grupo,
           SUM(f.value_usd)        AS usd
    FROM gold.fact_exports AS f
    JOIN gold.dim_date     AS d ON d.date_key    = f.date_key
    JOIN gold.dim_country  AS c ON c.country_key = f.country_key
    WHERE f.is_preferred = 1
      AND f.country_key NOT IN (0, 9999)
    GROUP BY d.calendar_year, c.iso3, c.country_name_es, c.cna_market_group
),
ranked AS (
    SELECT p.*,
           RANK() OVER (PARTITION BY anio ORDER BY usd DESC)          AS puesto,
           100 * usd / SUM(usd) OVER (PARTITION BY anio)              AS pct_identificado
    FROM pais AS p
),
with_previous AS (
    -- LAG mira la fila anterior DEL MISMO PAÍS (ordenada por año), antes de filtrar el top 10
    SELECT r.*,
           LAG(anio)   OVER (PARTITION BY iso3 ORDER BY anio)         AS anio_anterior,
           LAG(puesto) OVER (PARTITION BY iso3 ORDER BY anio)         AS puesto_lag
    FROM ranked AS r
)
SELECT anio,
       puesto,
       iso3,
       pais,
       grupo,
       ROUND(usd / 1e6, 1)                                            AS usd_musd,
       ROUND(pct_identificado, 1)                                     AS pct_identificado,
       CASE WHEN anio_anterior = anio - 1 THEN puesto_lag END         AS puesto_anterior,
       -- RANK() devuelve BIGINT UNSIGNED: sin CAST, 3 - 5 daría error en vez de -2
       CASE WHEN anio_anterior = anio - 1
            THEN CAST(puesto_lag AS SIGNED) - CAST(puesto AS SIGNED) END AS cambio
FROM with_previous
WHERE puesto <= 10
ORDER BY anio DESC, puesto
