-- =============================================================================
-- 02_silver.sql — Capa SILVER: datos limpios, validados y normalizados (3FN)
-- Proyecto: Shrimp Intelligence Ecuador
-- Ejecutar con el usuario: shrimp_etl
--
-- Reglas de silver:
--   * Única versión de la verdad: tipos correctos, PK, FK y CHECK.
--   * Cada dato vive en UN solo lugar (3FN).
--   * Cargas idempotentes con INSERT ... ON DUPLICATE KEY UPDATE (nunca INSERT IGNORE).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- SECCIÓN 1: Geografía
-- -----------------------------------------------------------------------------

-- 1.1 Regiones continentales según el estándar M49 de la ONU (el mismo de Comtrade)
CREATE TABLE IF NOT EXISTS silver.regions (
    region_id         SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    m49_region_code   CHAR(3)       NOT NULL,          -- ej. '150' = Europa
    region_name_es    VARCHAR(60)   NOT NULL,
    region_name_en    VARCHAR(60)   NOT NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (region_id),
    UNIQUE KEY uq_regions_m49 (m49_region_code),
    CONSTRAINT chk_regions_m49 CHECK (REGEXP_LIKE(m49_region_code, '^[0-9]{3}$'))
) ENGINE = InnoDB
  COMMENT = 'SILVER: regiones continentales M49 (ONU)';

-- 1.2 Países: una fila por país, identificado por ISO3
CREATE TABLE IF NOT EXISTS silver.countries (
    country_id        SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    iso3              CHAR(3)       NOT NULL,          -- ej. 'CHN'
    m49_code          SMALLINT UNSIGNED NULL,          -- código de país en Comtrade (ej. 156)
    country_name_es   VARCHAR(100)  NOT NULL,
    country_name_en   VARCHAR(100)  NOT NULL,
    region_id         SMALLINT UNSIGNED NOT NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (country_id),
    UNIQUE KEY uq_countries_iso3 (iso3),
    UNIQUE KEY uq_countries_m49 (m49_code),
    KEY ix_countries_region (region_id),

    -- 'c' = comparación sensible a mayúsculas (la collation _ci la ignoraría)
    CONSTRAINT chk_countries_iso3 CHECK (REGEXP_LIKE(iso3, '^[A-Z]{3}$', 'c')),

    -- No se puede borrar una región que todavía tenga países
    CONSTRAINT fk_countries_region FOREIGN KEY (region_id)
        REFERENCES silver.regions (region_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB
  COMMENT = 'SILVER: catálogo de países (ISO3 + M49)';

-- 1.3 Alias: todas las formas en que las fuentes escriben un país
--     Ej.: 'EEUU', 'Estados Unidos', 'USA', 'U.S.A.' -> country_id de USA
CREATE TABLE IF NOT EXISTS silver.country_aliases (
    alias_id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    alias_text        VARCHAR(100)  NOT NULL,          -- texto tal como aparece en la fuente
    country_id        SMALLINT UNSIGNED NOT NULL,
    seen_in_source    VARCHAR(50)   NULL,              -- dónde se vio: cna, bce, farm...
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (alias_id),
    -- Con collation _ai_ci, 'EEUU' y 'eeuu' cuentan como el mismo alias
    UNIQUE KEY uq_aliases_text (alias_text),
    KEY ix_aliases_country (country_id),

    CONSTRAINT fk_aliases_country FOREIGN KEY (country_id)
        REFERENCES silver.countries (country_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB
  COMMENT = 'SILVER: mapeo de nombres de país sucios a su país normalizado';

-- 1.4 Carga inicial de regiones (idempotente: re-ejecutar no duplica)
INSERT INTO silver.regions (m49_region_code, region_name_es, region_name_en)
VALUES
    ('002', 'África',   'Africa'),
    ('019', 'Américas', 'Americas'),
    ('142', 'Asia',     'Asia'),
    ('150', 'Europa',   'Europe'),
    ('009', 'Oceanía',  'Oceania') AS new_row
ON DUPLICATE KEY UPDATE
    region_name_es = new_row.region_name_es,
    region_name_en = new_row.region_name_en;
    
    -- -----------------------------------------------------------------------------
-- SECCIÓN 2: Productos, tallas y fuentes de datos
-- -----------------------------------------------------------------------------

-- 2.1 Productos: partidas HS del proyecto
CREATE TABLE IF NOT EXISTS silver.products (
    product_id        SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    hs_code           CHAR(6)       NOT NULL,          -- texto: conserva el cero inicial
    -- Columna generada: MySQL la calcula sola, nunca queda desalineada
    hs_heading        CHAR(4) GENERATED ALWAYS AS (LEFT(hs_code, 4)) STORED,
    product_category  VARCHAR(20)   NOT NULL,          -- frozen | prepared
    product_name_es   VARCHAR(150)  NOT NULL,          -- descripción abreviada
    product_name_en   VARCHAR(150)  NOT NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (product_id),
    UNIQUE KEY uq_products_hs (hs_code),
    CONSTRAINT chk_products_hs       CHECK (REGEXP_LIKE(hs_code, '^[0-9]{6}$')),
    CONSTRAINT chk_products_category CHECK (product_category IN ('frozen', 'prepared'))
) ENGINE = InnoDB
  COMMENT = 'SILVER: partidas HS de camarón incluidas en el análisis';

-- 2.2 Tallas comerciales: piezas por kilogramo (ej. 40/50 = entre 40 y 50 camarones por kg)
CREATE TABLE IF NOT EXISTS silver.sizes (
    size_id           SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    size_code         VARCHAR(10)   NOT NULL,          -- formato normalizado: '40/50'
    min_count         SMALLINT UNSIGNED NOT NULL,      -- piezas por kg (talla más grande del rango)
    max_count         SMALLINT UNSIGNED NOT NULL,
    count_basis       VARCHAR(20)   NOT NULL DEFAULT 'pieces_per_kg',
    sort_order        TINYINT UNSIGNED NOT NULL,       -- 1 = camarón más grande
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (size_id),
    UNIQUE KEY uq_sizes_code (size_code),
    CONSTRAINT chk_sizes_range  CHECK (min_count > 0 AND min_count < max_count),
    CONSTRAINT chk_sizes_format CHECK (REGEXP_LIKE(size_code, '^[0-9]{2,3}/[0-9]{2,3}$'))
) ENGINE = InnoDB
  COMMENT = 'SILVER: tallas comerciales de camarón entero';

-- 2.3 Fuentes de datos: ficha técnica de cada fuente (clave para la conciliación)
CREATE TABLE IF NOT EXISTS silver.data_sources (
    source_id         TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_code       VARCHAR(20)   NOT NULL,          -- UN_COMTRADE, CNA, BCE, FARM_SIM
    source_name       VARCHAR(150)  NOT NULL,
    publisher         VARCHAR(150)  NOT NULL,
    source_url        VARCHAR(255)  NULL,              -- NULL = por confirmar en la Fase 4
    weight_unit       VARCHAR(5)    NULL,              -- kg | lb | t
    valuation_basis   VARCHAR(10)   NULL,              -- FOB | CIF
    is_simulated      BOOLEAN       NOT NULL DEFAULT FALSE,
    notes             VARCHAR(500)  NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (source_id),
    UNIQUE KEY uq_sources_code (source_code),
    CONSTRAINT chk_sources_unit CHECK (weight_unit IS NULL OR weight_unit IN ('kg', 'lb', 't')),
    CONSTRAINT chk_sources_val  CHECK (valuation_basis IS NULL OR valuation_basis IN ('FOB', 'CIF'))
) ENGINE = InnoDB
  COMMENT = 'SILVER: metadatos de cada fuente (unidad, valoración, si es simulada)';

-- 2.4 Carga inicial de productos (idempotente)
INSERT INTO silver.products (hs_code, product_category, product_name_es, product_name_en)
VALUES
    ('030617', 'frozen',   'Camarones y langostinos congelados',          'Frozen shrimps and prawns'),
    ('160521', 'prepared', 'Camarones preparados, sin envase hermético',   'Prepared shrimps, not in airtight containers'),
    ('160529', 'prepared', 'Camarones preparados, los demás',              'Prepared shrimps, other') AS new_row
ON DUPLICATE KEY UPDATE
    product_category = new_row.product_category,
    product_name_es  = new_row.product_name_es,
    product_name_en  = new_row.product_name_en;

-- 2.5 Carga inicial de tallas: rangos estándar de camarón entero por kg (idempotente)
INSERT INTO silver.sizes (size_code, min_count, max_count, sort_order)
VALUES
    ('20/30',   20,  30, 1),
    ('30/40',   30,  40, 2),
    ('40/50',   40,  50, 3),
    ('50/60',   50,  60, 4),
    ('60/70',   60,  70, 5),
    ('70/80',   70,  80, 6),
    ('80/100',  80, 100, 7),
    ('100/120', 100, 120, 8) AS new_row
ON DUPLICATE KEY UPDATE
    min_count  = new_row.min_count,
    max_count  = new_row.max_count,
    sort_order = new_row.sort_order;

-- 2.6 Carga inicial de fuentes (idempotente)
INSERT INTO silver.data_sources
    (source_code, source_name, publisher, source_url, weight_unit, valuation_basis, is_simulated, notes)
VALUES
    ('UN_COMTRADE', 'UN Comtrade Database', 'Naciones Unidas (UNSD)',
     'https://comtradeplus.un.org', 'kg', 'FOB', FALSE,
     'Exportaciones reportadas por Ecuador (reporter 218), detalle país x HS6 x mes'),
    ('CNA', 'Estadísticas de exportación', 'Cámara Nacional de Acuacultura',
     NULL, 'lb', 'FOB', FALSE,
     'URL y formato por confirmar en Fase 4; reporta por mercado en libras'),
    ('BCE', 'Estadísticas de comercio exterior', 'Banco Central del Ecuador',
     'https://www.bce.fin.ec', NULL, 'FOB', FALSE,
     'Unidad de peso por confirmar en Fase 4 al revisar el archivo'),
    ('FARM_SIM', 'Operación de Acuícola Del Golfo S.A.', 'Generado con Python (numpy, semilla 42)',
     NULL, 'lb', NULL, TRUE,
     'DATOS SIMULADOS para demostrar análisis de producción') AS new_row
ON DUPLICATE KEY UPDATE
    source_name     = new_row.source_name,
    publisher       = new_row.publisher,
    source_url      = new_row.source_url,
    weight_unit     = new_row.weight_unit,
    valuation_basis = new_row.valuation_basis,
    is_simulated    = new_row.is_simulated,
    notes           = new_row.notes;
    -- -----------------------------------------------------------------------------
-- SECCIÓN 3: Exportaciones y conciliación de fuentes
-- -----------------------------------------------------------------------------

-- 3.1 Exportaciones mensuales limpias
--     Grano: una fila por fuente x país x partida x mes
CREATE TABLE IF NOT EXISTS silver.export_monthly (
    export_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_id         TINYINT UNSIGNED  NOT NULL,
    country_id        SMALLINT UNSIGNED NOT NULL,
    product_id        SMALLINT UNSIGNED NOT NULL,
    period_date       DATE              NOT NULL,      -- siempre el día 1 del mes
    value_usd         DECIMAL(18,2)     NOT NULL,      -- DECIMAL: exacto, nunca FLOAT
    weight_kg         DECIMAL(18,3)     NULL,          -- unidad canónica de peso
    -- Libras calculadas por MySQL: nunca se desalinean del kg
    weight_lb         DECIMAL(18,3) GENERATED ALWAYS AS (ROUND(weight_kg * 2.20462, 3)) STORED,
    source_batch_id   VARCHAR(40)       NOT NULL,      -- trazabilidad hacia bronze
    loaded_at         DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (export_id),
    -- La llave natural: garantiza que re-ejecutar no duplique
    UNIQUE KEY uq_export_grain (source_id, country_id, product_id, period_date),
    KEY ix_export_period (period_date),
    KEY ix_export_country_period (country_id, period_date),

    CONSTRAINT chk_export_first_day CHECK (DAY(period_date) = 1),
    CONSTRAINT chk_export_value     CHECK (value_usd >= 0),
    CONSTRAINT chk_export_weight    CHECK (weight_kg IS NULL OR weight_kg >= 0),

    CONSTRAINT fk_export_source  FOREIGN KEY (source_id)  REFERENCES silver.data_sources (source_id),
    CONSTRAINT fk_export_country FOREIGN KEY (country_id) REFERENCES silver.countries (country_id),
    CONSTRAINT fk_export_product FOREIGN KEY (product_id) REFERENCES silver.products (product_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: exportaciones mensuales limpias por fuente, país y partida';

-- 3.2 Conciliación de fuentes (formato largo: una fila por fuente comparada)
--     Grano: mes x nivel de comparación x país (NULL = total Ecuador) x fuente
CREATE TABLE IF NOT EXISTS silver.source_reconciliation (
    recon_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    period_date           DATE              NOT NULL,
    comparison_level      VARCHAR(10)       NOT NULL,  -- 'total' | 'country'
    country_id            SMALLINT UNSIGNED NULL,      -- NULL = total de Ecuador
    -- NULL -> 0 para que la llave única funcione (MySQL permite varios NULL en UNIQUE)
    country_key           SMALLINT UNSIGNED GENERATED ALWAYS AS (COALESCE(country_id, 0)) STORED,
    source_id             TINYINT UNSIGNED  NOT NULL,  -- fuente comparada (CNA, BCE)
    reference_source_id   TINYINT UNSIGNED  NOT NULL,  -- fuente de referencia (UN_COMTRADE)

    value_usd             DECIMAL(18,2)     NULL,
    reference_value_usd   DECIMAL(18,2)     NULL,
    weight_kg             DECIMAL(18,3)     NULL,
    reference_weight_kg   DECIMAL(18,3)     NULL,

    -- Brechas calculadas por MySQL (NULLIF evita dividir entre cero)
    gap_usd   DECIMAL(18,2) GENERATED ALWAYS AS (value_usd - reference_value_usd) STORED,
    gap_pct   DECIMAL(9,4)  GENERATED ALWAYS AS (
                  (value_usd - reference_value_usd) / NULLIF(reference_value_usd, 0) * 100
              ) STORED,

    probable_cause        VARCHAR(500)      NULL,      -- ej. 'BCE incluye partidas 1605'
    computed_at           DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (recon_id),
    UNIQUE KEY uq_recon_grain (period_date, comparison_level, country_key, source_id),
    KEY ix_recon_period (period_date),

    CONSTRAINT chk_recon_first_day CHECK (DAY(period_date) = 1),
    CONSTRAINT chk_recon_level     CHECK (comparison_level IN ('total', 'country')),
    -- Coherencia: si es 'total' no lleva país; si es 'country', sí
    CONSTRAINT chk_recon_country   CHECK (
        (comparison_level = 'total'   AND country_id IS NULL) OR
        (comparison_level = 'country' AND country_id IS NOT NULL)
    ),
    CONSTRAINT chk_recon_not_self  CHECK (source_id <> reference_source_id),

    CONSTRAINT fk_recon_country FOREIGN KEY (country_id)          REFERENCES silver.countries (country_id),
    CONSTRAINT fk_recon_source  FOREIGN KEY (source_id)           REFERENCES silver.data_sources (source_id),
    CONSTRAINT fk_recon_ref     FOREIGN KEY (reference_source_id) REFERENCES silver.data_sources (source_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: comparación CNA/BCE vs Comtrade, sin descartar ninguna fuente';

-- -----------------------------------------------------------------------------

-- 4.1 Fincas
CREATE TABLE IF NOT EXISTS silver.farms (
    farm_id           SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    farm_code         VARCHAR(20)   NOT NULL,          -- ej. 'FARM-GYE'
    farm_name         VARCHAR(100)  NOT NULL,
    province          VARCHAR(50)   NOT NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (farm_id),
    UNIQUE KEY uq_farms_code (farm_code),
    CONSTRAINT chk_farms_province CHECK (
        province IN ('Guayas', 'El Oro', 'Santa Elena', 'Esmeraldas', 'Manabí')
    )
) ENGINE = InnoDB
  COMMENT = 'SILVER: fincas camaroneras (SIMULADO)';

-- 4.2 Piscinas
CREATE TABLE IF NOT EXISTS silver.ponds (
    pond_id           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    farm_id           SMALLINT UNSIGNED NOT NULL,
    pond_code         VARCHAR(20)   NOT NULL,          -- ej. 'P-07' (único dentro de la finca)
    area_ha           DECIMAL(8,2)  NOT NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (pond_id),
    UNIQUE KEY uq_ponds_farm_code (farm_id, pond_code),
    CONSTRAINT chk_ponds_area CHECK (area_ha > 0 AND area_ha <= 50),
    CONSTRAINT fk_ponds_farm FOREIGN KEY (farm_id) REFERENCES silver.farms (farm_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: piscinas de cultivo (SIMULADO)';

-- 4.3 Ciclos de producción: una siembra en una piscina
CREATE TABLE IF NOT EXISTS silver.production_cycles (
    cycle_id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    pond_id           INT UNSIGNED  NOT NULL,
    cycle_code        VARCHAR(30)   NOT NULL,
    stocking_date     DATE          NOT NULL,          -- fecha de siembra
    final_harvest_date DATE         NULL,              -- NULL = ciclo en curso
    -- Días de cultivo calculados por MySQL
    days_of_culture   SMALLINT GENERATED ALWAYS AS (DATEDIFF(final_harvest_date, stocking_date)) STORED,
    larvae_stocked    INT UNSIGNED  NOT NULL,          -- hecho medido (la densidad se calcula)
    feed_kg           DECIMAL(12,2) NULL,              -- alimento total del ciclo (hecho medido)
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (cycle_id),
    UNIQUE KEY uq_cycles_code (cycle_code),
    KEY ix_cycles_pond_date (pond_id, stocking_date),
    CONSTRAINT chk_cycles_dates  CHECK (final_harvest_date IS NULL OR final_harvest_date > stocking_date),
    CONSTRAINT chk_cycles_larvae CHECK (larvae_stocked > 0),
    CONSTRAINT chk_cycles_feed   CHECK (feed_kg IS NULL OR feed_kg >= 0),
    CONSTRAINT fk_cycles_pond FOREIGN KEY (pond_id) REFERENCES silver.ponds (pond_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: ciclos de cultivo por piscina (SIMULADO)';

-- 4.4 Costos del ciclo (formato largo: una fila por tipo de costo)
CREATE TABLE IF NOT EXISTS silver.cycle_costs (
    cost_id           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    cycle_id          INT UNSIGNED  NOT NULL,
    cost_type         VARCHAR(20)   NOT NULL,          -- larvae | feed | energy | labor
    amount_usd        DECIMAL(14,2) NOT NULL,

    PRIMARY KEY (cost_id),
    UNIQUE KEY uq_costs_cycle_type (cycle_id, cost_type),
    CONSTRAINT chk_costs_type   CHECK (cost_type IN ('larvae', 'feed', 'energy', 'labor')),
    CONSTRAINT chk_costs_amount CHECK (amount_usd >= 0),
    CONSTRAINT fk_costs_cycle FOREIGN KEY (cycle_id) REFERENCES silver.production_cycles (cycle_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: costos por ciclo y tipo (SIMULADO)';

-- 4.5 Cosechas: un ciclo puede tener raleos (parciales) y una cosecha final
CREATE TABLE IF NOT EXISTS silver.harvests (
    harvest_id        INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    cycle_id          INT UNSIGNED  NOT NULL,
    harvest_date      DATE          NOT NULL,
    harvest_type      VARCHAR(10)   NOT NULL,          -- partial | final
    harvested_kg      DECIMAL(12,2) NOT NULL,          -- unidad canónica
    harvested_lb      DECIMAL(12,2) GENERATED ALWAYS AS (ROUND(harvested_kg * 2.20462, 2)) STORED,
    avg_weight_g      DECIMAL(6,2)  NOT NULL,          -- peso promedio del camarón
    size_id           SMALLINT UNSIGNED NOT NULL,      -- talla asignada por la empacadora

    PRIMARY KEY (harvest_id),
    UNIQUE KEY uq_harvests_cycle_date (cycle_id, harvest_date),
    CONSTRAINT chk_harvests_type   CHECK (harvest_type IN ('partial', 'final')),
    CONSTRAINT chk_harvests_kg     CHECK (harvested_kg > 0),
    CONSTRAINT chk_harvests_weight CHECK (avg_weight_g > 0 AND avg_weight_g <= 60),
    CONSTRAINT fk_harvests_cycle FOREIGN KEY (cycle_id) REFERENCES silver.production_cycles (cycle_id),
    CONSTRAINT fk_harvests_size  FOREIGN KEY (size_id)  REFERENCES silver.sizes (size_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: cosechas parciales y finales (SIMULADO)';

-- 4.6 Ventas de cada cosecha
CREATE TABLE IF NOT EXISTS silver.sales (
    sale_id                 INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    harvest_id              INT UNSIGNED  NOT NULL,
    sale_date               DATE          NOT NULL,
    sold_lb                 DECIMAL(12,2) NOT NULL,
    price_usd_lb            DECIMAL(8,4)  NOT NULL,    -- 4 decimales: precios por libra finos
    revenue_usd             DECIMAL(14,2) GENERATED ALWAYS AS (ROUND(sold_lb * price_usd_lb, 2)) STORED,
    destination_country_id  SMALLINT UNSIGNED NULL,    -- mercado destino declarado
    created_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (sale_id),
    KEY ix_sales_harvest (harvest_id),
    KEY ix_sales_date (sale_date),
    CONSTRAINT chk_sales_lb    CHECK (sold_lb > 0),
    CONSTRAINT chk_sales_price CHECK (price_usd_lb > 0),
    CONSTRAINT fk_sales_harvest FOREIGN KEY (harvest_id)             REFERENCES silver.harvests (harvest_id),
    CONSTRAINT fk_sales_country FOREIGN KEY (destination_country_id) REFERENCES silver.countries (country_id)
) ENGINE = InnoDB
  COMMENT = 'SILVER: ventas por cosecha (SIMULADO)';