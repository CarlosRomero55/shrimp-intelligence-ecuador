-- =============================================================================
-- q08 · Ranking de piscinas por conversión alimenticia (FCR) (datos SIMULADOS)
-- Pregunta: ¿qué piscinas convierten mejor el alimento en camarón?
-- Fuente: gold.fact_production (finca SIMULADA), todos los ciclos.
-- Lectura:
--   * fcr = kg de alimento ÷ kg cosechados. MENOR es mejor (1,3 = 1,3 kg de
--     alimento por kg de camarón).
--   * fcr_finca = FCR de toda su finca (Σ alimento ÷ Σ kg, no promedio de FCR).
--   * dif_vs_finca_pct < 0 = la piscina es más eficiente que su finca.
--   * puesto = ranking general · puesto_en_finca = ranking dentro de su finca.
-- SQL: DENSE_RANK() (empates comparten puesto, sin saltos) y SUM() OVER (PARTITION BY).
-- =============================================================================
WITH pond AS (
    SELECT p.farm_code                   AS finca,
           p.pond_code                   AS piscina,
           COUNT(*)                      AS ciclos,
           SUM(f.feed_kg)                AS alimento_kg,
           SUM(f.harvested_kg)           AS cosecha_kg,
           SUM(f.harvested_animals)      AS animales,
           SUM(f.larvae_stocked)         AS larvas
    FROM gold.fact_production AS f
    JOIN gold.dim_pond        AS p ON p.pond_key = f.pond_key
    GROUP BY p.farm_code, p.pond_code
),
ratios AS (
    SELECT pond.*,
           alimento_kg / cosecha_kg                                              AS fcr,
           SUM(alimento_kg) OVER (PARTITION BY finca)
               / SUM(cosecha_kg) OVER (PARTITION BY finca)                       AS fcr_finca
    FROM pond
)
SELECT DENSE_RANK() OVER (ORDER BY ROUND(fcr, 3))                    AS puesto,
       finca,
       piscina,
       DENSE_RANK() OVER (PARTITION BY finca ORDER BY ROUND(fcr, 3)) AS puesto_en_finca,
       ciclos,
       ROUND(fcr, 3)                                                 AS fcr,
       ROUND(fcr_finca, 3)                                           AS fcr_finca,
       ROUND(100 * (fcr / fcr_finca - 1), 1)                         AS dif_vs_finca_pct,
       ROUND(100 * animales / larvas, 1)                             AS supervivencia_pct
FROM ratios
ORDER BY puesto, finca, piscina
