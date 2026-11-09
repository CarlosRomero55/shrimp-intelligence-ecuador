"""
clean_farm.py — Limpieza de la finca simulada: bronze.farm_raw → tablas de silver.

Paso 5.6: farms y ponds.  (5.7: production_cycles y harvests · 5.8: cycle_costs y sales.)

Principio (ADR-002): silver guarda solo hechos MEDIDOS. Los ratios que trae bronze
(fcr, survival_pct, density_pl_m2) no se guardan; gold los recalcula. Los datos que
bronze trae "calculados" sirven para VERIFICAR la limpieza.

Política de nulos en columnas obligatorias (decisión del 2026-11-09): IMPUTAR y MARCAR.
Cada imputación queda contada en el registro de reglas y se compara con la verdad
(data/processed/farm_clean_truth.csv) para medir su error.

Ejecutar: python -m src.clean_farm
"""
from __future__ import annotations

import logging

import pandas as pd

from src.clean_exports import RuleLog
from src.config import ROOT_DIR
from src.db import read_sql, upsert
from src.simulate_farm import SOURCE_NAME, TRUTH_PATH

logger = logging.getLogger(__name__)

QUALITY_PATH = ROOT_DIR / "data" / "processed" / "quality_farm.csv"
FARM_CODE_PREFIX = "ADG"                      # Acuícola Del Golfo
VALID_PROVINCES = {"Guayas", "El Oro", "Santa Elena", "Esmeraldas", "Manabí"}  # chk_farms_province

# Lote vigente: la simulación se carga completa en cada lote, así que se usa SOLO el más
# reciente. DENSE_RANK() es otra window function: da el MISMO número a todas las filas que
# empatan (todas las filas de un lote tienen la misma fecha), a diferencia de ROW_NUMBER().
BRONZE_SQL = """
WITH ranked AS (
    SELECT r.*,
           DENSE_RANK() OVER (ORDER BY extracted_at DESC, batch_id DESC) AS batch_rank
    FROM bronze.farm_raw AS r
    WHERE source_name = :source
)
SELECT * FROM ranked WHERE batch_rank = 1
"""


# ------------------------------------------------------------------ utilidades
def parse_decimal(text) -> float | None:
    """'9,95' o '9.95' → 9.95 (la simulación no usa separador de miles). Inválido → None."""
    if text is None or pd.isna(text):
        return None
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        return None


def mode_or_first(series: pd.Series):
    """Valor más frecuente (si empatan, el primero en aparecer)."""
    counts = series.dropna().value_counts()
    return counts.index[0] if not counts.empty else None


def read_bronze_farm(log: RuleLog) -> pd.DataFrame:
    df = read_sql(BRONZE_SQL, {"source": SOURCE_NAME})
    if df.empty:
        raise ValueError("bronze.farm_raw no tiene filas de la simulación")
    log.add("finca_filas_lote_vigente", len(df), "lectura",
            f"lote {df['batch_id'].iloc[0]}")
    return df


# ------------------------------------------------------------------ farms
def clean_farms(raw: pd.DataFrame, log: RuleLog) -> pd.DataFrame:
    """Una fila por finca. Código = prefijo de la piscina (GY-P01 → ADG-GY)."""
    df = raw.assign(prefix=raw["pond_code"].str.split("-").str[0].str.strip().str.upper())
    farms = df.groupby("prefix").agg(
        farm_name=("farm_name", mode_or_first),
        province=("province", mode_or_first),
        names=("farm_name", "nunique"),
        provinces=("province", "nunique"),
    ).reset_index()

    inconsistent = farms[(farms["names"] > 1) | (farms["provinces"] > 1)]
    log.add("finca_nombre_consistente", len(inconsistent), "se usa el valor más frecuente",
            ", ".join(inconsistent["prefix"]))

    bad = ~farms["province"].isin(VALID_PROVINCES)
    log.add("finca_provincia_valida", bad.sum(), "excluidas",
            ", ".join(farms.loc[bad, "province"].astype(str)))
    farms = farms[~bad]

    farms["farm_code"] = FARM_CODE_PREFIX + "-" + farms["prefix"]
    log.add("fincas_para_silver", len(farms), "carga", "upsert por farm_code")
    return farms[["farm_code", "farm_name", "province"]]


# ------------------------------------------------------------------ ponds
def clean_ponds(raw: pd.DataFrame, log: RuleLog, farm_ids: dict[str, int]) -> pd.DataFrame:
    """Una fila por piscina, con su área limpia y consistente entre ciclos."""
    df = raw.assign(
        pond_code=raw["pond_code"].str.strip().str.upper(),
        area_ha=raw["pond_area_ha"].map(parse_decimal),
    )
    commas = raw["pond_area_ha"].fillna("").str.contains(",", regex=False)
    log.add("area_coma_decimal", commas.sum(), "corregidas", "'9,95' → 9.95")
    log.add("area_no_numerica", df["area_ha"].isna().sum(), "ignoradas en el cálculo",
            "texto no convertible")

    ponds = df.groupby("pond_code").agg(
        area_ha=("area_ha", mode_or_first),
        distinct_areas=("area_ha", "nunique"),
    ).reset_index()
    inconsistent = ponds["distinct_areas"] > 1
    log.add("area_consistente", inconsistent.sum(), "se usa el valor más frecuente",
            ", ".join(ponds.loc[inconsistent, "pond_code"]))

    out_of_range = ponds["area_ha"].isna() | (ponds["area_ha"] <= 0) | (ponds["area_ha"] > 50)
    log.add("area_fuera_de_rango", out_of_range.sum(), "excluidas", "regla chk_ponds_area")
    ponds = ponds[~out_of_range]

    ponds["farm_code"] = FARM_CODE_PREFIX + "-" + ponds["pond_code"].str.split("-").str[0]
    ponds["farm_id"] = ponds["farm_code"].map(farm_ids)
    no_farm = ponds["farm_id"].isna()
    log.add("piscina_sin_finca", no_farm.sum(), "excluidas", "prefijo sin finca en silver")
    ponds = ponds[~no_farm]

    ponds["farm_id"] = ponds["farm_id"].astype(int)
    ponds["area_ha"] = ponds["area_ha"].round(2)
    log.add("piscinas_para_silver", len(ponds), "carga", "upsert por finca + código")
    return ponds[["farm_id", "pond_code", "area_ha"]]


# ------------------------------------------------------------------ verificación
def verify_ponds_against_truth() -> tuple[int, int]:
    """Compara las áreas de silver con la verdad de la simulación (antes de ensuciar)."""
    truth = (pd.read_csv(TRUTH_PATH, usecols=["pond_code", "pond_area_ha"])
             .drop_duplicates("pond_code"))
    silver = read_sql(
        "SELECT p.pond_code, p.area_ha FROM silver.ponds AS p "
        "JOIN silver.farms AS f ON f.farm_id = p.farm_id "
        "WHERE f.farm_code LIKE :prefix",
        {"prefix": f"{FARM_CODE_PREFIX}-%"},
    )
    merged = truth.merge(silver, on="pond_code", how="left")
    ok = (merged["area_ha"].astype(float) - merged["pond_area_ha"]).abs() < 0.005
    return int(ok.sum()), len(truth)


# ------------------------------------------------------------------ orquestación
def run() -> pd.DataFrame:
    log = RuleLog()
    raw = read_bronze_farm(log)

    farms = clean_farms(raw, log)
    upsert(farms, "silver", "farms", key_cols=["farm_code"])
    farm_ids = read_sql("SELECT farm_id, farm_code FROM silver.farms")
    farm_ids = dict(zip(farm_ids["farm_code"], farm_ids["farm_id"]))

    ponds = clean_ponds(raw, log, farm_ids)
    upsert(ponds, "silver", "ponds", key_cols=["farm_id", "pond_code"])

    rules = log.to_frame()
    QUALITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    rules.to_csv(QUALITY_PATH, index=False, encoding="utf-8")
    logger.info("Registro de reglas guardado en %s", QUALITY_PATH.relative_to(ROOT_DIR))
    return rules


# Ejecutar: python -m src.clean_farm
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    rules = run()
    print("\nReglas aplicadas:")
    print(rules.to_string(index=False))

    summary = read_sql(
        """
        SELECT f.farm_code, f.farm_name, f.province,
               COUNT(p.pond_id)       AS piscinas,
               ROUND(SUM(p.area_ha), 2) AS hectareas
        FROM silver.farms AS f
        LEFT JOIN silver.ponds AS p ON p.farm_id = f.farm_id
        GROUP BY f.farm_code, f.farm_name, f.province
        ORDER BY f.farm_code
        """
    )
    print("\nFincas y piscinas en silver:")
    print(summary.to_string(index=False))

    ok, total = verify_ponds_against_truth()
    print(f"\nVerificación contra la verdad: {ok}/{total} áreas correctas")