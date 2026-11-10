-- =============================================================================
-- q07 · Margen por piscina y cuartiles de rentabilidad (datos SIMULADOS)
-- Pregunta: ¿qué piscinas son más rentables y cuánto se separan de las demás?
-- Fuente: gold.fact_production (finca SIMULADA "Acuícola Del Golfo"), todos los ciclos.
-- Lectura:
--   * margen_pct = (ingreso − costo) ÷ ingreso, sumando TODOS los ciclos de la piscina.
--   * cuartil 1 = 25 % más rentable · cuartil 4 = 25 % menos rentable.
--   * costo_lb y precio_lb explican el margen: ¿produce barato o vende caro?
--   * En la simulación el costo por libra es casi constante: las diferencias son
--     pequeñas. El valor de la consulta está en el método (sirve con datos reales).
-- SQL: NTILE(4) OVER (ORDER BY ...) reparte las piscinas en 4 grupos iguales.
-- =============================================================================
WITH pond AS (
    SELECT p.farm_code                 AS finca,
           p.pond_code                 AS piscina,
           p.area_band                 AS tamano,
           COUNT(*)                    AS ciclos,
           SUM(f.revenue_usd)          AS ingreso,
           SUM(f.total_cost_usd)       AS costo,
           SUM(f.harvested_lb)         AS lb
    FROM gold.fact_production AS f
    JOIN gold.dim_pond        AS p ON p.pond_key = f.pond_key
    GROUP BY p.farm_code, p.pond_code, p.area_band
)
SELECT finca,
       piscina,
       tamano,
       ciclos,
       ROUND(ingreso / 1e3, 1)                                    AS ingreso_miles_usd,
       ROUND((ingreso - costo) / 1e3, 1)                          AS utilidad_miles_usd,
       ROUND(100 * (ingreso - costo) / ingreso, 1)                AS margen_pct,
       ROUND(costo / lb, 3)                                       AS costo_lb,
       ROUND(ingreso / lb, 3)                                     AS precio_lb,
       NTILE(4) OVER (ORDER BY (ingreso - costo) / ingreso DESC)  AS cuartil
FROM pond
ORDER BY margen_pct DESC
