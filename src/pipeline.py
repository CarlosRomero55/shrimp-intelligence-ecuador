"""
pipeline.py — Ejecuta todo el proceso en orden con un solo comando (Fase 8, Paso 8.2).

Etapas (todas con el mismo batch_id y registradas en ops.etl_runs):
  1. extract.comtrade        solo con --extract: extracción INCREMENTAL de UN Comtrade
                             (meses recientes y faltantes; usa la llave de la API)
  2. silver.export_monthly   clean_exports.run()
  3. silver.farm             clean_farm.run()
  4. silver.reconciliation   reconcile.run()      (necesita export_monthly limpio)
  5. gold.build              build_gold.run()     (necesita todo silver)
  6. docs.quality_report     quality_report.run()
  7. quality_gate.pytest     pytest -m db         (puerta de calidad sobre el resultado)

Decisiones de la Fase 8: Python puro (sin Airflow/Prefect), fallar rápido (si una etapa
falla, las siguientes no corren) y pytest como puerta de calidad final.

Código de salida: 0 = todo bien · 1 = alguna etapa falló (útil para programarlo).

Uso:
    python -m src.pipeline                      # transformaciones + reporte + pruebas
    python -m src.pipeline --extract            # además trae datos nuevos de Comtrade
    python -m src.pipeline --skip-tests
    python -m src.pipeline --triggered-by scheduler
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time

import pandas as pd

from src import (build_gold, clean_exports, clean_farm, extract_comtrade, quality_report,
                 reconcile)
from src.config import ROOT_DIR
from src.db import read_sql
from src.run_log import make_run_batch_id, track_step

logger = logging.getLogger(__name__)

PYTEST_TIMEOUT_SEC = 600


# ------------------------------------------------------------------ utilidades
def rule_rows(rules: pd.DataFrame, *names: str) -> int | None:
    """Suma las filas afectadas de las reglas indicadas (None si no existe ninguna)."""
    found = rules[rules["regla"].isin(names)]
    return None if found.empty else int(found["filas_afectadas"].sum())


def rejected_rows(rules: pd.DataFrame) -> int:
    """Filas excluidas por las reglas de limpieza (acción 'excluidas')."""
    return int(rules.loc[rules["accion"] == "excluidas", "filas_afectadas"].sum())


def run_quality_gate() -> tuple[int, int, str]:
    """Corre pytest -m db en un proceso aparte. Devuelve (pasaron, fallaron, resumen)."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "db", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT_DIR, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=PYTEST_TIMEOUT_SEC,
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    summary = lines[-1] if lines else "(pytest no devolvió salida)"

    # Resumen típico: "35 passed, 1 failed in 3.20s" → {"passed": 35, "failed": 1}
    counts = {word: int(n) for n, word in re.findall(r"(\d+) (passed|failed|errors?)", summary)}
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    if result.returncode != 0:
        failures = [ln for ln in lines if ln.startswith(("FAILED", "ERROR"))][:5]
        detail = " | ".join(failures) if failures else summary
        raise RuntimeError(f"pytest -m db falló ({summary}): {detail}")
    return passed, failed, summary


# ------------------------------------------------------------------ etapas
def run_pipeline(batch_id: str, *, triggered_by: str, skip_tests: bool,
                 extract: bool = False) -> None:
    common = {"triggered_by": triggered_by}

    # 1) Extracción incremental de UN Comtrade (solo con --extract: usa la llave de la API).
    #    El BCE sigue siendo descarga manual (docs/fuentes/bce_descarga_manual.md).
    with track_step(batch_id, "extract.comtrade", source_name="UN Comtrade", **common) as step:
        if extract:
            result = extract_comtrade.extract_incremental()
            step.rows_read = result["export_months"] + result["mirror_months"]  # meses pedidos
            step.rows_inserted = result["export_new"] + result["mirror_new"]    # filas nuevas
            logger.info("Extracción incremental: %s", result)
        else:
            step.skip("sin --extract: se usan los datos ya cargados en bronze")

    # 2) Exportaciones → silver.export_monthly
    with track_step(batch_id, "silver.export_monthly", source_name="UN Comtrade",
                    **common) as step:
        rules = clean_exports.run()
        step.rows_read = rule_rows(rules, "filas_en_bronze")
        step.rows_inserted = rule_rows(rules, "filas_para_silver")
        step.rows_rejected = rejected_rows(rules)

    # 3) Finca SIMULADA → farms, ponds, cycles, harvests, costs, sales
    with track_step(batch_id, "silver.farm", source_name="Finca SIMULADA", **common) as step:
        rules, _verification = clean_farm.run()
        step.rows_read = rule_rows(rules, "finca_filas_lote_vigente")
        step.rows_inserted = rule_rows(rules, "ciclos_limpios")
        step.rows_rejected = rejected_rows(rules)

    # 4) Conciliación de fuentes (necesita export_monthly limpio)
    with track_step(batch_id, "silver.reconciliation", source_name="BCE + UN Comtrade",
                    **common) as step:
        rules = reconcile.run()
        step.rows_inserted = rule_rows(rules, "filas_conciliacion_total",
                                       "filas_conciliacion_pais")
        step.rows_rejected = rejected_rows(rules)

    # 5) Gold: dimensiones (upsert) + hechos (reconstrucción completa)
    with track_step(batch_id, "gold.build", **common) as step:
        _dims, facts = build_gold.run()
        step.rows_inserted = int(facts["total"].sum())

    # 6) Reporte de calidad (lee todo lo anterior)
    with track_step(batch_id, "docs.quality_report", **common):
        quality_report.run()

    # 7) Puerta de calidad: las pruebas de datos deben pasar sobre el resultado final
    with track_step(batch_id, "quality_gate.pytest", **common) as step:
        if skip_tests:
            step.skip("--skip-tests")
        else:
            passed, failed, summary = run_quality_gate()
            step.rows_read = passed + failed
            step.rows_inserted = passed          # pruebas que pasaron
            step.rows_rejected = failed
            logger.info("Puerta de calidad: %s", summary)


def print_summary(batch_id: str) -> None:
    """Muestra lo que quedó registrado en ops.etl_runs para esta ejecución."""
    runs = read_sql(
        """
        SELECT step_name                     AS etapa,
               status                        AS estado,
               duration_sec                  AS segundos,
               rows_read                     AS leidas,
               rows_inserted                 AS cargadas,
               rows_rejected                 AS excluidas,
               LEFT(error_message, 70)       AS error
        FROM ops.etl_runs
        WHERE batch_id = :b
        ORDER BY run_id
        """,
        {"b": batch_id},
    )
    for col in ["leidas", "cargadas", "excluidas"]:
        runs[col] = runs[col].astype("Int64")
    print(f"\nEjecución {batch_id} (ops.etl_runs):")
    shown = runs.astype(object).where(runs.notna(), "")     # vacíos en vez de <NA>
    print(shown.to_string(index=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline Shrimp Intelligence Ecuador")
    parser.add_argument("--extract", action="store_true",
                        help="extracción incremental de UN Comtrade (llama a la API con tu llave)")
    parser.add_argument("--skip-tests", action="store_true",
                        help="no correr la puerta de calidad (pytest -m db)")
    parser.add_argument("--triggered-by", default="manual",
                        help="quién lo dispara: manual | scheduler | ci (default: manual)")
    args = parser.parse_args()

    batch_id = make_run_batch_id()
    start = time.perf_counter()
    logger.info("Pipeline %s | iniciado por %s", batch_id, args.triggered_by)
    try:
        run_pipeline(batch_id, triggered_by=args.triggered_by, skip_tests=args.skip_tests,
                     extract=args.extract)
        code = 0
    except Exception:                       # el detalle ya quedó en ops.etl_runs y en el log
        logger.exception("Pipeline detenido")
        code = 1
    elapsed = time.perf_counter() - start

    print_summary(batch_id)
    print(f"\nResultado: {'OK' if code == 0 else 'FALLÓ'} en {elapsed:.1f} s "
          f"(código de salida {code})")
    return code


# Ejecutar: python -m src.pipeline
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    sys.exit(main())