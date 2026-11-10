-- =============================================================================
-- q10 · Productividad por finca y año de cosecha (datos SIMULADOS)
-- Pregunta: ¿qué finca produce más por hectárea y con qué eficiencia?
-- Fuente: gold.fact_production + gold.dim_pond + gold.dim_date (año de cosecha).
-- Lectura (todos los ratios como Σ ÷ Σ, no promedios de ratios):
--   * lb_ha_ciclo = libras cosechadas ÷ hectáreas sembradas (por ciclo).
--   * supervivencia_pct = animales cosechados ÷ larvas sembradas.
--   * peso_g = peso promedio por animal cosechado.
--   * costo_lb y margen_pct cierran la lectura económica.
--   * 2022 y 2026 son años parciales de la simulación (menos ciclos).
-- SQL: varios ratios ponderados en una sola agregación.
-- =============================================================================
SELECT p.farm_code                                                         AS finca,
       MAX(p.province)                                                     AS provincia,
       d.calendar_year                                                     AS anio,
       COUNT(*)                                                            AS ciclos,
       ROUND(AVG(f.days_of_culture), 1)                                    AS dias_promedio,
       ROUND(SUM(f.harvested_lb) / SUM(f.area_ha), 0)                      AS lb_ha_ciclo,
       ROUND(100 * SUM(f.harvested_animals) / SUM(f.larvae_stocked), 1)    AS supervivencia_pct,
       ROUND(1000 * SUM(f.harvested_kg) / SUM(f.harvested_animals), 2)     AS peso_g,
       ROUND(SUM(f.feed_kg) / SUM(f.harvested_kg), 3)                      AS fcr,
       ROUND(SUM(f.total_cost_usd) / SUM(f.harvested_lb), 3)               AS costo_lb,
       ROUND(100 * (SUM(f.revenue_usd) - SUM(f.total_cost_usd)) / SUM(f.revenue_usd), 1)
                                                                           AS margen_pct
FROM gold.fact_production AS f
JOIN gold.dim_pond AS p ON p.pond_key = f.pond_key
JOIN gold.dim_date AS d ON d.date_key = f.harvest_date_key
GROUP BY p.farm_code, d.calendar_year
ORDER BY p.farm_code, d.calendar_year
