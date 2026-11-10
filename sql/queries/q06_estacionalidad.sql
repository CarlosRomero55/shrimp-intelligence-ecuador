-- =============================================================================
-- q06 · Estacionalidad: peso de cada mes en el valor exportado del año
-- Pregunta: ¿hay meses o temporadas (lluviosa / seca de la Costa) más fuertes?
-- Fuente: gold.fact_exports, cifras PREFERIDAS (total mensual = BCE), solo años
--         completos (12 meses): 2018-2025.
-- Lectura:
--   * indice = valor del mes ÷ promedio mensual de su año × 100.
--     100 = mes promedio · 110 = 10 % sobre el promedio.
--   * minimo / maximo muestran qué tan estable es el patrón entre años.
--   * indice_temporada = promedio de los meses de cada temporada.
--   * Es estacionalidad en VALOR (precio × volumen), no solo en volumen.
-- SQL: SUM() OVER (PARTITION BY año) y un promedio de ventana sobre otro
--      promedio: AVG(AVG(...)) OVER (PARTITION BY temporada).
-- =============================================================================
WITH monthly AS (
    SELECT d.calendar_year          AS anio,
           d.calendar_month         AS mes,
           MAX(d.month_short_es)    AS mes_nombre,
           MAX(d.ec_coastal_season) AS temporada,
           SUM(f.value_usd)         AS usd
    FROM gold.fact_exports AS f
    JOIN gold.dim_date AS d ON d.date_key = f.date_key
    WHERE f.is_preferred = 1
    GROUP BY d.calendar_year, d.calendar_month
),
full_years AS (
    SELECT anio FROM monthly GROUP BY anio HAVING COUNT(*) = 12
),
indexed AS (
    SELECT m.*,
           100 * 12 * m.usd / SUM(m.usd) OVER (PARTITION BY m.anio)   AS indice
    FROM monthly AS m
    JOIN full_years AS fy ON fy.anio = m.anio
)
SELECT mes,
       mes_nombre,
       temporada,
       ROUND(AVG(indice), 1)                                          AS indice,
       ROUND(MIN(indice), 1)                                          AS minimo,
       ROUND(MAX(indice), 1)                                          AS maximo,
       COUNT(*)                                                       AS anios,
       ROUND(AVG(AVG(indice)) OVER (PARTITION BY temporada), 1)       AS indice_temporada
FROM indexed
GROUP BY mes, mes_nombre, temporada
ORDER BY mes
