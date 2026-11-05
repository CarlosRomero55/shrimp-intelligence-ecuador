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
-- -----------------------------------------------------------------------------
-- SECCIÓN 2: Dimensiones (desnormalizadas, SCD tipo 1)
-- Reglas:
--   * Llave = ID de silver (determinista: las relaciones de Power BI no se rompen al recargar)
--   * Fila 0 = 'Desconocido' en cada dimensión (los hechos nunca quedan con FK nula)
--   * Se llenan desde silver en la Fase 7 (build_gold.py)
-- -----------------------------------------------------------------------------

-- 2.1 Países, con su región copiada dentro (desnormalización intencional)
CREATE TABLE IF NOT EXISTS gold.dim_country (
    country_key       SMALLINT UNSIGNED NOT NULL,      -- = silver.countries.country_id
    iso3              CHAR(3)       NOT NULL,
    m49_code          SMALLINT UNSIGNED NULL,
    country_name_es   VARCHAR(100)  NOT NULL,
    country_name_en   VARCHAR(100)  NOT NULL,
    region_code_m49   CHAR(3)       NOT NULL,          -- copiado de silver.regions
    region_name_es    VARCHAR(60)   NOT NULL,          -- copiado de silver.regions
    region_name_en    VARCHAR(60)   NOT NULL,
    cna_market_group  VARCHAR(30)   NOT NULL,          -- agrupación tipo CNA: China, EE. UU., Europa, Resto de Asia...
    dw_loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (country_key),
    UNIQUE KEY uq_dim_country_iso3 (iso3)
) ENGINE = InnoDB
  COMMENT = 'GOLD: dimensión país con región (desnormalizada)';

-- 2.2 Productos (partidas HS)
CREATE TABLE IF NOT EXISTS gold.dim_product (
    product_key       SMALLINT UNSIGNED NOT NULL,      -- = silver.products.product_id
    hs_code           CHAR(6)       NOT NULL,
    hs_heading        CHAR(4)       NOT NULL,
    product_category  VARCHAR(20)   NOT NULL,          -- frozen | prepared
    category_name_es  VARCHAR(30)   NOT NULL,          -- 'Congelado' | 'Preparado'
    product_name_es   VARCHAR(150)  NOT NULL,
    product_name_en   VARCHAR(150)  NOT NULL,
    dw_loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (product_key),
    UNIQUE KEY uq_dim_product_hs (hs_code)
) ENGINE = InnoDB
  COMMENT = 'GOLD: dimensión producto (partidas HS)';

-- 2.3 Tallas, con pesos YA calculados (en gold se precalcula para leer rápido)
CREATE TABLE IF NOT EXISTS gold.dim_size (
    size_key          SMALLINT UNSIGNED NOT NULL,      -- = silver.sizes.size_id
    size_code         VARCHAR(10)   NOT NULL,
    min_count         SMALLINT UNSIGNED NULL,
    max_count         SMALLINT UNSIGNED NULL,
    min_weight_g      DECIMAL(6,2)  NULL,              -- 1000 / max_count
    max_weight_g      DECIMAL(6,2)  NULL,              -- 1000 / min_count
    size_group        VARCHAR(15)   NOT NULL,          -- Grande | Mediana | Pequeña
    sort_order        TINYINT UNSIGNED NOT NULL,
    dw_loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (size_key),
    UNIQUE KEY uq_dim_size_code (size_code)
) ENGINE = InnoDB
  COMMENT = 'GOLD: dimensión talla con rangos de peso precalculados';

-- 2.4 Piscinas, con los datos de su finca copiados dentro (SIMULADO)
CREATE TABLE IF NOT EXISTS gold.dim_pond (
    pond_key          INT UNSIGNED  NOT NULL,          -- = silver.ponds.pond_id
    pond_code         VARCHAR(20)   NOT NULL,
    farm_code         VARCHAR(20)   NOT NULL,          -- copiado de silver.farms
    farm_name         VARCHAR(100)  NOT NULL,
    province          VARCHAR(50)   NOT NULL,
    area_ha           DECIMAL(8,2)  NULL,
    area_band         VARCHAR(15)   NOT NULL,          -- '< 5 ha' | '5-10 ha' | '> 10 ha'
    is_simulated      BOOLEAN       NOT NULL DEFAULT TRUE,
    dw_loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (pond_key),
    UNIQUE KEY uq_dim_pond_farm_code (farm_code, pond_code)
) ENGINE = InnoDB
  COMMENT = 'GOLD: dimensión piscina con datos de finca (SIMULADO)';

-- 2.5 Fuentes de datos
CREATE TABLE IF NOT EXISTS gold.dim_source (
    source_key        TINYINT UNSIGNED NOT NULL,       -- = silver.data_sources.source_id
    source_code       VARCHAR(20)   NOT NULL,
    source_name       VARCHAR(150)  NOT NULL,
    publisher         VARCHAR(150)  NOT NULL,
    weight_unit       VARCHAR(5)    NULL,
    valuation_basis   VARCHAR(10)   NULL,
    is_simulated      BOOLEAN       NOT NULL,
    is_reference      BOOLEAN       NOT NULL,          -- TRUE solo para UN_COMTRADE
    dw_loaded_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (source_key),
    UNIQUE KEY uq_dim_source_code (source_code)
) ENGINE = InnoDB
  COMMENT = 'GOLD: dimensión fuente de datos';

-- -----------------------------------------------------------------------------
-- 2.6 Miembro 'Desconocido' (llave 0) en cada dimensión — idempotente
-- -----------------------------------------------------------------------------
INSERT INTO gold.dim_country
    (country_key, iso3, m49_code, country_name_es, country_name_en,
     region_code_m49, region_name_es, region_name_en, cna_market_group)
VALUES (0, 'UNK', NULL, 'Desconocido', 'Unknown', '000', 'Desconocida', 'Unknown', 'Otros') AS r
ON DUPLICATE KEY UPDATE country_name_es = r.country_name_es;

INSERT INTO gold.dim_product
    (product_key, hs_code, hs_heading, product_category, category_name_es, product_name_es, product_name_en)
VALUES (0, 'N/A', 'N/A', 'unknown', 'Desconocido', 'Desconocido', 'Unknown') AS r
ON DUPLICATE KEY UPDATE product_name_es = r.product_name_es;

INSERT INTO gold.dim_size
    (size_key, size_code, min_count, max_count, min_weight_g, max_weight_g, size_group, sort_order)
VALUES (0, 'N/A', NULL, NULL, NULL, NULL, 'Desconocida', 99) AS r
ON DUPLICATE KEY UPDATE size_group = r.size_group;

INSERT INTO gold.dim_pond
    (pond_key, pond_code, farm_code, farm_name, province, area_ha, area_band, is_simulated)
VALUES (0, 'N/A', 'N/A', 'Desconocida', 'Desconocida', NULL, 'Desconocida', TRUE) AS r
ON DUPLICATE KEY UPDATE farm_name = r.farm_name;

INSERT INTO gold.dim_source
    (source_key, source_code, source_name, publisher, weight_unit, valuation_basis, is_simulated, is_reference)
VALUES (0, 'UNKNOWN', 'Desconocida', 'Desconocido', NULL, NULL, FALSE, FALSE) AS r
ON DUPLICATE KEY UPDATE source_name = r.source_name;
-- -----------------------------------------------------------------------------
-- SECCIÓN 3: Tablas de hechos
-- Reglas:
--   * La PRIMARY KEY es el GRANO: la base impide filas duplicadas a ese nivel.
--   * Solo hechos ADITIVOS. Los ratios (USD/lb, FCR, supervivencia, lb/ha,
--     costo/lb, margen %) se calculan como medidas, nunca se guardan.
--   * Nunca se mezclan granos distintos en una misma tabla.
-- -----------------------------------------------------------------------------

-- 3.1 Exportaciones. GRANO: un país x una partida HS x un mes (fuente: UN Comtrade)
CREATE TABLE IF NOT EXISTS gold.fact_exports (
    date_key          INT UNSIGNED      NOT NULL,      -- siempre el día 1 del mes (AAAAMM01)
    country_key       SMALLINT UNSIGNED NOT NULL,
    product_key       SMALLINT UNSIGNED NOT NULL,

    -- Hechos aditivos
    value_usd         DECIMAL(18,2)     NOT NULL,
    weight_kg         DECIMAL(18,3)     NULL,
    weight_lb         DECIMAL(18,3)     NULL,          -- en gold se guarda ya convertido

    -- Trazabilidad
    source_batch_id   VARCHAR(40)       NOT NULL,
    dw_loaded_at      DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- La PK ES el grano
    PRIMARY KEY (date_key, country_key, product_key),
    KEY ix_fexp_country (country_key),
    KEY ix_fexp_product (product_key),

    CONSTRAINT chk_fexp_month_start CHECK (date_key MOD 100 = 1),   -- termina en 01
    CONSTRAINT chk_fexp_value       CHECK (value_usd >= 0),

    CONSTRAINT fk_fexp_date    FOREIGN KEY (date_key)    REFERENCES gold.dim_date (date_key),
    CONSTRAINT fk_fexp_country FOREIGN KEY (country_key) REFERENCES gold.dim_country (country_key),
    CONSTRAINT fk_fexp_product FOREIGN KEY (product_key) REFERENCES gold.dim_product (product_key)
) ENGINE = InnoDB
  COMMENT = 'GOLD: hechos de exportación. Grano = país x partida x mes. Fuente: UN Comtrade';

-- 3.2 Producción. GRANO: un ciclo de cultivo en una piscina (DATOS SIMULADOS)
CREATE TABLE IF NOT EXISTS gold.fact_production (
    cycle_key               INT UNSIGNED      NOT NULL,  -- = silver.production_cycles.cycle_id
    cycle_code              VARCHAR(30)       NOT NULL,  -- dimensión degenerada

    -- Llaves a dimensiones (dim_date cumple DOS roles)
    pond_key                INT UNSIGNED      NOT NULL,
    stocking_date_key       INT UNSIGNED      NOT NULL,  -- rol 1: fecha de siembra
    harvest_date_key        INT UNSIGNED      NOT NULL,  -- rol 2: fecha de cosecha final
    size_key                SMALLINT UNSIGNED NOT NULL,  -- talla de la cosecha final

    -- Hechos aditivos (los ratios se calculan como medidas)
    days_of_culture         SMALLINT UNSIGNED NOT NULL,
    num_harvests            TINYINT UNSIGNED  NOT NULL,  -- raleos + cosecha final
    area_ha                 DECIMAL(8,2)      NOT NULL,  -- para lb/ha y densidad
    larvae_stocked          INT UNSIGNED      NOT NULL,
    harvested_animals       INT UNSIGNED      NOT NULL,  -- supervivencia = animales / larvas
    harvested_kg            DECIMAL(12,2)     NOT NULL,
    harvested_lb            DECIMAL(12,2)     NOT NULL,
    feed_kg                 DECIMAL(12,2)     NOT NULL,  -- FCR = alimento / kg cosechados

    -- Costos pivoteados de largo (silver) a ancho (gold)
    cost_larvae_usd         DECIMAL(14,2)     NOT NULL DEFAULT 0,
    cost_feed_usd           DECIMAL(14,2)     NOT NULL DEFAULT 0,
    cost_energy_usd         DECIMAL(14,2)     NOT NULL DEFAULT 0,
    cost_labor_usd          DECIMAL(14,2)     NOT NULL DEFAULT 0,
    total_cost_usd          DECIMAL(14,2)     NOT NULL,
    revenue_usd             DECIMAL(14,2)     NOT NULL,

    dw_loaded_at            DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- La PK ES el grano: un ciclo = una fila
    PRIMARY KEY (cycle_key),
    UNIQUE KEY uq_fprod_cycle_code (cycle_code),
    KEY ix_fprod_pond (pond_key),
    KEY ix_fprod_harvest_date (harvest_date_key),

    CONSTRAINT chk_fprod_harvest_after CHECK (harvest_date_key > stocking_date_key),
    CONSTRAINT chk_fprod_amounts CHECK (harvested_kg >= 0 AND feed_kg >= 0 AND revenue_usd >= 0),
    -- Coherencia: el total debe ser la suma de sus partes
    CONSTRAINT chk_fprod_total_cost CHECK (
        total_cost_usd = cost_larvae_usd + cost_feed_usd + cost_energy_usd + cost_labor_usd
    ),

    CONSTRAINT fk_fprod_pond     FOREIGN KEY (pond_key)          REFERENCES gold.dim_pond (pond_key),
    CONSTRAINT fk_fprod_stocking FOREIGN KEY (stocking_date_key) REFERENCES gold.dim_date (date_key),
    CONSTRAINT fk_fprod_harvest  FOREIGN KEY (harvest_date_key)  REFERENCES gold.dim_date (date_key),
    CONSTRAINT fk_fprod_size     FOREIGN KEY (size_key)          REFERENCES gold.dim_size (size_key)
) ENGINE = InnoDB
  COMMENT = 'GOLD: hechos de producción. Grano = ciclo de cultivo por piscina (SIMULADO)';