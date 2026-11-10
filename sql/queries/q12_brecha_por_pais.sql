-- =============================================================================
-- q12 · Conciliación por país: lo que declara el importador (espejo, CIF)
--       frente a lo que declara Ecuador (Comtrade X, FOB)
-- Pregunta: ¿con qué países coinciden las fuentes y dónde hay asimetrías?
-- Fuente: gold.v_reconciliation, nivel 'country', SOLO meses comparables
--         (is_comparable = 1: ambas fuentes reportaron y |brecha| ≤ 1.000 %).
--         Países con al menos 12 meses comparables.
-- Lectura:
--   * brecha_ponderada_pct = Σ espejo ÷ Σ Ecuador − 1 (pesa más los meses grandes).
--   * brecha_mediana_pct = el mes "típico" (no la afectan los meses extremos).
--   * Clasificación sobre la brecha ponderada:
--       ±5 %            → Coinciden
--       +5 % a +15 %    → Espejo mayor, coherente con CIF (flete + seguro)
--       > +15 %         → Espejo mucho mayor (asimetría: reexportación, otra partida)
--       < −5 %          → Espejo menor (asimetría: país de tránsito, sin reporte parcial)
--     Las causas son HIPÓTESIS (ver ADR-006), no hechos verificados.
-- SQL: MEDIANA sin función MEDIAN(): ROW_NUMBER() + COUNT() OVER y promedio de
--      la(s) fila(s) del medio (1 si n es impar, 2 si n es par).
-- =============================================================================
WITH comparable AS (
    SELECT iso3, country_name_es AS pais, cna_market_group AS grupo,
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
    SELECT iso3, AVG(gap_pct) AS mediana
    FROM ordered
    WHERE rn IN (FLOOR((n + 1) / 2), FLOOR((n + 2) / 2))
    GROUP BY iso3
),
totals AS (
    SELECT iso3, MAX(pais) AS pais, MAX(grupo) AS grupo, COUNT(*) AS meses,
           SUM(reference_value_usd) AS ecuador_usd, SUM(value_usd) AS espejo_usd
    FROM comparable
    GROUP BY iso3
    HAVING COUNT(*) >= 12
),
gaps AS (
    SELECT t.*, m.mediana,
           100 * (t.espejo_usd / t.ecuador_usd - 1) AS brecha
    FROM totals AS t
    JOIN median AS m ON m.iso3 = t.iso3
)
SELECT iso3,
       pais,
       grupo,
       meses,
       ROUND(ecuador_usd / 1e6, 1)                 AS ecuador_musd,
       ROUND(espejo_usd / 1e6, 1)                  AS espejo_musd,
       ROUND(brecha, 1)                            AS brecha_ponderada_pct,
       ROUND(mediana, 1)                           AS brecha_mediana_pct,
       CASE WHEN ABS(brecha) <= 5 THEN 'Coinciden (±5 %)'
            WHEN brecha > 15       THEN 'Espejo mucho mayor (asimetría)'
            WHEN brecha > 5        THEN 'Espejo mayor, coherente con CIF'
            ELSE 'Espejo menor (asimetría)'
       END                                         AS clasificacion
FROM gaps
ORDER BY ecuador_usd DESC
