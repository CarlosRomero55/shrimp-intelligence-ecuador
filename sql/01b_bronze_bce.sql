-- =====================================================================
-- 01b_bronze_bce.sql — Bronze: cuadros estadísticos del Banco Central del Ecuador
-- Copia fiel de la fuente: valores como texto (formato ecuatoriano 1.234,56).
-- Una fila por serie y mes. Inmutable: shrimp_etl solo puede SELECT/INSERT.
-- =====================================================================

CREATE TABLE IF NOT EXISTS bronze.bce_raw (
    raw_id          BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    -- Auditoría
    source_name     VARCHAR(50)      NOT NULL,             -- 'BCE'
    batch_id        VARCHAR(40)      NOT NULL,
    extracted_at    DATETIME(6)      NOT NULL,             -- fecha de consulta del cuadro
    source_file     VARCHAR(150)     NOT NULL,             -- nombre del Excel descargado
    -- Identificación del cuadro
    table_code      VARCHAR(20)      NOT NULL,             -- '3.1.2'
    table_unit      VARCHAR(50)      NULL,                 -- 'Miles de USD'
    -- Contenido (tal cual la fuente)
    series_order    SMALLINT UNSIGNED NOT NULL,            -- posición de la fila en el cuadro
    series_group    VARCHAR(100)     NULL,                 -- sección: 'Tradicionales', ...
    series_name     VARCHAR(150)     NOT NULL,             -- 'Camarón'
    period_label    VARCHAR(20)      NOT NULL,             -- 'Ene 2025'
    value_raw       VARCHAR(50)      NULL,                 -- '596.248,67089'
    record_hash     CHAR(64)         NOT NULL,             -- SHA-256 de tabla|serie|mes|valor
    PRIMARY KEY (raw_id),
    UNIQUE KEY uq_bce_raw_hash (record_hash),              -- misma cifra nunca se carga dos veces
    KEY idx_bce_raw_batch (batch_id),
    KEY idx_bce_raw_series_period (table_code, series_name, period_label)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci
  COMMENT = 'Bronze: cuadros BCEData del BCE (descarga manual documentada)';

-- Permisos: igual que el resto de bronze (sin UPDATE, DELETE ni DROP)
GRANT SELECT, INSERT ON bronze.bce_raw TO 'shrimp_etl'@'localhost';

-- Verificación
SHOW GRANTS FOR 'shrimp_etl'@'localhost';
DESCRIBE bronze.bce_raw;