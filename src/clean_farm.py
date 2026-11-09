"""
clean_farm.py — Limpieza de la finca simulada: bronze.farm_raw → tablas de silver.

Paso 5.6: farms y ponds · 5.7: production_cycles y harvests · 5.8: cycle_costs y sales.

Principio (ADR-002): silver guarda solo hechos MEDIDOS. Los ratios que trae bronze
(fcr, survival_pct, density_pl_m2) no se guardan; gold los recalcula. Los datos que
bronze trae "calculados" (days_of_culture, fcr) sirven para VERIFICAR la limpieza.

Política de nulos en columnas obligatorias (decisión del 2026-11-09): IMPUTAR y MARCAR.
Cada imputación queda contada en el registro de reglas y se compara con la verdad
(data/processed/farm_clean_truth.csv) para medir su error.

Ejecutar: python -m src.clean_farm
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import numpy as np
import pandas as pd

from src.catalogs import normalize_alias, normalize_size
from src.clean_exports import RuleLog
from src.config import ROOT_DIR, project
from src.db import read_sql, upsert
from src.simulate_farm import CONTENT_COLS, SOURCE_NAME, TRUTH_PATH

logger = logging.getLogger(__name__)

QUALITY_PATH = ROOT_DIR / "data" / "processed" / "quality_farm.csv"
VERIFY_PATH = ROOT_DIR / "data" / "processed" / "farm_verification.csv"
FARM_CODE_PREFIX = "ADG"                      # Acuícola Del Golfo
VALID_PROVINCES = {"Guayas", "El Oro", "Santa Elena", "Esmeraldas", "Manabí"}  # chk_farms_province
LB_PER_KG = project.kg_to_lb                  # 2.20462
# Formatos de fecha aceptados. Regla del proyecto: el DÍA va antes que el MES.
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"]
FCR_TOLERANCE = 0.15                          # 15 %: FCR reportado vs alimento ÷ cosecha
MAX_WEIGHT_G = 60                             # regla chk_harvests_weight
# Columna de bronze → cost_type de silver (regla CHECK chk_costs_type)
COST_COLUMNS = {"cost_larvae_usd": "larvae", "cost_feed_usd": "feed",
                "cost_energy_usd": "energy", "cost_labor_usd": "labor"}

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


def parse_date(text) -> tuple[date | None, str | None]:
    """Prueba los formatos aceptados; devuelve (fecha, formato usado) o (None, None)."""
    if text is None or pd.isna(text):
        return None, None
    value = str(text).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date(), fmt
        except ValueError:
            continue
    return None, None


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


# ------------------------------------------------------------------ 5.6 farms
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


# ------------------------------------------------------------------ 5.6 ponds
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


# ------------------------------------------------------------------ 5.7 ciclos y cosechas
def clean_cycles(raw: pd.DataFrame, log: RuleLog, ponds: pd.DataFrame,
                 sizes: pd.DataFrame) -> pd.DataFrame:
    """
    Una fila por ciclo, con todo lo necesario para production_cycles y harvests
    (y, en el paso 5.8, para costos y ventas). Columnas *_imputed marcan valores estimados.
    """
    df = raw.copy()

    # --- Duplicados: primero copias exactas, luego códigos de ciclo repetidos con datos distintos
    exact = df.duplicated(subset=CONTENT_COLS, keep="first")
    log.add("duplicado_exacto", exact.sum(), "excluidas", "filas idénticas en todo su contenido")
    df = df[~exact]
    df["cycle_code"] = df["cycle_code"].str.strip().str.upper()
    conflict = df.duplicated(subset="cycle_code", keep="first")
    log.add("ciclo_repetido_con_cambios", conflict.sum(), "se conserva la primera",
            ", ".join(df.loc[conflict, "cycle_code"]))
    df = df[~conflict]

    # --- Fechas en varios formatos (el día siempre antes que el mes)
    for col, new in [("stocking_date", "stocking"), ("harvest_date", "harvest")]:
        parsed = df[col].map(parse_date)
        df[new] = parsed.str[0]
        fmt = parsed.str[1]
        log.add(f"fecha_formato_{new}", (fmt.notna() & (fmt != DATE_FORMATS[0])).sum(),
                "convertidas", "formato distinto de AAAA-MM-DD")
    bad_date = df["stocking"].isna() | df["harvest"].isna()
    log.add("fecha_invalida", bad_date.sum(), "excluidas", "no coincide con ningún formato")
    df = df[~bad_date]
    bad_order = df["harvest"] <= df["stocking"]
    log.add("fecha_cosecha_antes_siembra", bad_order.sum(), "excluidas", "regla chk_cycles_dates")
    df = df[~bad_order]

    # --- Verificación: días calculados desde las fechas limpias vs. lo que reporta bronze
    computed_days = (pd.to_datetime(df["harvest"]) - pd.to_datetime(df["stocking"])).dt.days
    reported_days = pd.to_numeric(df["days_of_culture"], errors="coerce")
    mismatch = reported_days.notna() & (computed_days != reported_days)
    log.add("validar_dias_cultivo", mismatch.sum(), "solo reporte",
            "días desde fechas limpias ≠ days_of_culture de bronze")

    # --- Piscina
    df["pond_code"] = df["pond_code"].str.strip().str.upper()
    df = df.merge(ponds[["pond_code", "pond_id", "area_ha"]], on="pond_code", how="left")
    no_pond = df["pond_id"].isna()
    log.add("ciclo_sin_piscina", no_pond.sum(), "excluidas", "piscina no cargada en silver")
    df = df[~no_pond]

    # --- Cosecha: todo a kg (las libras las genera MySQL)
    qty = df["harvested_qty"].map(parse_decimal)
    unit = df["harvested_unit"].fillna("").str.strip().str.lower()
    df["harvested_kg"] = np.where(unit == "lb", qty / LB_PER_KG,
                                  np.where(unit == "kg", qty, np.nan))
    log.add("unidad_kg", (unit == "kg").sum(), "se conservan en kg", "las demás: lb ÷ 2,20462")
    bad_qty = df["harvested_kg"].isna() | (df["harvested_kg"] <= 0)
    log.add("cosecha_invalida", bad_qty.sum(), "excluidas", "cantidad o unidad no válida")
    df = df[~bad_qty]

    # --- Talla
    df["size_code"] = df["size_grade"].map(normalize_size)
    changed = df["size_code"].notna() & (df["size_code"] != df["size_grade"])
    log.add("talla_formato", changed.sum(), "normalizadas", "'20-30' / '20 30' → '20/30'")
    df = df.merge(sizes, on="size_code", how="left")
    no_size = df["size_id"].isna()
    log.add("talla_desconocida", no_size.sum(), "excluidas", "talla fuera del catálogo")
    df = df[~no_size]

    # --- Números con coma decimal y campos de apoyo (no se guardan en silver)
    for col in ["avg_weight_g", "density_pl_m2", "survival_pct", "feed_kg", "fcr"]:
        commas = df[col].fillna("").astype(str).str.contains(",", regex=False)
        if commas.any():
            log.add(f"coma_decimal_{col}", commas.sum(), "corregidas", "',' → '.'")
        df[col + "_num"] = df[col].map(parse_decimal)

    # --- Larvas sembradas (dato obligatorio): medido = densidad × área × 10.000
    df["larvae"] = df["density_pl_m2_num"] * df["area_ha"] * 10_000
    df["larvae_imputed"] = df["larvae"].isna()
    # Imputación 1: despejar de la cosecha → piezas ÷ supervivencia
    pieces = df["harvested_kg"] * 1000 / df["avg_weight_g_num"]
    by_harvest = df["larvae"].isna() & pieces.notna() & df["survival_pct_num"].gt(0)
    df.loc[by_harvest, "larvae"] = pieces[by_harvest] / (df.loc[by_harvest, "survival_pct_num"] / 100)
    log.add("larvas_imputadas_por_cosecha", by_harvest.sum(), "imputadas",
            "densidad nula: piezas cosechadas ÷ supervivencia")
    # Imputación 2: densidad mediana de la finca
    df["farm_prefix"] = df["pond_code"].str.split("-").str[0]
    median_density = df.groupby("farm_prefix")["density_pl_m2_num"].transform("median")
    by_median = df["larvae"].isna()
    df.loc[by_median, "larvae"] = median_density[by_median] * df.loc[by_median, "area_ha"] * 10_000
    log.add("larvas_imputadas_por_mediana", by_median.sum(), "imputadas",
            "densidad y supervivencia nulas: densidad mediana de la finca")

    # --- Peso promedio (dato obligatorio)
    df["avg_weight"] = df["avg_weight_g_num"]
    df["weight_imputed"] = df["avg_weight"].isna()
    measured_larvae = ~df["larvae_imputed"]
    by_biomass = df["avg_weight"].isna() & measured_larvae & df["survival_pct_num"].gt(0)
    df.loc[by_biomass, "avg_weight"] = (
        df.loc[by_biomass, "harvested_kg"] * 1000
        / (df.loc[by_biomass, "larvae"] * df.loc[by_biomass, "survival_pct_num"] / 100))
    log.add("peso_imputado_por_biomasa", by_biomass.sum(), "imputados",
            "kg ÷ (larvas × supervivencia)")
    by_size = df["avg_weight"].isna()
    midpoint = 1000 / ((df["min_count"] + df["max_count"]) / 2)      # g por pieza
    df.loc[by_size, "avg_weight"] = midpoint[by_size]
    log.add("peso_imputado_por_talla", by_size.sum(), "imputados",
            "punto medio de la talla (piezas por kg)")
    bad_weight = (df["avg_weight"] <= 0) | (df["avg_weight"] > MAX_WEIGHT_G)
    log.add("peso_fuera_de_rango", bad_weight.sum(), "excluidas", "regla chk_harvests_weight")
    df = df[~bad_weight]

    # --- Alimento (opcional en silver): negativo → vacío
    neg_feed = df["feed_kg_num"] < 0
    df.loc[neg_feed, "feed_kg_num"] = np.nan
    log.add("alimento_negativo", neg_feed.sum(), "a NULL", "feed_kg < 0")

    # --- Verificación: FCR reportado vs alimento ÷ cosecha (el FCR NO se guarda)
    computed_fcr = df["feed_kg_num"] / df["harvested_kg"]
    fcr_bad = (df["fcr_num"].notna() & computed_fcr.notna()
               & ((df["fcr_num"] - computed_fcr).abs() > FCR_TOLERANCE * computed_fcr))
    log.add("fcr_reportado_inconsistente", fcr_bad.sum(), "solo reporte (no se guarda FCR)",
            ", ".join(df.loc[fcr_bad, "cycle_code"]))

    log.add("ciclos_limpios", len(df), "carga", "production_cycles + harvests")
    return df.reset_index(drop=True)


def to_cycles_table(clean: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "pond_id": clean["pond_id"].astype(int),
        "cycle_code": clean["cycle_code"],
        "stocking_date": clean["stocking"],
        "final_harvest_date": clean["harvest"],
        "larvae_stocked": clean["larvae"].round().astype(int),
        "feed_kg": clean["feed_kg_num"].round(2),
    })


def to_harvests_table(clean: pd.DataFrame, cycle_ids: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame({
        "cycle_id": clean["cycle_code"].map(cycle_ids).astype(int),
        "harvest_date": clean["harvest"],
        "harvest_type": "final",
        "harvested_kg": clean["harvested_kg"].round(2),
        "avg_weight_g": clean["avg_weight"].round(2),
        "size_id": clean["size_id"].astype(int),
    })


# ------------------------------------------------------------------ 5.8 costos
def clean_costs(clean: pd.DataFrame, log: RuleLog, cycle_ids: dict[str, int]) -> pd.DataFrame:
    """
    Formato largo: 4 filas por ciclo (larvae, feed, energy, labor).
    Costo nulo o negativo → IMPUTADO con la mediana de USD por hectárea-día del mismo tipo
    de costo en la misma finca × área × días del ciclo (y marcado).
    """
    base = clean.assign(
        cycle_id=clean["cycle_code"].map(cycle_ids),
        ha_days=clean["area_ha"] * (pd.to_datetime(clean["harvest"])
                                    - pd.to_datetime(clean["stocking"])).dt.days,
    )
    frames = []
    for col, cost_type in COST_COLUMNS.items():
        commas = base[col].fillna("").astype(str).str.contains(",", regex=False)
        if commas.any():
            log.add(f"coma_decimal_{col}", commas.sum(), "corregidas", "',' → '.'")
        amount = base[col].map(parse_decimal)
        negative = amount < 0
        log.add(f"costo_negativo_{cost_type}", negative.sum(), "se imputan", "amount < 0")
        amount = amount.mask(negative)

        rate = amount / base["ha_days"]                        # USD por hectárea-día
        median_rate = rate.groupby(base["farm_prefix"]).transform("median")
        missing = amount.isna()
        amount = amount.where(~missing, median_rate * base["ha_days"])
        log.add(f"costo_imputado_{cost_type}", missing.sum(), "imputados",
                "mediana USD/ha-día de la finca × área × días")

        frames.append(pd.DataFrame({
            "cycle_code": base["cycle_code"], "cycle_id": base["cycle_id"],
            "cost_type": cost_type, "amount_usd": amount.round(2), "imputed": missing,
        }))
    costs = pd.concat(frames, ignore_index=True)
    still_missing = costs["amount_usd"].isna() | costs["cycle_id"].isna()
    log.add("costo_sin_valor", still_missing.sum(), "excluidas", "no se pudo imputar")
    costs = costs[~still_missing]
    costs["cycle_id"] = costs["cycle_id"].astype(int)
    log.add("costos_para_silver", len(costs), "carga", "4 tipos por ciclo")
    return costs


# ------------------------------------------------------------------ 5.8 ventas
def clean_sales(clean: pd.DataFrame, log: RuleLog, harvest_ids: dict[str, int],
                alias_map: dict[str, int], names_es: set[str]) -> pd.DataFrame:
    """
    Una venta por cosecha: se vende toda la cosecha al precio y destino del ciclo.
    sold_lb replica la misma conversión que la columna generada harvested_lb.
    revenue_usd NO se envía: columna generada (sold_lb × price_usd_lb).
    """
    df = clean.assign(harvest_id=clean["cycle_code"].map(harvest_ids))

    commas = df["sale_price_usd_lb"].fillna("").astype(str).str.contains(",", regex=False)
    log.add("coma_decimal_precio", commas.sum(), "corregidas", "',' → '.'")
    df["price"] = df["sale_price_usd_lb"].map(parse_decimal)
    bad_price = df["price"].isna() | (df["price"] <= 0)
    log.add("precio_invalido", bad_price.sum(), "excluidas", "regla chk_sales_price")
    df = df[~bad_price]

    # País de destino: cualquier variante → alias normalizado → country_id
    raw_market = df["destination_market"]
    df["country_id"] = raw_market.map(normalize_alias).map(alias_map)
    is_null = raw_market.isna() | (raw_market.astype(str).str.strip() == "")
    variant = ~is_null & df["country_id"].notna() & ~raw_market.isin(names_es)
    not_found = ~is_null & df["country_id"].isna()
    log.add("pais_variante_resuelta", variant.sum(), "resueltas por alias",
            ", ".join(sorted(raw_market[variant].astype(str).unique())))
    log.add("pais_nulo", is_null.sum(), "a NULL", "destino desconocido (columna opcional)")
    log.add("pais_no_encontrado", not_found.sum(), "a NULL",
            ", ".join(sorted(raw_market[not_found].astype(str).unique())))

    no_harvest = df["harvest_id"].isna()
    log.add("venta_sin_cosecha", no_harvest.sum(), "excluidas", "cosecha no cargada en silver")
    df = df[~no_harvest]

    sales = pd.DataFrame({
        "harvest_id": df["harvest_id"].astype(int),
        "sale_date": df["harvest"],
        "sold_lb": (df["harvested_kg"].round(2) * LB_PER_KG).round(2),
        "price_usd_lb": df["price"].round(4),
        "destination_country_id": df["country_id"].astype("Int64"),
    })
    log.add("ventas_para_silver", len(sales), "carga", "upsert por harvest_id (una por cosecha)")
    return sales.assign(cycle_code=df["cycle_code"].values)


# ------------------------------------------------------------------ verificación vs. verdad
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


def verify_cycles_against_truth(clean: pd.DataFrame) -> pd.DataFrame:
    """Exactitud de la limpieza de ciclos y cosechas frente a farm_clean_truth.csv."""
    truth = pd.read_csv(TRUTH_PATH)
    t = truth.assign(
        t_stocking=pd.to_datetime(truth["stocking_date"]).dt.date,
        t_harvest=pd.to_datetime(truth["harvest_date"]).dt.date,
        t_kg=truth["harvested_qty"] / LB_PER_KG,
        t_larvae=truth["density_pl_m2"] * truth["pond_area_ha"] * 10_000,
        t_weight=truth["avg_weight_g"],
        t_feed=truth["feed_kg"],
    )[["cycle_code", "t_stocking", "t_harvest", "t_kg", "t_larvae", "t_weight", "t_feed"]]
    m = t.merge(clean, on="cycle_code", how="left")
    found = m["harvest"].notna()

    def pct_ok(mask: pd.Series, ok: pd.Series) -> str:
        n = int(mask.sum())
        return f"{int((ok & mask).sum())}/{n}" if n else "0/0"

    def mean_err(mask: pd.Series, est: pd.Series, real: pd.Series) -> str:
        n = int(mask.sum())
        return f"{((est - real).abs() / real)[mask].mean() * 100:.1f} %" if n else "—"

    rel = lambda a, b, tol: ((a - b).abs() / b) <= tol             # noqa: E731
    measured_l = found & ~m["larvae_imputed"].fillna(True).astype(bool)
    imputed_l = found & m["larvae_imputed"].fillna(False).astype(bool)
    measured_w = found & ~m["weight_imputed"].fillna(True).astype(bool)
    imputed_w = found & m["weight_imputed"].fillna(False).astype(bool)
    has_feed = found & m["t_feed"].notna() & m["feed_kg_num"].notna()

    rows = [
        ("ciclos en silver", f"{int(found.sum())}/{len(t)}", "deben ser 510"),
        ("fecha de siembra exacta", pct_ok(found, m["stocking"] == m["t_stocking"]), ""),
        ("fecha de cosecha exacta", pct_ok(found, m["harvest"] == m["t_harvest"]), ""),
        ("kg cosechados (±0,5 %)", pct_ok(found, rel(m["harvested_kg"], m["t_kg"], 0.005)), ""),
        ("larvas medidas (±0,1 %)", pct_ok(measured_l, rel(m["larvae"], m["t_larvae"], 0.001)), ""),
        ("larvas imputadas: error medio", mean_err(imputed_l, m["larvae"], m["t_larvae"]),
         f"{int(imputed_l.sum())} imputadas"),
        ("peso medido exacto", pct_ok(measured_w, (m["avg_weight"] - m["t_weight"]).abs() < 0.05), ""),
        ("peso imputado: error medio", mean_err(imputed_w, m["avg_weight"], m["t_weight"]),
         f"{int(imputed_w.sum())} imputados"),
        ("alimento exacto", pct_ok(has_feed, (m["feed_kg_num"] - m["t_feed"]).abs() < 0.5), ""),
    ]
    return pd.DataFrame(rows, columns=["control", "resultado", "nota"])


def verify_costs_sales_against_truth(costs: pd.DataFrame, sales: pd.DataFrame,
                                     iso_by_country: dict[int, str]) -> pd.DataFrame:
    """Exactitud de costos y ventas frente a farm_clean_truth.csv."""
    truth = pd.read_csv(TRUTH_PATH)
    rows = []

    long_truth = truth.melt(id_vars="cycle_code", value_vars=list(COST_COLUMNS),
                            var_name="col", value_name="t_amount")
    long_truth["cost_type"] = long_truth["col"].map(COST_COLUMNS)
    m = long_truth.merge(costs, on=["cycle_code", "cost_type"], how="left")
    measured = m["imputed"].eq(False)
    imputed = m["imputed"].eq(True)
    exact = (m["amount_usd"] - m["t_amount"]).abs() < 0.01
    rows.append(("costos en silver", f"{int(m['amount_usd'].notna().sum())}/{len(m)}",
                 "4 por ciclo"))
    rows.append(("costos medidos exactos", f"{int((exact & measured).sum())}/{int(measured.sum())}", ""))
    err = ((m["amount_usd"] - m["t_amount"]).abs() / m["t_amount"])[imputed]
    rows.append(("costos imputados: error medio",
                 f"{err.mean() * 100:.1f} %" if len(err) else "—",
                 f"{int(imputed.sum())} imputados"))

    s = truth[["cycle_code", "sale_price_usd_lb", "destination_market"]].merge(
        sales, on="cycle_code", how="left")
    rows.append(("ventas en silver", f"{int(s['harvest_id'].notna().sum())}/{len(s)}", ""))
    price_ok = (s["price_usd_lb"] - s["sale_price_usd_lb"]).abs() < 0.0001
    rows.append(("precio exacto", f"{int(price_ok.sum())}/{len(s)}", ""))

    # La verdad usa nombres en español sin variantes ('Estados Unidos', 'China', ...)
    truth_iso = {"China": "CHN", "Estados Unidos": "USA", "España": "ESP", "Francia": "FRA",
                 "Italia": "ITA", "Vietnam": "VNM", "Rusia": "RUS"}
    s["t_iso"] = s["destination_market"].map(truth_iso)
    s["iso"] = s["destination_country_id"].map(iso_by_country)
    known = s["iso"].notna()
    rows.append(("país correcto (destino conocido)",
                 f"{int((s['iso'] == s['t_iso'])[known].sum())}/{int(known.sum())}",
                 f"{int((~known).sum())} con destino nulo en bronze"))
    return pd.DataFrame(rows, columns=["control", "resultado", "nota"])


# ------------------------------------------------------------------ orquestación
def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    log = RuleLog()
    raw = read_bronze_farm(log)

    # 5.6 fincas y piscinas
    farms = clean_farms(raw, log)
    upsert(farms, "silver", "farms", key_cols=["farm_code"])
    farm_ids = read_sql("SELECT farm_id, farm_code FROM silver.farms")
    farm_ids = dict(zip(farm_ids["farm_code"], farm_ids["farm_id"]))

    ponds = clean_ponds(raw, log, farm_ids)
    upsert(ponds, "silver", "ponds", key_cols=["farm_id", "pond_code"])

    # 5.7 ciclos y cosechas
    pond_map = read_sql(
        "SELECT p.pond_id, p.pond_code, p.area_ha FROM silver.ponds AS p "
        "JOIN silver.farms AS f ON f.farm_id = p.farm_id WHERE f.farm_code LIKE :prefix",
        {"prefix": f"{FARM_CODE_PREFIX}-%"},
    )
    pond_map["area_ha"] = pond_map["area_ha"].astype(float)
    sizes = read_sql("SELECT size_id, size_code, min_count, max_count FROM silver.sizes")

    clean = clean_cycles(raw, log, pond_map, sizes)
    # days_of_culture NO se envía: columna generada (final_harvest_date − stocking_date)
    upsert(to_cycles_table(clean), "silver", "production_cycles", key_cols=["cycle_code"])

    cycle_ids = read_sql("SELECT cycle_id, cycle_code FROM silver.production_cycles")
    cycle_ids = dict(zip(cycle_ids["cycle_code"], cycle_ids["cycle_id"]))
    # harvested_lb NO se envía: columna generada (harvested_kg × 2,20462)
    upsert(to_harvests_table(clean, cycle_ids), "silver", "harvests",
           key_cols=["cycle_id", "harvest_date"])

    # 5.8 costos y ventas
    costs = clean_costs(clean, log, cycle_ids)
    upsert(costs[["cycle_id", "cost_type", "amount_usd"]], "silver", "cycle_costs",
           key_cols=["cycle_id", "cost_type"])

    harvests = read_sql("SELECT harvest_id, cycle_id FROM silver.harvests")
    code_by_cycle = {v: k for k, v in cycle_ids.items()}
    harvest_ids = {code_by_cycle[c]: h for h, c in zip(harvests["harvest_id"], harvests["cycle_id"])
                   if c in code_by_cycle}
    aliases = read_sql("SELECT alias_text, country_id FROM silver.country_aliases")
    alias_map = dict(zip(aliases["alias_text"], aliases["country_id"]))
    countries = read_sql("SELECT country_id, iso3, country_name_es FROM silver.countries")
    sales = clean_sales(clean, log, harvest_ids, alias_map, set(countries["country_name_es"]))
    # revenue_usd NO se envía: columna generada. Requiere UNIQUE (harvest_id) en silver.sales.
    upsert(sales.drop(columns="cycle_code"), "silver", "sales", key_cols=["harvest_id"])

    rules = log.to_frame()
    QUALITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    rules.to_csv(QUALITY_PATH, index=False, encoding="utf-8")
    logger.info("Registro de reglas guardado en %s", QUALITY_PATH.relative_to(ROOT_DIR))

    iso_by_country = dict(zip(countries["country_id"], countries["iso3"]))
    verification = pd.concat([
        verify_cycles_against_truth(clean),
        verify_costs_sales_against_truth(costs, sales, iso_by_country),
    ], ignore_index=True)
    verification.to_csv(VERIFY_PATH, index=False, encoding="utf-8")
    return rules, verification


# Ejecutar: python -m src.clean_farm
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    rules, verification = run()
    print("\nReglas aplicadas:")
    print(rules.to_string(index=False))

    summary = read_sql(
        """
        SELECT f.farm_code,
               COUNT(DISTINCT p.pond_id)              AS piscinas,
               COUNT(DISTINCT c.cycle_id)             AS ciclos,
               ROUND(SUM(h.harvested_lb) / 1e6, 2)    AS millones_lb,
               ROUND(AVG(c.days_of_culture), 1)       AS dias_promedio
        FROM silver.farms AS f
        JOIN silver.ponds             AS p ON p.farm_id  = f.farm_id
        JOIN silver.production_cycles AS c ON c.pond_id  = p.pond_id
        JOIN silver.harvests          AS h ON h.cycle_id = c.cycle_id
        GROUP BY f.farm_code
        ORDER BY f.farm_code
        """
    )
    print("\nCiclos y cosechas en silver (libras y días calculados por MySQL):")
    print(summary.to_string(index=False))

    money = read_sql(
        """
        SELECT YEAR(s.sale_date)                         AS anio,
               COUNT(*)                                  AS ventas,
               ROUND(SUM(s.revenue_usd) / 1e6, 2)        AS ingreso_musd,
               ROUND(SUM(k.costo) / 1e6, 2)              AS costo_musd,
               ROUND((SUM(s.revenue_usd) - SUM(k.costo))
                     / SUM(s.revenue_usd) * 100, 1)      AS margen_pct
        FROM silver.sales AS s
        JOIN silver.harvests AS h ON h.harvest_id = s.harvest_id
        JOIN (SELECT cycle_id, SUM(amount_usd) AS costo
              FROM silver.cycle_costs GROUP BY cycle_id) AS k ON k.cycle_id = h.cycle_id
        GROUP BY YEAR(s.sale_date)
        ORDER BY anio
        """
    )
    print("\nIngresos, costos y margen por año (ingreso calculado por MySQL):")
    print(money.to_string(index=False))

    ok, total = verify_ponds_against_truth()
    print(f"\nVerificación contra la verdad — áreas de piscinas: {ok}/{total}")
    print("\nVerificación contra la verdad — ciclos, cosechas, costos y ventas:")
    print(verification.to_string(index=False))