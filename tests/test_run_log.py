"""
test_run_log.py — Pruebas del registro de etapas en ops.etl_runs (Fase 8, Paso 8.1).

Cada prueba escribe dentro de una transacción que se deshace al final (ROLLBACK):
no quedan filas de prueba en ops.etl_runs.

Ejecutar: python -m pytest tests/test_run_log.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src.db import get_engine
from src.run_log import make_run_batch_id, track_step

pytestmark = pytest.mark.db          # usan MySQL


@pytest.fixture
def conn():
    """Conexión con transacción abierta. Al terminar la prueba: ROLLBACK siempre."""
    with get_engine().connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _row(conn, run_id: int) -> dict:
    return dict(conn.execute(
        text("SELECT status, finished_at, duration_sec, rows_inserted, error_message, "
             "triggered_by FROM ops.etl_runs WHERE run_id = :id"),
        {"id": run_id},
    ).mappings().one())


def test_batch_id_cabe_en_la_columna():
    assert len(make_run_batch_id()) <= 40


def test_etapa_exitosa(conn):
    batch = make_run_batch_id("test")
    with track_step(batch, "pytest.ok", triggered_by="pytest", conn=conn) as step:
        step.rows_inserted = 5
    row = _row(conn, step.run_id)
    assert row["status"] == "success"
    assert row["finished_at"] is not None
    assert row["duration_sec"] is not None and row["duration_sec"] >= 0   # columna generada
    assert row["rows_inserted"] == 5
    assert row["error_message"] is None
    assert row["triggered_by"] == "pytest"


def test_etapa_fallida_registra_error_y_relanza(conn):
    batch = make_run_batch_id("test")
    with pytest.raises(ValueError, match="explotó"):
        with track_step(batch, "pytest.falla", triggered_by="pytest", conn=conn) as step:
            raise ValueError("la etapa explotó")
    row = _row(conn, step.run_id)
    assert row["status"] == "failed"
    assert "ValueError: la etapa explotó" in row["error_message"]   # chk_runs_error
    assert row["finished_at"] is not None


def test_etapa_omitida(conn):
    batch = make_run_batch_id("test")
    with track_step(batch, "pytest.omitida", triggered_by="pytest", conn=conn) as step:
        step.skip("no se pidió --extract")
    assert _row(conn, step.run_id)["status"] == "skipped"


def test_etapas_de_una_ejecucion_comparten_batch(conn):
    batch = make_run_batch_id("test")
    for name in ["pytest.a", "pytest.b", "pytest.c"]:
        with track_step(batch, name, triggered_by="pytest", conn=conn):
            pass
    n = conn.execute(text("SELECT COUNT(*) FROM ops.etl_runs WHERE batch_id = :b"),
                     {"b": batch}).scalar_one()
    assert n == 3