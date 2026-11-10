"""
run_queries.py — Ejecuta las consultas de negocio de sql/queries/ (Fase 9).

Cada archivo sql/queries/qNN_nombre.sql es UNA pregunta de negocio. Su encabezado
(líneas que empiezan con "--") explica qué responde y cómo leer el resultado.
Este script las ejecuta, muestra el resultado y lo guarda en
data/processed/queries/qNN_nombre.csv (para el EDA, la API y Power BI).

Solo LEE gold: no modifica la base.

Ejecutar:
    python -m src.run_queries              # todas
    python -m src.run_queries q01 q03      # solo algunas (por prefijo)
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from src.config import ROOT_DIR
from src.db import read_sql

logger = logging.getLogger(__name__)

QUERIES_DIR = ROOT_DIR / "sql" / "queries"
OUTPUT_DIR = ROOT_DIR / "data" / "processed" / "queries"
MAX_ROWS_ON_SCREEN = 40


def list_queries(prefixes: list[str] | None = None) -> list[Path]:
    """Archivos q*.sql en orden; si se pasan prefijos ('q01'), solo esos."""
    files = sorted(QUERIES_DIR.glob("q*.sql"))
    if prefixes:
        files = [f for f in files if any(f.stem.startswith(p) for p in prefixes)]
    return files


def read_query(path: Path) -> tuple[str, str]:
    """Devuelve (título, SQL). El título es la primera línea '-- qNN · ...' del encabezado."""
    sql = path.read_text(encoding="utf-8").strip().rstrip(";")
    title = next((line.lstrip("- ").strip() for line in sql.splitlines()
                  if line.startswith("--") and "·" in line), path.stem)
    return title, sql


def run_query(path: Path) -> pd.DataFrame:
    title, sql = read_query(path)
    df = read_sql(sql)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{path.stem}.csv"
    df.to_csv(out, index=False, encoding="utf-8")

    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    print(df.head(MAX_ROWS_ON_SCREEN).to_string(index=False))
    if len(df) > MAX_ROWS_ON_SCREEN:
        print(f"... {len(df) - MAX_ROWS_ON_SCREEN} filas más en el CSV")
    print(f"→ {len(df)} filas · {out.relative_to(ROOT_DIR)}")
    return df


def short_error(exc: Exception) -> str:
    """Solo el mensaje de MySQL (sin la traza ni el SQL completo)."""
    cause = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    return f"{type(cause).__name__}: {cause}"


def run(prefixes: list[str] | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Ejecuta cada consulta; si una falla, muestra el error corto y sigue con las demás."""
    files = list_queries(prefixes)
    if not files:
        raise FileNotFoundError(f"No hay consultas q*.sql en {QUERIES_DIR} "
                                f"(filtro: {prefixes or 'ninguno'})")
    results, errors = {}, {}
    for f in files:
        try:
            results[f.stem] = run_query(f)
        except Exception as exc:                  # noqa: BLE001 — se reporta y se sigue
            errors[f.stem] = short_error(exc)
            print(f"\n✖ {f.stem} FALLÓ → {errors[f.stem]}")
    return results, errors


# Ejecutar: python -m src.run_queries [q01 q02 ...]
if __name__ == "__main__":
    from src.logging_config import setup_logging

    parser = argparse.ArgumentParser(description="Consultas de negocio sobre gold")
    parser.add_argument("prefixes", nargs="*", help="prefijos de consulta, ej. q01 q03")
    args = parser.parse_args()

    setup_logging()
    results, errors = run(args.prefixes)
    print(f"\nListo: {len(results)} consulta(s) correctas, {len(errors)} con error.")
    raise SystemExit(1 if errors else 0)