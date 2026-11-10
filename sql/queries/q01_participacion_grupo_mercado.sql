-- =============================================================================
-- q01 · Participación por grupo de mercado y año (% del valor exportado)
-- Pregunta: ¿cuánto depende Ecuador de China, Europa y EE. UU.?
-- Fuente: gold.fact_exports, cifras PREFERIDAS (el total de cada año = BCE).
-- Lectura:
--   * Cada columna *_pct es el % del TOTAL del año (incluye "Sin reporte").
--   * cobertura_pct = parte del total con país identificado. Si es baja (2025,
--     por la ausencia de China en el espejo), los % por grupo se subestiman.
--   * 2026 incluye solo los meses publicados por el BCE (columna meses).
-- SQL: agregación condicional (SUM(CASE ...)) para pasar grupos a columnas.
-- =============================================================================
WITH base AS (
    SELECT d.calendar_year                                         AS anio,
           f.date_key,
           CASE WHEN f.country_key = 9999 THEN 'Sin reporte'
                ELSE c.cna_market_group END                        AS grupo,
           f.value_usd
    FROM gold.fact_exports AS f
    JOIN gold.dim_date     AS d ON d.date_key    = f.date_key
    JOIN gold.dim_country  AS c ON c.country_key = f.country_key
    WHERE f.is_preferred = 1
)
SELECT anio,
       COUNT(DISTINCT date_key)                                                     AS meses,
       ROUND(SUM(value_usd) / 1e6, 1)                                               AS total_musd,
       ROUND(100 * SUM(CASE WHEN grupo = 'China'            THEN value_usd END) / SUM(value_usd), 1) AS china_pct,
       ROUND(100 * SUM(CASE WHEN grupo = 'Europa'           THEN value_usd END) / SUM(value_usd), 1) AS europa_pct,
       ROUND(100 * SUM(CASE WHEN grupo = 'EE. UU.'          THEN value_usd END) / SUM(value_usd), 1) AS eeuu_pct,
       ROUND(100 * SUM(CASE WHEN grupo = 'Resto de Asia'    THEN value_usd END) / SUM(value_usd), 1) AS resto_asia_pct,
       ROUND(100 * SUM(CASE WHEN grupo = 'Resto de América' THEN value_usd END) / SUM(value_usd), 1) AS resto_america_pct,
       ROUND(100 * SUM(CASE WHEN grupo IN ('África', 'Oceanía', 'Otros')
                            THEN value_usd END) / SUM(value_usd), 1)                AS otros_pct,
       ROUND(100 * SUM(CASE WHEN grupo = 'Sin reporte'      THEN value_usd END) / SUM(value_usd), 1) AS sin_reporte_pct,
       ROUND(100 * SUM(CASE WHEN grupo <> 'Sin reporte'     THEN value_usd END) / SUM(value_usd), 1) AS cobertura_pct
FROM base
GROUP BY anio
ORDER BY anio
