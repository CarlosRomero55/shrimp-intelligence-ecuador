-- =============================================================================
-- q04 · Variación interanual del valor y del precio por libra (USD/lb)
-- Pregunta: ¿se está vendiendo más por menos? (¿crece el valor más lento que el volumen?)
-- Fuente: gold.fact_exports, cifras PREFERIDAS.
-- Lectura:
--   * valor_musd y valor_yoy_pct usan el TOTAL (= BCE): completos todos los años.
--     El YoY compara los MISMOS meses (2026 ene-ago vs 2025 ene-ago).
--   * usd_lb sale solo de las filas CON peso (países identificados): cobertura_peso_pct
--     dice qué parte del valor respalda ese precio.
--   * fuente_precio: UN_COMTRADE = FOB (Ecuador) · COMTRADE_MIRROR = CIF (importadores,
--     incluye flete: precio más alto). precio_yoy_comparable = 'No' si cambió la fuente.
-- SQL: LAG() para el año anterior; NULLIF() evita dividir entre cero.
-- =============================================================================
WITH monthly AS (
    SELECT d.calendar_year AS anio, d.calendar_month AS mes, SUM(f.value_usd) AS usd
    FROM gold.fact_exports AS f
    JOIN gold.dim_date AS d ON d.date_key = f.date_key
    WHERE f.is_preferred = 1
    GROUP BY d.calendar_year, d.calendar_month
),
yearly AS (
    SELECT anio, COUNT(*) AS meses, MAX(mes) AS ultimo_mes, SUM(usd) AS usd
    FROM monthly
    GROUP BY anio
),
same_period_previous AS (
    -- valor del año anterior SOLO en los meses que tiene el año actual
    SELECT y.anio, SUM(m.usd) AS usd_anterior
    FROM yearly AS y
    JOIN monthly AS m ON m.anio = y.anio - 1 AND m.mes <= y.ultimo_mes
    GROUP BY y.anio
),
price AS (
    SELECT d.calendar_year                                    AS anio,
           SUM(f.value_usd)                                   AS usd_con_peso,
           SUM(f.weight_lb)                                   AS lb,
           CASE WHEN MIN(s.source_code) = MAX(s.source_code)
                THEN MIN(s.source_code) ELSE 'MIXTA' END      AS fuente_precio
    FROM gold.fact_exports AS f
    JOIN gold.dim_date   AS d ON d.date_key   = f.date_key
    JOIN gold.dim_source AS s ON s.source_key = f.source_key
    WHERE f.is_preferred = 1
      AND f.country_key <> 9999
      AND f.weight_lb > 0
    GROUP BY d.calendar_year
),
combined AS (
    SELECT y.anio, y.meses, y.usd, sp.usd_anterior,
           p.lb, p.usd_con_peso, p.fuente_precio,
           p.usd_con_peso / NULLIF(p.lb, 0)                            AS usd_lb,
           LAG(p.usd_con_peso / NULLIF(p.lb, 0)) OVER (ORDER BY y.anio) AS usd_lb_anterior,
           LAG(p.fuente_precio)                  OVER (ORDER BY y.anio) AS fuente_anterior
    FROM yearly AS y
    LEFT JOIN same_period_previous AS sp ON sp.anio = y.anio
    LEFT JOIN price                AS p  ON p.anio  = y.anio
)
SELECT anio,
       meses,
       ROUND(usd / 1e6, 1)                                         AS valor_musd,
       ROUND(100 * (usd / NULLIF(usd_anterior, 0) - 1), 1)         AS valor_yoy_pct,
       ROUND(lb / 1e6, 1)                                          AS lb_identificadas_m,
       ROUND(usd_lb, 2)                                            AS usd_lb,
       ROUND(100 * (usd_lb / NULLIF(usd_lb_anterior, 0) - 1), 1)   AS precio_yoy_pct,
       fuente_precio,
       CASE WHEN fuente_anterior IS NULL          THEN NULL
            WHEN fuente_precio = fuente_anterior  THEN 'Sí'
            ELSE 'No (cambia la fuente)' END                       AS precio_yoy_comparable,
       ROUND(100 * usd_con_peso / usd, 1)                          AS cobertura_peso_pct
FROM combined
ORDER BY anio
