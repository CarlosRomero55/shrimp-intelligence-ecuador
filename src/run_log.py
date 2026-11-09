"""
run_log.py — Registro de cada etapa del pipeline en ops.etl_runs (Fase 8, Paso 8.1).

Uso:
    batch_id = make_run_batch_id()
    with track_step(batch_id, "silver.export_monthly") as step:
        clean_exports.run()
        step.rows_inserted = 10202        # opcional: lo que la etapa sepa reportar

Qué hace:
  - Al ENTRAR inserta una fila con status = 'running' y la hora de inicio.
  - Al SALIR BIEN la actualiza a 'success' (o 'skipped' si la etapa llamó step.skip()).
  - Si hay un ERROR la actualiza a 'failed' con el mensaje y VUELVE A LANZAR el error:
    el pipeline se detiene (fallar rápido).

Cada escritura del registro va en SU PROPIA transacción, separada de la etapa: si la
etapa hace ROLLBACK, el registro del fallo igual queda guardado.
(Las pruebas pasan conn= para escribir dentro de una transacción que luego se deshace.)

duration_sec es columna GENERADA en ops.etl_runs: MySQL la calcula de las dos fechas.
Estados permitidos (chk_runs_status): running | success | failed | skipped.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.db import get_engine

logger = logging.getLogger(__name__)

PIPELINE_NAME = "shrimp_intelligence"
MAX_ERROR_CHARS = 2000                 # suficiente para el mensaje; la traza va al log

INSERT_SQL = """
INSERT INTO ops.etl_runs
    (batch_id, pipeline_name, step_name, source_name, status, started_at, triggered_by)
VALUES (:batch_id, :pipeline_name, :step_name, :source_name, 'running', :started_at,
        :triggered_by)
"""

FINISH_SQL = """
UPDATE ops.etl_runs
SET status        = :status,
    finished_at   = :finished_at,
    rows_read     = :rows_read,
    rows_inserted = :rows_inserted,
    rows_updated  = :rows_updated,
    rows_rejected = :rows_rejected,
    error_message = :error_message
WHERE run_id = :run_id
"""


def make_run_batch_id(prefix: str = "run") -> str:
    """Identificador común a todas las etapas de UNA ejecución (máx. 40 caracteres)."""
    return f"{prefix}-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


@dataclass
class StepRun:
    """Lo que la etapa puede completar mientras corre (todo opcional)."""
    run_id: int
    step_name: str
    rows_read: int | None = None
    rows_inserted: int | None = None
    rows_updated: int | None = None
    rows_rejected: int | None = None
    skipped_reason: str | None = None

    def skip(self, reason: str) -> None:
        """Marca la etapa como 'skipped' (ej. --extract no se pidió)."""
        self.skipped_reason = reason


def _execute(sql: str, params: dict, conn: Connection | None):
    """Usa la transacción de quien llama (pruebas) o abre una propia (pipeline)."""
    if conn is not None:
        return conn.execute(text(sql), params)
    with get_engine().begin() as own_conn:
        return own_conn.execute(text(sql), params)


def _finish(step: StepRun, status: str, conn: Connection | None,
            error_message: str | None = None) -> None:
    _execute(FINISH_SQL, {
        "run_id": step.run_id, "status": status, "finished_at": datetime.now(),
        "rows_read": step.rows_read, "rows_inserted": step.rows_inserted,
        "rows_updated": step.rows_updated, "rows_rejected": step.rows_rejected,
        "error_message": error_message,
    }, conn)


@contextmanager
def track_step(batch_id: str, step_name: str, *, source_name: str | None = None,
               triggered_by: str = "manual", pipeline_name: str = PIPELINE_NAME,
               conn: Connection | None = None) -> Iterator[StepRun]:
    """Registra una etapa en ops.etl_runs: running → success | skipped | failed."""
    result = _execute(INSERT_SQL, {
        "batch_id": batch_id, "pipeline_name": pipeline_name, "step_name": step_name,
        "source_name": source_name, "started_at": datetime.now(),
        "triggered_by": triggered_by,
    }, conn)
    step = StepRun(run_id=int(result.lastrowid), step_name=step_name)
    logger.info("▶ %-28s | run_id %s | inicia", step_name, step.run_id)

    try:
        yield step
    except BaseException as exc:                 # también Ctrl+C: queda como 'failed'
        message = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
        _finish(step, "failed", conn, error_message=message)
        logger.error("✖ %-28s | run_id %s | FALLÓ: %s", step_name, step.run_id, message)
        raise                                     # fallar rápido: el pipeline se detiene
    else:
        if step.skipped_reason:
            _finish(step, "skipped", conn)
            logger.info("↷ %-28s | run_id %s | omitida: %s", step_name, step.run_id,
                        step.skipped_reason)
        else:
            _finish(step, "success", conn)
            logger.info("✔ %-28s | run_id %s | ok", step_name, step.run_id)