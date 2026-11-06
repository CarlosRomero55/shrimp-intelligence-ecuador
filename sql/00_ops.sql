-- =============================================================================
-- 00_ops.sql — Esquema OPS: metadatos y auditoría del pipeline
-- Proyecto: Shrimp Intelligence Ecuador
-- Ejecutar con el usuario: shrimp_etl
-- A diferencia de bronze, aquí SÍ se actualizan filas (running -> success/failed).
-- =============================================================================

-- Autosuficiente: crea el esquema si no existe (se puede ejecutar en cualquier orden)
CREATE DATABASE IF NOT EXISTS ops CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- Bitácora: una fila por paso del pipeline dentro de cada ejecución
CREATE TABLE IF NOT EXISTS ops.etl_runs (
    run_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_id          VARCHAR(40)   NOT NULL,          -- mismo ID que bronze y silver
    pipeline_name     VARCHAR(50)   NOT NULL DEFAULT 'shrimp_pipeline',
    step_name         VARCHAR(50)   NOT NULL,          -- extract_comtrade, clean, build_gold...
    source_name       VARCHAR(50)   NULL,              -- un_comtrade, cna, bce, farm_simulation
    status            VARCHAR(10)   NOT NULL DEFAULT 'running',
    started_at        DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at       DATETIME(6)   NULL,
    -- Duración calculada por MySQL (NULL mientras el paso sigue corriendo)
    duration_sec      DECIMAL(10,3) GENERATED ALWAYS AS (
                          TIMESTAMPDIFF(MICROSECOND, started_at, finished_at) / 1000000
                      ) STORED,

    -- Métricas del paso
    rows_read         INT UNSIGNED  NULL,
    rows_inserted     INT UNSIGNED  NULL,
    rows_updated      INT UNSIGNED  NULL,
    rows_rejected     INT UNSIGNED  NULL,              -- registros que la limpieza descartó

    error_message     TEXT          NULL,              -- solo si status = 'failed'
    triggered_by      VARCHAR(50)   NOT NULL DEFAULT 'manual',

    PRIMARY KEY (run_id),
    UNIQUE KEY uq_runs_batch_step (batch_id, step_name),
    KEY ix_runs_started (started_at),
    KEY ix_runs_status (status),

    CONSTRAINT chk_runs_status   CHECK (status IN ('running', 'success', 'failed', 'skipped')),
    CONSTRAINT chk_runs_finished CHECK (finished_at IS NULL OR finished_at >= started_at),
    -- Coherencia: un paso fallido debe explicar por qué
    CONSTRAINT chk_runs_error    CHECK (status <> 'failed' OR error_message IS NOT NULL)
) ENGINE = InnoDB
  COMMENT = 'OPS: bitácora de ejecuciones del pipeline (una fila por paso)';