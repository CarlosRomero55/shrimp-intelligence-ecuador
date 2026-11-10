-- =============================================================================
-- q09 · Relación talla-precio (datos SIMULADOS)
-- Pregunta: ¿cuánto más se paga por el camarón grande? ¿conviene cosechar más grande?
-- Fuente: gold.fact_production + gold.dim_size (talla de la cosecha final).
-- Lectura:
--   * precio_lb = Σ ingreso ÷ Σ libras de la talla (todos los años juntos).
--   * indice_precio controla el AÑO: 100 = precio promedio del año de cada cosecha.
--     Así la talla no "parece" más cara solo porque se cosechó en un año de precios
--     altos. 110 = se paga 10 % sobre el promedio de su año.
--   * margen_pct y dias_promedio muestran el costo de esperar a una talla mayor.
-- SQL: un promedio por año en una CTE y un índice ponderado Σ ingreso ÷ Σ (lb × precio del año).
-- =============================================================================
WITH year_price AS (
    SELECT d.calendar_year                              AS anio,
           SUM(f.revenue_usd) / SUM(f.harvested_lb)     AS precio_anio
    FROM gold.fact_production AS f
    JOIN gold.dim_date AS d ON d.date_key = f.harvest_date_key
    GROUP BY d.calendar_year
)
SELECT z.sort_order                                                       AS orden,
       z.size_code                                                        AS talla,
       z.size_group                                                       AS grupo,
       CONCAT(z.min_weight_g, '-', z.max_weight_g, ' g')                  AS peso_pieza,
       COUNT(*)                                                           AS ciclos,
       ROUND(SUM(f.harvested_lb) / 1e6, 2)                                AS millones_lb,
       ROUND(SUM(f.revenue_usd) / SUM(f.harvested_lb), 3)                 AS precio_lb,
       ROUND(100 * SUM(f.revenue_usd) / SUM(f.harvested_lb * yp.precio_anio), 1) AS indice_precio,
       ROUND(AVG(f.days_of_culture), 1)                                   AS dias_promedio,
       ROUND(100 * (SUM(f.revenue_usd) - SUM(f.total_cost_usd)) / SUM(f.revenue_usd), 1)
                                                                          AS margen_pct
FROM gold.fact_production AS f
JOIN gold.dim_size AS z  ON z.size_key  = f.size_key
JOIN gold.dim_date AS d  ON d.date_key  = f.harvest_date_key
JOIN year_price    AS yp ON yp.anio     = d.calendar_year
GROUP BY z.sort_order, z.size_code, z.size_group, z.min_weight_g, z.max_weight_g
ORDER BY z.sort_order
