-- =============================================================================
-- q05 · Precio por libra (USD/lb) por grupo de mercado y año
-- Pregunta: ¿qué mercados pagan más por libra? ¿se ensancha o se cierra la brecha?
-- Fuente: gold.fact_exports, SOLO Comtrade X (lo que declara Ecuador, valor FOB).
--   Se excluye el espejo a propósito: es CIF (incluye flete y seguro) y haría
--   parecer que los mercados lejanos "pagan más". Por eso solo hay años con dato
--   de Ecuador: 2018-2021, 2022 (1 mes), 2024 (11 meses) y 2026 (ene-mar).
-- Lectura:
--   * usd_lb = Σ valor / Σ libras del grupo (no un promedio de precios).
--   * prima_eeuu_vs_china_pct > 0 = EE. UU. paga más por libra que China.
--   * El precio depende también de la MEZCLA de producto (entero vs. cola, talla).
-- SQL: agregación condicional con cociente de sumas.
-- =============================================================================
WITH base AS (
    SELECT d.calendar_year AS anio, f.date_key, c.cna_market_group AS grupo,
           f.value_usd, f.weight_lb
    FROM gold.fact_exports AS f
    JOIN gold.dim_date    AS d ON d.date_key    = f.date_key
    JOIN gold.dim_country AS c ON c.country_key = f.country_key
    WHERE f.source_key = 1                 -- UN_COMTRADE (Ecuador, FOB)
      AND f.weight_lb > 0
)
SELECT anio,
       COUNT(DISTINCT date_key)                                                        AS meses,
       ROUND(SUM(value_usd) / SUM(weight_lb), 2)                                       AS total_usd_lb,
       ROUND(SUM(CASE WHEN grupo = 'China'   THEN value_usd END)
           / SUM(CASE WHEN grupo = 'China'   THEN weight_lb END), 2)                   AS china_usd_lb,
       ROUND(SUM(CASE WHEN grupo = 'EE. UU.' THEN value_usd END)
           / SUM(CASE WHEN grupo = 'EE. UU.' THEN weight_lb END), 2)                   AS eeuu_usd_lb,
       ROUND(SUM(CASE WHEN grupo = 'Europa'  THEN value_usd END)
           / SUM(CASE WHEN grupo = 'Europa'  THEN weight_lb END), 2)                   AS europa_usd_lb,
       ROUND(SUM(CASE WHEN grupo = 'Resto de Asia' THEN value_usd END)
           / SUM(CASE WHEN grupo = 'Resto de Asia' THEN weight_lb END), 2)             AS resto_asia_usd_lb,
       ROUND(SUM(CASE WHEN grupo = 'Resto de América' THEN value_usd END)
           / SUM(CASE WHEN grupo = 'Resto de América' THEN weight_lb END), 2)          AS resto_america_usd_lb,
       ROUND(100 * ( (SUM(CASE WHEN grupo = 'EE. UU.' THEN value_usd END)
                      / SUM(CASE WHEN grupo = 'EE. UU.' THEN weight_lb END))
                   / (SUM(CASE WHEN grupo = 'China'   THEN value_usd END)
                      / SUM(CASE WHEN grupo = 'China'   THEN weight_lb END)) - 1), 1) AS prima_eeuu_vs_china_pct
FROM base
GROUP BY anio
ORDER BY anio
