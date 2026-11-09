"""
reconcile.py — Conciliación de fuentes → silver.source_reconciliation.

Nivel 'total' (Paso 5.5): por cada mes, el total nacional de cada fuente de detalle
(Comtrade X = fuente 1, espejo = fuente 5) frente al total OFICIAL del BCE (fuente 3,
serie 'Camarón' del cuadro 3.1.2). MySQL calcula la brecha (gap_usd, gap_pct: columnas
generadas). Los meses en que una fuente NO publicó se guardan con value_usd vacío, para
que el hueco quede documentado en la propia tabla de conciliación.

Por qué no se "elige" una fuente y se borra la otra: cada una mide el comercio desde un
lugar distinto (aduana de Ecuador, organismo internacional, aduana del país comprador).
Las diferencias tienen causas explicables y son información (ver ADR-006).

El BCE NO va a export_monthly: es un total sin país (ponerlo como un "país" haría que el
total se sumara dos veces).

Nivel 'country' (por país, Comtrade X vs espejo): se agrega en un paso posterior.

Ejecutar: python -m src.reconcile
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd

from src.catalogs import SOURCE_IDS
from src.clean_exports import RuleLog
from src.config import ROOT_DIR
from src.db import read_sql, upsert

logger = logging.getLogger(__name__)

QUALITY_PATH = ROOT_DIR / "data" / "processed" / "quality_reconciliation.csv"
BCE_TABLE = "3.1.2"
BCE_SERIES = "Camarón"
BCE_UNIT_FACTOR = 1000                      # el cuadro está en MILES de USD
REFERENCE = SOURCE_IDS["BCE"]               # 3
COMPARED = {                                # fuente comparada → causa probable de la brecha
    SOURCE_IDS["UN_COMTRADE"]:
        "Cobertura de producto (BCE 'Camarón' vs HS 030617+160521+160529) y fecha de "
        "registro; misma valoración FOB.",
    SOURCE_IDS["COMTRADE_MIRROR"]:
        "Valoración CIF (flete y seguro) vs FOB, desfase por fecha de llegada al destino y "
        "países importadores que no reportan el mes.",
}
NOT_PUBLISHED = "La fuente no publicó datos de Ecuador para este mes (ver ADR-006)."
MONTHS_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
             "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

# Versión vigente de cada mes (el BCE revisa cifras): misma técnica que en clean_exports
BCE_SQL = """
WITH ranked AS (
    SELECT raw_id, batch_id, period_label, value_raw,
           ROW_NUMBER() OVER (
               PARTITION BY table_code, series_name, period_label
               ORDER BY extracted_at DESC, raw_id DESC
           ) AS version_rank
    FROM bronze.bce_raw
    WHERE table_code = :table AND series_name = :series
)
SELECT raw_id, batch_id, period_label, value_raw, version_rank
FROM ranked
"""

# Total nacional mensual de cada fuente de detalle (suma de todos los países y productos)
DETAIL_SQL = """
SELECT source_id, period_date,
       SUM(value_usd) AS value_usd,
       SUM(weight_kg) AS weight_kg
FROM silver.export_monthly
GROUP BY source_id, period_date
"""


def parse_ec_number(text) -> float | None:
    """Formato ecuatoriano → número: '596.248,67089' → 596248.67089 (vacío/inválido → None)."""
    if text is None or pd.isna(text):
        return None
    try:
        return float(str(text).strip().replace(".", "").replace(",", "."))
    except ValueError:
        return None


def parse_month_es(label) -> date | None:
    """'Ene 2025' → 2025-01-01 (mes en español abreviado; inválido → None)."""
    parts = str(label).strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    month = MONTHS_ES.get(parts[0][:3].lower())
    return date(int(parts[1]), month, 1) if month else None


def load_bce_total(log: RuleLog) -> pd.DataFrame:
    """Serie mensual del BCE en USD (no en miles), versión vigente."""
    df = read_sql(BCE_SQL, {"table": BCE_TABLE, "series": BCE_SERIES})
    log.add("bce_filas_en_bronze", len(df), "lectura", f"cuadro {BCE_TABLE}, serie {BCE_SERIES}")
    if df.empty:
        raise ValueError(f"No hay datos de la serie '{BCE_SERIES}' en bronze.bce_raw")

    old = df["version_rank"] > 1
    log.add("bce_version_vigente", old.sum(), "excluidas", "versiones antiguas (ROW_NUMBER > 1)")
    df = df[~old]

    df = df.assign(reference_value_usd=df["value_raw"].map(parse_ec_number) * BCE_UNIT_FACTOR,
                   period_date=df["period_label"].map(parse_month_es))
    bad_value = df["reference_value_usd"].isna()
    log.add("bce_valor_invalido", bad_value.sum(), "excluidas", "texto no numérico")
    bad_month = df["period_date"].isna()
    log.add("bce_mes_invalido", bad_month.sum(), "excluidas",
            ", ".join(df.loc[bad_month, "period_label"].astype(str)))
    df = df[~bad_value & ~bad_month]
    return df[["period_date", "reference_value_usd"]]


def build_total_level(log: RuleLog) -> pd.DataFrame:
    """Una fila por mes del BCE y por fuente comparada (con valor vacío si no publicó)."""
    bce = load_bce_total(log)
    detail = read_sql(DETAIL_SQL)
    detail["period_date"] = pd.to_datetime(detail["period_date"]).dt.date

    no_ref = ~detail["period_date"].isin(set(bce["period_date"]))
    log.add("sin_referencia_bce", no_ref.sum(), "excluidas",
            "meses de Comtrade aún no publicados por el BCE")

    frames = []
    for source_id, cause in COMPARED.items():
        d = detail[detail["source_id"] == source_id]
        merged = bce.merge(d[["period_date", "value_usd", "weight_kg"]],
                           on="period_date", how="left")
        missing = merged["value_usd"].isna()
        code = next(c for c, i in SOURCE_IDS.items() if i == source_id).lower()
        log.add(f"sin_dato_{code}", missing.sum(), "conservadas con NULL",
                "mes del BCE sin dato en la fuente comparada")
        merged["probable_cause"] = cause
        merged.loc[missing, "probable_cause"] = NOT_PUBLISHED
        merged["source_id"] = source_id
        frames.append(merged)

    out = pd.concat(frames, ignore_index=True)
    out = out.assign(
        comparison_level="total",
        country_id=None,                      # nivel total: sin país (regla chk_recon_country)
        reference_source_id=REFERENCE,
        reference_weight_kg=None,             # el cuadro del BCE no trae peso
        value_usd=out["value_usd"].round(2),
        reference_value_usd=out["reference_value_usd"].round(2),
        weight_kg=out["weight_kg"].round(3),
        computed_at=datetime.now(),
    )
    log.add("filas_conciliacion_total", len(out), "carga", "upsert por mes-nivel-país-fuente")
    return out[["period_date", "comparison_level", "country_id", "source_id",
                "reference_source_id", "value_usd", "reference_value_usd", "weight_kg",
                "reference_weight_kg", "probable_cause", "computed_at"]]


def run() -> pd.DataFrame:
    log = RuleLog()
    total = build_total_level(log)
    # country_key, gap_usd y gap_pct NO se envían: son columnas generadas por MySQL.
    # La llave única es (period_date, comparison_level, country_key, source_id).
    upsert(total, "silver", "source_reconciliation",
           key_cols=["period_date", "comparison_level", "source_id"])

    rules = log.to_frame()
    QUALITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    rules.to_csv(QUALITY_PATH, index=False, encoding="utf-8")
    logger.info("Registro de reglas guardado en %s", QUALITY_PATH.relative_to(ROOT_DIR))
    return rules


# Ejecutar: python -m src.reconcile
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    rules = run()
    print("\nReglas aplicadas:")
    print(rules.to_string(index=False))

    # Brecha ANUAL: se suman los USD y luego se calcula el %, nunca se promedian porcentajes
    yearly = read_sql(
        """
        SELECT s.source_code                                   AS fuente,
               YEAR(r.period_date)                             AS anio,
               COUNT(r.value_usd)                              AS meses_con_dato,
               COUNT(*) - COUNT(r.value_usd)                   AS meses_sin_dato,
               ROUND(SUM(r.value_usd) / 1e6, 1)                AS fuente_musd,
               ROUND(SUM(CASE WHEN r.value_usd IS NOT NULL
                              THEN r.reference_value_usd END) / 1e6, 1) AS bce_musd,
               ROUND((SUM(r.value_usd)
                      - SUM(CASE WHEN r.value_usd IS NOT NULL
                                 THEN r.reference_value_usd END))
                     / SUM(CASE WHEN r.value_usd IS NOT NULL
                                THEN r.reference_value_usd END) * 100, 1) AS brecha_pct
        FROM silver.source_reconciliation AS r
        JOIN silver.data_sources AS s ON s.source_id = r.source_id
        WHERE r.comparison_level = 'total'
        GROUP BY s.source_code, YEAR(r.period_date)
        ORDER BY s.source_code, anio
        """
    )
    print("\nBrecha anual frente al BCE (solo meses con dato en ambas fuentes):")
    print(yearly.to_string(index=False))