"""
quality_report.py — Genera docs/data_quality_report.md (Fase 6, Paso 6.3).

Reúne en un solo documento lo que ya registraron las limpiezas:
  1. Antes vs. después: filas en bronze → filas en silver, por fuente.
  2. Reglas aplicadas que tocaron datos (data/processed/quality_*.csv).
  3. Imputaciones y verificación de la finca contra la verdad de la simulación.
  4. Cobertura de fuentes y huecos conocidos (desde silver.source_reconciliation).
  5. Pruebas automáticas (pytest) y cómo correrlas.

Solo LEE: no modifica la base ni los CSV. Se vuelve a correr después de cada carga para
que el reporte refleje siempre los datos actuales.

Ejecutar: python -m src.quality_report
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.catalogs import SOURCE_IDS
from src.config import ROOT_DIR
from src.db import read_sql

logger = logging.getLogger(__name__)

PROCESSED = ROOT_DIR / "data" / "processed"
REPORT_PATH = ROOT_DIR / "docs" / "data_quality_report.md"
RULE_FILES = {                                   # archivo → etapa de limpieza
    "quality_exports.csv": "Exportaciones (Comtrade X y espejo) → `export_monthly`",
    "quality_farm.csv": "Finca SIMULADA → `farms`, `ponds`, `production_cycles`, "
                        "`harvests`, `cycle_costs`, `sales`",
    "quality_reconciliation.csv": "Conciliación de fuentes → `source_reconciliation`",
}
VERIFY_FILE = "farm_verification.csv"
YEAR_COLUMNS = {"anio", "año", "year"}         # años: sin separador de miles (2023, no 2.023)
# Acciones que no corrigen nada (solo leen o cargan): no se listan como reglas
NEUTRAL_ACTIONS = {"lectura", "carga", "comparadas"}

# ------------------------------------------------------------------ antes vs. después
# Cada fila: qué se compara, conteo en bronze, conteo en silver y por qué cambia.
BEFORE_AFTER = [
    {
        "dato": "Exportaciones de Ecuador (Comtrade X)",
        "bronze": "SELECT COUNT(*) AS n FROM bronze.comtrade_raw WHERE flow_code = 'X'",
        "silver": "SELECT COUNT(*) AS n FROM silver.export_monthly WHERE source_id = :x",
        "por_que": "Se excluye la fila 'World' (total, no es un país) y versiones antiguas.",
    },
    {
        "dato": "Importaciones desde Ecuador (espejo)",
        "bronze": "SELECT COUNT(*) AS n FROM bronze.comtrade_raw WHERE flow_code = 'M'",
        "silver": "SELECT COUNT(*) AS n FROM silver.export_monthly WHERE source_id = :m",
        "por_que": "Se excluyen valores vacíos o en cero y versiones antiguas.",
    },
    {
        "dato": "BCE serie 'Camarón' (cuadro 3.1.2)",
        "bronze": "SELECT COUNT(*) AS n FROM bronze.bce_raw "
                  "WHERE table_code = '3.1.2' AND series_name = 'Camarón'",
        "silver": "SELECT COUNT(*) AS n FROM silver.source_reconciliation "
                  "WHERE comparison_level = 'total'",
        "por_que": "No va a export_monthly (no tiene país): es la referencia nacional. "
                   "Cada mes se compara con 2 fuentes → 2 filas por mes.",
    },
    {
        "dato": "Finca SIMULADA: ciclos",
        "bronze": "SELECT COUNT(*) AS n FROM bronze.farm_raw",
        "silver": "SELECT COUNT(*) AS n FROM silver.production_cycles",
        "por_que": "Se excluyen duplicados exactos. Cada ciclo genera además 1 cosecha, "
                   "1 venta y 4 costos.",
    },
]

SILVER_TABLES = ["regions", "countries", "country_aliases", "products", "sizes",
                 "data_sources", "export_monthly", "source_reconciliation", "farms",
                 "ponds", "production_cycles", "harvests", "cycle_costs", "sales"]

COVERAGE_SQL = """
SELECT s.source_code                        AS fuente,
       COUNT(*)                             AS filas,
       COUNT(DISTINCT e.country_id)         AS paises,
       COUNT(DISTINCT e.period_date)        AS meses,
       MIN(e.period_date)                   AS desde,
       MAX(e.period_date)                   AS hasta,
       ROUND(SUM(e.value_usd) / 1e6, 1)     AS usd_millones
FROM silver.export_monthly AS e
JOIN silver.data_sources AS s ON s.source_id = e.source_id
GROUP BY s.source_code
ORDER BY s.source_code
"""

GAPS_SQL = """
SELECT s.source_code                                  AS fuente,
       YEAR(r.period_date)                            AS anio,
       CAST(SUM(r.value_usd IS NULL) AS SIGNED)       AS meses_sin_dato,
       CAST(COUNT(*) AS SIGNED)                       AS meses_del_bce
FROM silver.source_reconciliation AS r
JOIN silver.data_sources AS s ON s.source_id = r.source_id
WHERE r.comparison_level = 'total'
GROUP BY s.source_code, YEAR(r.period_date)
HAVING meses_sin_dato > 0
ORDER BY s.source_code, anio
"""

MIRROR_GAPS_SQL = """
SELECT c.iso3,
       c.country_name_es                              AS pais,
       CAST(SUM(r.value_usd IS NULL) AS SIGNED)       AS meses_sin_espejo,
       CAST(COUNT(*) AS SIGNED)                       AS meses_con_dato_de_ecuador
FROM silver.source_reconciliation AS r
JOIN silver.countries AS c ON c.country_id = r.country_id
WHERE r.comparison_level = 'country' AND r.reference_value_usd IS NOT NULL
GROUP BY c.iso3, c.country_name_es
HAVING meses_sin_espejo > 0
ORDER BY SUM(r.reference_value_usd) DESC
LIMIT 8
"""

TESTS_TEXT = """\
| Archivo | Tipo | Qué protege |
|---|---|---|
| `tests/test_cleaning_rules.py` | Unitarias (sin MySQL) | Funciones de limpieza: fechas en 4 formatos, decimales con coma, formato numérico del BCE, meses en español, tallas, alias de países |
| `tests/test_silver_data.py` | Datos (`db`) | Llaves únicas, sin huérfanos, FCR 0,8–3, supervivencia 0–100 %, peso ≤ 60 g, montos positivos, 4 costos por ciclo, libras vendidas = cosechadas, conciliación coherente |
| `tests/test_db.py` | Integración (`db`) | Conexión, parámetros enlazados, rollback, idempotencia de `upsert`, bronze protegido |
| `tests/test_sql_scripts.py` | Esquema (`db`) | Los scripts `sql/*.sql` coinciden con las tablas reales (schema drift) |

```powershell
python -m pytest -q                # todo
python -m pytest -m "not db" -q    # sin MySQL
python -m pytest -m db -v          # solo las que consultan la base
```
"""


# ------------------------------------------------------------------ utilidades
def fmt(value) -> str:
    """Formato para el reporte: miles con punto y decimales con coma (estilo Ecuador)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, int) or (isinstance(value, float) and float(value).is_integer()
                                  and abs(value) >= 1):
        return f"{int(value):,}".replace(",", ".")
    if isinstance(value, float):
        return f"{value:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(value).replace("|", "/")          # '|' rompería la tabla Markdown


def md_table(df: pd.DataFrame) -> str:
    """DataFrame → tabla Markdown (sin depender del paquete tabulate)."""
    if df.empty:
        return "_Sin filas._\n"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "|" + "---|" * len(df.columns)
    is_year = [str(c) in YEAR_COLUMNS for c in df.columns]
    body = ["| " + " | ".join(str(v) if year else fmt(v) for v, year in zip(row, is_year)) + " |"
            for row in df.astype(object).itertuples(index=False)]
    return "\n".join([header, sep, *body]) + "\n"


def count(sql: str) -> int:
    params = {"x": SOURCE_IDS["UN_COMTRADE"], "m": SOURCE_IDS["COMTRADE_MIRROR"]}
    used = {k: v for k, v in params.items() if f":{k}" in sql}
    return int(read_sql(sql, used or None)["n"].iloc[0])


def read_processed(name: str) -> pd.DataFrame | None:
    path = PROCESSED / name
    if not path.exists():
        logger.warning("No existe %s: ejecuta primero la limpieza que lo genera", path)
        return None
    return pd.read_csv(path, encoding="utf-8", keep_default_na=False)


# ------------------------------------------------------------------ secciones
def section_before_after() -> str:
    rows = []
    for item in BEFORE_AFTER:
        before, after = count(item["bronze"]), count(item["silver"])
        rows.append({"dato": item["dato"], "bronze": before, "silver": after,
                     "diferencia": after - before, "por qué cambia": item["por_que"]})
    table = pd.DataFrame(rows)

    sizes = []
    for t in SILVER_TABLES:
        sizes.append({"tabla silver": f"`{t}`",
                      "filas": count(f"SELECT COUNT(*) AS n FROM silver.{t}")})
    return ("## 1. Antes vs. después (bronze → silver)\n\n"
            "Bronze guarda todo tal como llegó (incluye versiones antiguas y filas que no "
            "son datos, como el total 'World'). Silver guarda solo filas limpias y únicas.\n\n"
            + md_table(table)
            + "\n**Filas por tabla de silver:**\n\n" + md_table(pd.DataFrame(sizes)))


def section_rules() -> str:
    parts = ["## 2. Reglas de limpieza aplicadas\n\n"
             "Solo se listan las reglas que afectaron al menos una fila. El registro completo "
             "(incluidas las reglas con 0 filas) está en `data/processed/quality_*.csv`.\n"]
    for name, stage in RULE_FILES.items():
        rules = read_processed(name)
        parts.append(f"\n### {stage}\n\n")
        if rules is None:
            parts.append(f"_Falta `{name}`: ejecutar la limpieza correspondiente._\n")
            continue
        rules["filas_afectadas"] = pd.to_numeric(rules["filas_afectadas"], errors="coerce")
        active = rules[(rules["filas_afectadas"] > 0)
                       & ~rules["accion"].isin(NEUTRAL_ACTIONS)]
        parts.append(md_table(active.rename(columns={
            "regla": "regla", "filas_afectadas": "filas", "accion": "acción",
            "detalle": "detalle"})))
    return "".join(parts)


def section_imputation() -> str:
    text = ["## 3. Imputaciones y verificación (finca SIMULADA)\n\n"
            "Política (decisión del 2026-11-09): un dato obligatorio nulo se **imputa y se "
            "marca**, en vez de descartar el ciclo. Como la finca es simulada, existe la "
            "verdad sin errores (`farm_clean_truth.csv`) y se puede medir el error de cada "
            "imputación.\n\n"]
    rules = read_processed("quality_farm.csv")
    if rules is not None:
        rules["filas_afectadas"] = pd.to_numeric(rules["filas_afectadas"], errors="coerce")
        imputed = rules[rules["accion"].str.startswith("imputad") & (rules["filas_afectadas"] > 0)]
        text.append("**Qué se imputó y cómo:**\n\n")
        text.append(md_table(imputed[["regla", "filas_afectadas", "detalle"]].rename(
            columns={"filas_afectadas": "filas"})))
    verification = read_processed(VERIFY_FILE)
    text.append("\n**Verificación contra la verdad de la simulación:**\n\n")
    text.append(md_table(verification) if verification is not None
                else f"_Falta `{VERIFY_FILE}`: ejecutar `python -m src.clean_farm`._\n")
    text.append("\n> Nota: el error de 0,0 % en costos imputados se debe a que la simulación "
                "calcula la energía de forma proporcional a hectáreas × días. Con datos reales "
                "el error sería mayor; el método es correcto, la precisión depende de la "
                "regularidad del dato.\n")
    return "".join(text)


def section_coverage() -> str:
    coverage = read_sql(COVERAGE_SQL)
    gaps = read_sql(GAPS_SQL)
    mirror_gaps = read_sql(MIRROR_GAPS_SQL)
    return ("## 4. Cobertura de fuentes y huecos conocidos\n\n"
            "**Detalle por país en `export_monthly`:**\n\n" + md_table(coverage)
            + "\n**Meses del BCE sin dato en la fuente de detalle** (nivel total):\n\n"
            + md_table(gaps)
            + "\n**Destinos principales con meses sin espejo** (el importador no reportó, "
              "en meses en que Ecuador sí publicó):\n\n" + md_table(mirror_gaps)
            + "\nInterpretación y estrategia de fuentes: ver `docs/adr/` (ADR-006).\n")


def build_report() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        "# Reporte de calidad de datos — Shrimp Intelligence Ecuador\n\n"
        f"_Generado automáticamente por `src/quality_report.py` el {now}. "
        "No editar a mano: volver a ejecutar el script después de cada carga._\n\n"
        "Datos **reales**: UN Comtrade (Ecuador y espejo) y Banco Central del Ecuador. "
        "Datos **SIMULADOS**: finca \"Acuícola Del Golfo S.A.\" (semilla fija 42).\n\n"
    )
    sections = [section_before_after(), section_rules(), section_imputation(),
                section_coverage(), "## 5. Pruebas automáticas\n\n" + TESTS_TEXT]
    return header + "\n".join(sections)


def run() -> Path:
    report = build_report()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("Reporte guardado en %s (%d líneas)", REPORT_PATH.relative_to(ROOT_DIR),
                report.count("\n"))
    return REPORT_PATH


# Ejecutar: python -m src.quality_report
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    path = run()
    print(f"\nListo: {path.relative_to(ROOT_DIR)}  (en VS Code: Ctrl + Shift + V para verlo)")