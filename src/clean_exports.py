"""
clean_exports.py — Limpieza bronze.comtrade_raw → silver.export_monthly.

Grano de silver.export_monthly: fuente × país × producto × mes.
  - Fuente 1 (UN_COMTRADE):     Ecuador reporta sus exportaciones (flow X). País = destino.
  - Fuente 5 (COMTRADE_MIRROR): otros países reportan importaciones desde Ecuador (flow M).
                                País = importador. Valores CIF (ADR-006).

weight_lb NO se envía: es una columna GENERADA por MySQL (weight_kg × 2,20462).

Cada regla de limpieza tiene nombre y cuenta las filas que afectó. El registro se guarda en
data/processed/quality_exports.csv (insumo del reporte de calidad de la Fase 6).

Nota sobre isAggregate (dentro de raw_payload): en el espejo hay filas con true y con false.
Indica si la ONU agregó la cifra desde datos más detallados del país; NO implica doble conteo
(se verificó que no hay filas repetidas por país–producto–mes), por eso no se filtra.

Ejecutar: python -m src.clean_exports
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from src.catalogs import SOURCE_IDS, normalize_alias
from src.config import ROOT_DIR
from src.db import read_sql, upsert

logger = logging.getLogger(__name__)

QUALITY_PATH = ROOT_DIR / "data" / "processed" / "quality_exports.csv"
WORLD_PARTNER = "0"                           # fila 'World' = total, no es un país
AGGREGATE_REPORTERS = {"97"}                  # 97 = Unión Europea (agregado de países)
FLOW_TO_SOURCE = {"X": SOURCE_IDS["UN_COMTRADE"], "M": SOURCE_IDS["COMTRADE_MIRROR"]}
GRAIN = ["source_id", "country_id", "product_id", "period_date"]

# ------------------------------------------------------------------ lectura de bronze
# CTE (WITH ... AS) = una "tabla temporal con nombre" que solo existe durante la consulta.
# ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) es una WINDOW FUNCTION:
#   - PARTITION BY agrupa las filas que representan el MISMO registro
#     (flujo + reportador + socio + producto + mes), sin colapsarlas como haría GROUP BY;
#   - ORDER BY extracted_at DESC ordena sus versiones de la más nueva a la más vieja;
#   - ROW_NUMBER() las numera 1, 2, 3...  → la versión vigente es la número 1.
BRONZE_SQL = """
WITH ranked AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY flow_code, reporter_code, partner_code, cmd_code, period
               ORDER BY extracted_at DESC, raw_id DESC
           ) AS version_rank
    FROM bronze.comtrade_raw AS r
    WHERE flow_code IN ('X', 'M')
)
SELECT raw_id, batch_id, extracted_at, flow_code,
       reporter_code, partner_code, partner_iso,
       JSON_UNQUOTE(JSON_EXTRACT(raw_payload, '$.reporterISO'))  AS reporter_iso,
       cmd_code, period, primary_value, net_wgt,
       JSON_UNQUOTE(JSON_EXTRACT(raw_payload, '$.partner2Code')) AS partner2,
       JSON_UNQUOTE(JSON_EXTRACT(raw_payload, '$.customsCode'))  AS customs,
       JSON_UNQUOTE(JSON_EXTRACT(raw_payload, '$.motCode'))      AS mot,
       version_rank
FROM ranked
"""


class RuleLog:
    """Registro de reglas: qué se hizo, a cuántas filas y con qué acción."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, rule: str, affected: int, action: str, detail: str = "") -> None:
        self.rows.append({"regla": rule, "filas_afectadas": int(affected),
                          "accion": action, "detalle": detail})
        logger.info("Regla %-24s | %6s filas | %s", rule, affected, action)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def _to_number(series: pd.Series) -> pd.Series:
    """Texto de bronze → número (vacío o texto inválido → NaN)."""
    return pd.to_numeric(series, errors="coerce")


# ------------------------------------------------------------------ verificación previa
def check_world_totals(df: pd.DataFrame, tolerance_pct: float = 0.5) -> pd.DataFrame:
    """
    En flow X, la suma por países debe igualar la fila 'World' de cada producto y mes.
    Si no coincide, el detalle por país estaría incompleto. Devuelve los meses que difieren.
    """
    x = df[(df["flow_code"] == "X") & (df["version_rank"] == 1)].copy()
    x["value"] = _to_number(x["primary_value"])
    is_world = x["partner_code"] == WORLD_PARTNER
    world = x[is_world].groupby(["period", "cmd_code"])["value"].sum()
    countries = x[~is_world].groupby(["period", "cmd_code"])["value"].sum()
    cmp = pd.concat({"world": world, "countries": countries}, axis=1).fillna(0)
    cmp["diff_pct"] = (cmp["countries"] - cmp["world"]) / cmp["world"].where(cmp["world"] != 0) * 100
    bad = cmp[cmp["diff_pct"].abs() > tolerance_pct]
    logger.info("Control World (X): %s combinaciones mes-producto, %s fuera de ±%.1f %%",
                len(cmp), len(bad), tolerance_pct)
    return bad


# ------------------------------------------------------------------ limpieza
def clean(raw: pd.DataFrame, log: RuleLog) -> pd.DataFrame:
    """Aplica las reglas en orden y devuelve filas listas para silver.export_monthly."""
    df = raw.copy()
    log.add("filas_en_bronze", len(df), "lectura", "flow X y M")

    # R1: solo la versión vigente de cada registro
    old = df["version_rank"] > 1
    log.add("version_vigente", old.sum(), "excluidas", "versiones antiguas (ROW_NUMBER > 1)")
    df = df[~old]

    # R2: la fila 'World' de Ecuador es un total, no un país
    world = (df["flow_code"] == "X") & (df["partner_code"] == WORLD_PARTNER)
    log.add("excluir_total_world", world.sum(), "excluidas", "partner_code 0 en flow X")
    df = df[~world]

    # R3: reportadores agregados en el espejo (Unión Europea = suma de sus países)
    agg = (df["flow_code"] == "M") & (df["reporter_code"].isin(AGGREGATE_REPORTERS))
    log.add("excluir_agregado_ue", agg.sum(), "excluidas", "reporter 97 en flow M")
    df = df[~agg]

    # R4: desgloses por socio secundario, régimen aduanero o transporte (no son totales)
    detail = ((df["partner2"].fillna("0") != "0") | (df["customs"].fillna("C00") != "C00")
              | (df["mot"].fillna("0") != "0"))
    log.add("excluir_desgloses", detail.sum(), "excluidas", "partner2≠0, customs≠C00 o mot≠0")
    df = df[~detail]

    # R5: valor en USD (texto → número); sin valor, cero o negativo no sirve.
    # Cero también se excluye (Fase 6): no aporta valor y daría USD/lb = 0 en gold.
    # Se evalúa ya redondeado a centavos: 0,004 USD se guardaría como 0,00.
    df = df.assign(value_usd=_to_number(df["primary_value"]))
    bad_value = df["value_usd"].isna() | (df["value_usd"].round(2) <= 0)
    log.add("valor_invalido", bad_value.sum(), "excluidas",
            "primary_value vacío, cero o negativo (en centavos)")
    df = df[~bad_value]

    # R6: peso neto en kg (texto → número); vacío se permite, negativo pasa a vacío
    df = df.assign(weight_kg=_to_number(df["net_wgt"]))
    neg = df["weight_kg"] < 0
    log.add("peso_negativo", neg.sum(), "a NULL", "net_wgt < 0")
    df.loc[neg, "weight_kg"] = None
    log.add("peso_vacio", df["weight_kg"].isna().sum(), "conservadas con NULL",
            "sin peso: cuentan en USD, no en USD/lb")

    # R7: país por ISO3 a través de los alias (X: destino; M: importador)
    aliases = read_sql("SELECT alias_text, country_id FROM silver.country_aliases")
    alias_map = dict(zip(aliases["alias_text"], aliases["country_id"]))
    iso = df["partner_iso"].where(df["flow_code"] == "X", df["reporter_iso"])
    df = df.assign(country_id=iso.map(normalize_alias).map(alias_map))
    no_country = df["country_id"].isna()
    log.add("pais_no_encontrado", no_country.sum(), "excluidas",
            ", ".join(sorted(iso[no_country].dropna().unique())))
    df = df[~no_country]

    # R8: producto por código HS
    products = read_sql("SELECT hs_code, product_id FROM silver.products")
    df = df.assign(product_id=df["cmd_code"].map(dict(zip(products["hs_code"],
                                                           products["product_id"]))))
    no_product = df["product_id"].isna()
    log.add("producto_no_encontrado", no_product.sum(), "excluidas",
            ", ".join(sorted(df.loc[no_product, "cmd_code"].unique())))
    df = df[~no_product]

    # R9: período 'AAAAMM' → fecha del primer día del mes (regla CHECK chk_export_first_day)
    df = df.assign(period_date=pd.to_datetime(df["period"].astype(str) + "01",
                                              format="%Y%m%d", errors="coerce").dt.date)
    bad_period = df["period_date"].isna()
    log.add("periodo_invalido", bad_period.sum(), "excluidas", "period no es AAAAMM")
    df = df[~bad_period]

    # R10: fuente y tipos finales (weight_lb lo calcula MySQL: columna generada)
    df = df.assign(
        source_id=df["flow_code"].map(FLOW_TO_SOURCE),
        value_usd=df["value_usd"].round(2),
        weight_kg=df["weight_kg"].round(3),
        country_id=df["country_id"].astype(int),
        product_id=df["product_id"].astype(int),
        source_batch_id=df["batch_id"],
        loaded_at=datetime.now(),
    )

    # Protección: el grano no puede repetirse (ej. dos códigos que llevan al mismo país)
    dup = df.duplicated(subset=GRAIN, keep=False)
    if dup.any():
        sample = df.loc[dup, GRAIN + ["reporter_code", "partner_code"]].head(10)
        raise ValueError(f"Grano repetido en {dup.sum()} filas; revisar:\n{sample}")

    log.add("filas_para_silver", len(df), "carga", "upsert por fuente-país-producto-mes")
    return df[GRAIN + ["value_usd", "weight_kg", "source_batch_id", "loaded_at"]]


def run() -> pd.DataFrame:
    """Lee bronze, controla, limpia, carga a silver y guarda el registro de reglas."""
    log = RuleLog()
    raw = read_sql(BRONZE_SQL)

    bad = check_world_totals(raw)
    if not bad.empty:
        logger.warning("Meses donde la suma por países ≠ World (X):\n%s", bad.head(10))

    clean_df = clean(raw, log)
    upsert(clean_df, "silver", "export_monthly", key_cols=GRAIN)

    rules = log.to_frame()
    QUALITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    rules.to_csv(QUALITY_PATH, index=False, encoding="utf-8")
    logger.info("Registro de reglas guardado en %s", QUALITY_PATH.relative_to(ROOT_DIR))
    return rules


# Ejecutar: python -m src.clean_exports
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    rules = run()

    print("\nReglas aplicadas:")
    print(rules.to_string(index=False))

    summary = read_sql(
        """
        SELECT s.source_code                       AS fuente,
               COUNT(*)                            AS filas,
               COUNT(DISTINCT e.country_id)        AS paises,
               COUNT(DISTINCT e.period_date)       AS meses,
               MIN(e.period_date)                  AS desde,
               MAX(e.period_date)                  AS hasta,
               ROUND(SUM(e.value_usd) / 1e6, 1)    AS usd_millones,
               ROUND(SUM(e.weight_lb) / 1e6, 1)    AS lb_millones
        FROM silver.export_monthly AS e
        JOIN silver.data_sources   AS s ON s.source_id = e.source_id
        GROUP BY s.source_code
        """
    )
    print("\nsilver.export_monthly por fuente (lb calculadas por MySQL):")
    print(summary.to_string(index=False))