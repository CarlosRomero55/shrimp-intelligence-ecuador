-- =============================================================================
-- q03 · Concentración de mercados: índice Herfindahl-Hirschman (HHI) por año
-- Pregunta: ¿qué tan arriesgada es la dependencia de pocos compradores?
-- Fórmula: HHI = Σ (participación % de cada país)²   → de 0 a 10.000
--   < 1.500 poco concentrado · 1.500-2.500 moderado · > 2.500 muy concentrado
--   (umbrales usados por autoridades de competencia, aplicados aquí a destinos)
-- Fuente: gold.fact_exports, cifras PREFERIDAS, solo países identificados.
-- Lectura:
--   * La participación se calcula sobre el valor CON país identificado.
--   * Si la cobertura es < 80 % (2025-2026: falta China) el HHI sale
--     artificialmente bajo → marcado "No comparable".
-- SQL: CTE en cadena + SUM() OVER (PARTITION BY ...) para la participación.
-- =============================================================================
WITH pais AS (
    SELECT d.calendar_year AS anio, c.country_name_es AS pais, SUM(f.value_usd) AS usd
    FROM gold.fact_exports AS f
    JOIN gold.dim_date     AS d ON d.date_key    = f.date_key
    JOIN gold.dim_country  AS c ON c.country_key = f.country_key
    WHERE f.is_preferred = 1
      AND f.country_key NOT IN (0, 9999)
    GROUP BY d.calendar_year, c.country_name_es
),
shares AS (
    SELECT anio, pais,
           100 * usd / SUM(usd) OVER (PARTITION BY anio)                   AS share_pct,
           ROW_NUMBER() OVER (PARTITION BY anio ORDER BY usd DESC)         AS orden
    FROM pais
),
hhi AS (
    SELECT anio,
           SUM(share_pct * share_pct)                                      AS hhi,
           COUNT(*)                                                        AS paises,
           MAX(CASE WHEN orden = 1 THEN pais END)                          AS principal,
           MAX(CASE WHEN orden = 1 THEN share_pct END)                     AS principal_pct,
           SUM(CASE WHEN orden <= 3 THEN share_pct END)                    AS top3_pct
    FROM shares
    GROUP BY anio
),
coverage AS (
    SELECT d.calendar_year AS anio,
           100 * SUM(CASE WHEN f.country_key <> 9999 THEN f.value_usd ELSE 0 END)
               / SUM(f.value_usd)                                          AS cobertura_pct
    FROM gold.fact_exports AS f
    JOIN gold.dim_date AS d ON d.date_key = f.date_key
    WHERE f.is_preferred = 1
    GROUP BY d.calendar_year
)
SELECT h.anio,
       ROUND(h.hhi, 0)                                                     AS hhi,
       CASE WHEN h.hhi < 1500 THEN 'Poco concentrado'
            WHEN h.hhi < 2500 THEN 'Moderado'
            ELSE 'Muy concentrado' END                                     AS nivel,
       h.principal,
       ROUND(h.principal_pct, 1)                                           AS principal_pct,
       ROUND(h.top3_pct, 1)                                                AS top3_pct,
       h.paises,
       ROUND(cv.cobertura_pct, 1)                                          AS cobertura_pct,
       CASE WHEN cv.cobertura_pct < 80 THEN 'No comparable (falta detalle por país)'
            ELSE 'Comparable' END                                          AS comparabilidad
FROM hhi AS h
JOIN coverage AS cv ON cv.anio = h.anio
ORDER BY h.anio
