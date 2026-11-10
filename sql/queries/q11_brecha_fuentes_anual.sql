-- =============================================================================
-- q11 · Conciliación por año: Ecuador (Comtrade X) y espejo frente al BCE
-- Pregunta: ¿cuánto se aleja cada fuente de la cifra oficial y en qué años?
-- Fuente: gold.v_reconciliation, nivel 'total' (una fila por mes y fuente comparada).
-- Lectura:
--   * meses_con_dato / meses_bce = cobertura de la fuente en ese año.
--   * brecha_pct compara SOLO los meses en que la fuente publicó
--     (fuente ÷ BCE de esos mismos meses − 1). Negativa = la fuente queda por debajo.
--   * Ecuador (X) suele quedar 1-8 % por debajo del BCE (cobertura de partidas y
--     fechas); el espejo cae fuerte en 2025-2026 porque China no reportó (ADR-006).
--   * peor_mes_pct / mejor_mes_pct muestran la dispersión mensual.
-- SQL: agregación condicional sobre una vista; CAST para contar condiciones.
-- =============================================================================
SELECT YEAR(period_date)                                                    AS anio,
       source_code                                                          AS fuente,
       COUNT(*)                                                             AS meses_bce,
       CAST(SUM(value_usd IS NOT NULL) AS SIGNED)                           AS meses_con_dato,
       ROUND(SUM(value_usd) / 1e6, 1)                                       AS fuente_musd,
       ROUND(SUM(CASE WHEN value_usd IS NOT NULL
                      THEN reference_value_usd END) / 1e6, 1)               AS bce_mismos_meses_musd,
       ROUND(100 * (SUM(value_usd)
                    / NULLIF(SUM(CASE WHEN value_usd IS NOT NULL
                                      THEN reference_value_usd END), 0) - 1), 1) AS brecha_pct,
       ROUND(MIN(gap_pct), 1)                                               AS peor_mes_pct,
       ROUND(MAX(gap_pct), 1)                                               AS mejor_mes_pct
FROM gold.v_reconciliation
WHERE comparison_level = 'total'
GROUP BY YEAR(period_date), source_code
ORDER BY anio, fuente
