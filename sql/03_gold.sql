-- =============================================================================
-- 03_gold.sql — Capa GOLD: modelo dimensional en esquema estrella (Kimball)
-- Proyecto: Shrimp Intelligence Ecuador
-- Ejecutar con el usuario: shrimp_etl
--
-- Reglas de gold:
--   * Es lo ÚNICO que consumen Power BI y la API.
--   * Dimensiones desnormalizadas (dim_*) + tablas de hechos con grano explícito (fact_*).
--   * Se reconstruye desde silver; nunca se edita a mano.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- SECCIÓN 1: Dimensión de fechas
-- Grano: UNA FILA POR DÍA, de 2018-01-01 a 2030-12-31
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key            INT UNSIGNED  NOT NULL,        -- AAAAMMDD, ej. 20250301 (estándar Kimball)
    full_date           DATE          NOT NULL,
    calendar_year       SMALLINT      NOT NULL,
    calendar_quarter    TINYINT       NOT NULL,        -- 1 a 4
    calendar_month      TINYINT       NOT NULL,        -- 1 a 12
    month_name_es       VARCHAR(12)   NOT NULL,        -- 'marzo'
    month_name_en       VARCHAR(12)   NOT NULL,        -- 'March'
    month_short_es      CHAR(3)       NOT NULL,        -- 'Mar'
    period_label        CHAR(7)       NOT NULL,        -- '2025-03' (year_month es palabra reservada)
    period_key          INT UNSIGNED  NOT NULL,        -- 202503: para ordenar meses en Power BI
    month_start_date    DATE          NOT NULL,        -- primer día del mes (une con hechos mensuales)
    day_of_month        TINYINT       NOT NULL,
    iso_day_of_week     TINYINT       NOT NULL,        -- 1 = lunes ... 7 = domingo
    day_name_es         VARCHAR(10)   NOT NULL,
    iso_week            TINYINT       NOT NULL,        -- semana ISO del año
    is_weekend          BOOLEAN       NOT NULL,
    is_month_start      BOOLEAN       NOT NULL,
    ec_coastal_season   VARCHAR(10)   NOT NULL,        -- 'lluviosa' (dic-may) | 'seca' (jun-nov), aproximado

    PRIMARY KEY (date_key),
    UNIQUE KEY uq_dim_date_full (full_date),
    KEY ix_dim_date_period (period_key)
) ENGINE = InnoDB
  COMMENT = 'GOLD: calendario diario 2018-2030 para inteligencia de tiempo';

-- -----------------------------------------------------------------------------
-- Carga de dim_date con una CTE recursiva (idempotente mediante anti-join)
-- -----------------------------------------------------------------------------

-- MySQL limita la recursión a 1.000 vueltas; necesitamos ~4.748 (solo en esta sesión)
SET SESSION cte_max_recursion_depth = 10000;

INSERT INTO gold.dim_date (
    date_key, full_date, calendar_year, calendar_quarter, calendar_month,
    month_name_es, month_name_en, month_short_es, period_label, period_key,
    month_start_date, day_of_month, iso_day_of_week, day_name_es, iso_week,
    is_weekend, is_month_start, ec_coastal_season
)
WITH RECURSIVE calendar AS (
    -- Ancla: el primer día del calendario
    SELECT DATE('2018-01-01') AS d
    UNION ALL
    -- Paso recursivo: el día anterior + 1, hasta la fecha final
    SELECT d + INTERVAL 1 DAY
    FROM calendar
    WHERE d < '2030-12-31'
)
SELECT
    CAST(DATE_FORMAT(c.d, '%Y%m%d') AS UNSIGNED)                      AS date_key,
    c.d                                                               AS full_date,
    YEAR(c.d)                                                         AS calendar_year,
    QUARTER(c.d)                                                      AS calendar_quarter,
    MONTH(c.d)                                                        AS calendar_month,
    -- ELT elige el texto según el número de mes: no depende del idioma del servidor
    ELT(MONTH(c.d), 'enero','febrero','marzo','abril','mayo','junio','julio',
                    'agosto','septiembre','octubre','noviembre','diciembre')   AS month_name_es,
    ELT(MONTH(c.d), 'January','February','March','April','May','June','July',
                    'August','September','October','November','December')    AS month_name_en,
    ELT(MONTH(c.d), 'Ene','Feb','Mar','Abr','May','Jun','Jul',
                    'Ago','Sep','Oct','Nov','Dic')                     AS month_short_es,
    DATE_FORMAT(c.d, '%Y-%m')                                         AS period_label,
    CAST(DATE_FORMAT(c.d, '%Y%m') AS UNSIGNED)                        AS period_key,
    DATE_FORMAT(c.d, '%Y-%m-01')                                      AS month_start_date,
    DAY(c.d)                                                          AS day_of_month,
    WEEKDAY(c.d) + 1                                                  AS iso_day_of_week,   -- WEEKDAY: 0 = lunes
    ELT(WEEKDAY(c.d) + 1, 'lunes','martes','miércoles','jueves',
                          'viernes','sábado','domingo')                AS day_name_es,
    WEEK(c.d, 3)                                                      AS iso_week,          -- modo 3 = ISO 8601
    WEEKDAY(c.d) >= 5                                                 AS is_weekend,
    DAY(c.d) = 1                                                      AS is_month_start,
    CASE WHEN MONTH(c.d) IN (12, 1, 2, 3, 4, 5) THEN 'lluviosa' ELSE 'seca' END
                                                                      AS ec_coastal_season
FROM calendar c
-- Anti-join: solo inserta las fechas que todavía no existen (re-ejecutar no duplica)
LEFT JOIN gold.dim_date dd ON dd.full_date = c.d
WHERE dd.full_date IS NULL;