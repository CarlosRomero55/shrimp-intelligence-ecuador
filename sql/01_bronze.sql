CREATE DATABASE IF NOT EXISTS bronze CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS silver CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS gold   CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS ops    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS bronze.comtrade_raw (
    raw_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Auditoría: de dónde, en qué ejecución y cuándo llegó el dato
    source_name     VARCHAR(50)  NOT NULL DEFAULT 'un_comtrade',
    batch_id        VARCHAR(40)  NOT NULL,
    extracted_at    DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    -- Campos clave como TEXTO (nombres en snake_case; el original está en raw_payload)
    freq_code       VARCHAR(5),        -- M = mensual
    period          VARCHAR(10),       -- AAAAMM, ej. 202503
    reporter_code   VARCHAR(10),       -- 218 = Ecuador
    flow_code       VARCHAR(5),        -- X = exportación
    partner_code    VARCHAR(10),       -- código M49 del país destino
    partner_iso     VARCHAR(10),
    partner_desc    VARCHAR(150),
    cmd_code        VARCHAR(20),       -- partida HS, ej. 030617
    cmd_desc        VARCHAR(500),
    qty_unit_abbr   VARCHAR(20),
    qty             VARCHAR(50),
    net_wgt         VARCHAR(50),       -- peso neto en kg
    fob_value       VARCHAR(50),
    primary_value   VARCHAR(50),       -- valor principal en USD

    -- Registro original completo, por si necesitamos un campo no extraído arriba
    raw_payload     JSON         NOT NULL,

    -- Huella SHA-256 del registro: evita duplicados al reintentar el mismo lote
    record_hash     CHAR(64)     NOT NULL,

    PRIMARY KEY (raw_id),
    UNIQUE KEY uq_comtrade_batch_record (batch_id, record_hash),
    KEY ix_comtrade_period_cmd (period, cmd_code)
) ENGINE = InnoDB
  COMMENT = 'BRONZE: exportaciones de Ecuador desde UN Comtrade, copia fiel de la API';

-- -----------------------------------------------------------------------------
-- 3. bronze.farm_raw — Exportación "plana" del sistema de la finca (SIMULADO)
--    Simula un Excel sucio: fechas en varios formatos, tallas escritas distinto,
--    libras y kg mezclados, países escritos de varias formas.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.farm_raw (
    raw_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    -- Auditoría
    source_name         VARCHAR(50)  NOT NULL DEFAULT 'farm_simulation',
    batch_id            VARCHAR(40)  NOT NULL,
    extracted_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    -- Finca y piscina
    farm_name           VARCHAR(100),
    province            VARCHAR(100),
    pond_code           VARCHAR(50),
    pond_area_ha        VARCHAR(50),

    -- Ciclo de cultivo
    cycle_code          VARCHAR(50),
    stocking_date       VARCHAR(50),   -- fecha de siembra, formatos mezclados
    harvest_date        VARCHAR(50),
    days_of_culture     VARCHAR(50),
    density_pl_m2       VARCHAR(50),   -- larvas por m²
    survival_pct        VARCHAR(50),
    avg_weight_g        VARCHAR(50),
    size_grade          VARCHAR(50),   -- "20/30", "20-30", "20 30"...

    -- Cosecha y alimento
    harvested_qty       VARCHAR(50),
    harvested_unit      VARCHAR(20),   -- "lb", "kg", "LB", "Kg"...
    feed_kg             VARCHAR(50),
    fcr                 VARCHAR(50),

    -- Costos del ciclo (USD)
    cost_larvae_usd     VARCHAR(50),
    cost_feed_usd       VARCHAR(50),
    cost_energy_usd     VARCHAR(50),
    cost_labor_usd      VARCHAR(50),

    -- Venta
    sale_price_usd_lb   VARCHAR(50),
    destination_market  VARCHAR(100),  -- "EEUU", "USA", "Estados Unidos"...

    record_hash         CHAR(64)     NOT NULL,

    PRIMARY KEY (raw_id),
    UNIQUE KEY uq_farm_batch_record (batch_id, record_hash),
    KEY ix_farm_pond_cycle (pond_code, cycle_code)
) ENGINE = InnoDB
  COMMENT = 'BRONZE: exportación plana del sistema de la finca (DATOS SIMULADOS)';