
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from src.audit import compute_record_hash, make_batch_id
from src.config import ROOT_DIR, project
from src.db import read_sql, write_df

logger = logging.getLogger(__name__)

TRUTH_PATH = ROOT_DIR / "data" / "processed" / "farm_clean_truth.csv"
ISSUES_PATH = ROOT_DIR / "data" / "processed" / "farm_injected_issues.csv"
RAW_DIR = ROOT_DIR / "data" / "raw" / "simulacion"
SOURCE_NAME = "Simulación Acuícola Del Golfo"
LB_PER_KG = project.kg_to_lb          # 2.20462

# ------------------------------------------------------------------ supuestos de simulación
PARAMS = {
    "first_stocking": date(2022, 7, 1),    # primera siembra posible
    "last_harvest": date(2026, 8, 31),     # última cosecha (hay precio real hasta aquí)
    "days_min": 90, "days_max": 120,       # días de cultivo
    "dry_days_min": 15, "dry_days_max": 30,  # secado/preparación entre ciclos
    "density_mean": 15, "density_sd": 4,   # larvas por m² (semi-intensivo)
    "density_min": 6, "density_max": 30,
    "survival_base": 72, "survival_sd": 7,  # % de supervivencia
    "survival_per_density": -0.5,          # cada larva/m² sobre 15 resta 0,5 puntos
    "growth_g_day": 0.24,                  # crecimiento diario de referencia (g/día)
    "growth_per_density": -0.012,          # más densidad → menos crecimiento (−1,2 %/larva)
    "growth_cycle_sd": 0.12,               # variación entre ciclos (clima, genética, manejo)
    "fcr_base": 1.30, "fcr_sd": 0.08,      # conversión alimenticia
    "fcr_per_density": 0.01,
    "farm_gate_share": 0.58,               # precio en finca ≈ 58 % del USD/lb de exportación
    "price_noise_sd": 0.03,
    "larvae_usd_per_thousand": 2.5,        # costo por mil larvas
    "feed_usd_per_kg": 1.15,               # costo del balanceado
    "energy_usd_ha_day": 8.0,              # aireadores y bombeo
    "labor_usd_ha_day": 6.0,
}

# 3 fincas: (nombre, provincia, prefijo, rango de nº de piscinas, rango de hectáreas)
FARMS = [
    ("Acuícola Del Golfo - Finca Taura", "Guayas", "GY", (17, 22), (6.0, 15.0)),
    ("Acuícola Del Golfo - Finca Hualtaco", "El Oro", "EO", (14, 19), (5.0, 12.0)),
    ("Acuícola Del Golfo - Finca Palmar", "Santa Elena", "SE", (9, 14), (4.0, 9.0)),
]

# Tallas de camarón entero: piezas por kilo (más grande = menos piezas = mejor precio)
# (talla, peso mínimo g, peso máximo g, multiplicador de precio)
SIZE_GRADES = [
    ("20/30", 33.3, 99.0, 1.35),
    ("30/40", 25.0, 33.3, 1.15),
    ("40/50", 20.0, 25.0, 1.00),
    ("50/60", 16.7, 20.0, 0.90),
    ("60/70", 14.3, 16.7, 0.82),
    ("70/80", 12.5, 14.3, 0.75),
    ("80/100", 0.0, 12.5, 0.68),
]

# Mercado de destino de cada cosecha (pesos aproximados a la participación real)
MARKETS = {
    "China": 0.49, "Estados Unidos": 0.19, "España": 0.08, "Francia": 0.07,
    "Italia": 0.07, "Vietnam": 0.05, "Rusia": 0.05,
}


# ------------------------------------------------------------------ precio real mensual
def load_monthly_price() -> pd.Series:
    """
    USD/lb mensual de exportación de camarón congelado (HS 030617), desde bronze:
    - Ecuador reporta (flow X, sin el total 'World') cuando existe;
    - si no, espejo (flow M, sin el agregado UE 97). El espejo es CIF: algo más alto.
    Devuelve una serie indexada por 'AAAAMM'.
    """
    df = read_sql(
        """
        SELECT flow_code,
               period,
               SUM(CAST(primary_value AS DECIMAL(20,2))) AS usd,
               SUM(CAST(net_wgt       AS DECIMAL(20,3))) AS kg
        FROM bronze.comtrade_raw
        WHERE cmd_code = '030617'
          AND partner_code  <> '0'
          AND reporter_code <> '97'
          AND net_wgt IS NOT NULL
          AND CAST(net_wgt AS DECIMAL(20,3)) > 0
        GROUP BY flow_code, period
        """
    )
    df["usd_lb"] = df["usd"].astype(float) / (df["kg"].astype(float) * LB_PER_KG)
    wide = df.pivot(index="period", columns="flow_code", values="usd_lb")
    price = wide["X"].combine_first(wide["M"]) if "X" in wide else wide["M"]
    n_mirror = int(wide["X"].isna().sum()) if "X" in wide else len(wide)
    logger.info("Precio mensual: %s meses (%s desde espejo), USD/lb %.2f – %.2f",
                len(price), n_mirror, price.min(), price.max())
    return price.sort_index()


def price_for(prices: pd.Series, d: date) -> float:
    """Precio de exportación del mes de 'd'; si falta, el último mes disponible anterior."""
    key = f"{d.year}{d.month:02d}"
    available = prices.loc[:key]
    if available.empty:
        raise ValueError(f"No hay precio de exportación para {key} ni meses anteriores")
    if available.index[-1] != key:
        logger.warning("Sin precio para %s: se usa el de %s", key, available.index[-1])
    return float(available.iloc[-1])


def size_grade_for(weight_g: float) -> tuple[str, float]:
    """Talla comercial y multiplicador de precio según el peso promedio."""
    for grade, w_min, w_max, mult in SIZE_GRADES:
        if w_min <= weight_g < w_max:
            return grade, mult
    return SIZE_GRADES[0][0], SIZE_GRADES[0][3]


# ------------------------------------------------------------------ Parte 1: datos limpios
def generate_clean(prices: pd.Series, seed: int = project.random_seed) -> pd.DataFrame:
    """Genera los ciclos limpios: fincas → piscinas → ciclos hasta la última cosecha."""
    rng = np.random.default_rng(seed)
    p = PARAMS
    market_names, market_weights = list(MARKETS), list(MARKETS.values())
    rows = []

    for farm_name, province, prefix, n_range, area_range in FARMS:
        n_ponds = int(rng.integers(n_range[0], n_range[1] + 1))
        for k in range(1, n_ponds + 1):
            pond_code = f"{prefix}-P{k:02d}"
            area_ha = round(float(rng.uniform(*area_range)), 2)
            stocking = p["first_stocking"] + timedelta(days=int(rng.integers(0, 60)))
            n_cycle = 0

            while True:
                days = int(rng.integers(p["days_min"], p["days_max"] + 1))
                harvest = stocking + timedelta(days=days)
                if harvest > p["last_harvest"]:
                    break
                n_cycle += 1

                # Densidad → supervivencia, peso y FCR (relaciones del enunciado)
                density = float(np.clip(rng.normal(p["density_mean"], p["density_sd"]),
                                        p["density_min"], p["density_max"]))
                excess = density - p["density_mean"]
                survival = float(np.clip(
                    rng.normal(p["survival_base"], p["survival_sd"])
                    + p["survival_per_density"] * excess, 35, 95))
                growth = (p["growth_g_day"] * (1 + p["growth_per_density"] * excess)
                          * max(0.6, rng.normal(1, p["growth_cycle_sd"])))
                weight = float(np.clip(days * growth + rng.normal(0, 1.5), 9, 40))
                fcr = float(np.clip(rng.normal(p["fcr_base"], p["fcr_sd"])
                                    + p["fcr_per_density"] * excess, 0.95, 2.2))

                # Biomasa cosechada
                stocked_pl = density * area_ha * 10_000          # larvas sembradas
                harvested_kg = stocked_pl * survival / 100 * weight / 1000
                harvested_lb = harvested_kg * LB_PER_KG
                feed_kg = harvested_kg * fcr

                # Precio: USD/lb real de exportación del mes de cosecha × talla
                grade, mult = size_grade_for(weight)
                price = (price_for(prices, harvest) * p["farm_gate_share"] * mult
                         * (1 + rng.normal(0, p["price_noise_sd"])))

                rows.append({
                    "farm_name": farm_name,
                    "province": province,
                    "pond_code": pond_code,
                    "pond_area_ha": area_ha,
                    "cycle_code": f"{pond_code}-C{n_cycle:02d}",
                    "stocking_date": stocking,
                    "harvest_date": harvest,
                    "days_of_culture": days,
                    "density_pl_m2": round(density, 1),
                    "survival_pct": round(survival, 1),
                    "avg_weight_g": round(weight, 1),
                    "size_grade": grade,
                    "harvested_qty": round(harvested_lb, 0),
                    "harvested_unit": "lb",
                    "feed_kg": round(feed_kg, 0),
                    "fcr": round(fcr, 2),
                    "cost_larvae_usd": round(stocked_pl / 1000 * p["larvae_usd_per_thousand"], 2),
                    "cost_feed_usd": round(feed_kg * p["feed_usd_per_kg"], 2),
                    "cost_energy_usd": round(area_ha * days * p["energy_usd_ha_day"], 2),
                    "cost_labor_usd": round(area_ha * days * p["labor_usd_ha_day"], 2),
                    "sale_price_usd_lb": round(price, 2),
                    "destination_market": str(rng.choice(market_names, p=market_weights)),
                })

                # Siguiente ciclo después del secado de la piscina
                stocking = harvest + timedelta(
                    days=int(rng.integers(p["dry_days_min"], p["dry_days_max"] + 1)))

    df = pd.DataFrame(rows)
    logger.info("Simulación limpia: %s fincas, %s piscinas, %s ciclos",
                df["farm_name"].nunique(), df["pond_code"].nunique(), len(df))
    return df


def summarize(df: pd.DataFrame) -> None:
    """Indicadores de control: ¿los números son verosímiles?"""
    cost = df[["cost_larvae_usd", "cost_feed_usd", "cost_energy_usd", "cost_labor_usd"]].sum(axis=1)
    revenue = df["harvested_qty"] * df["sale_price_usd_lb"]
    yearly = df.assign(
        year=pd.to_datetime(df["harvest_date"]).dt.year,
        revenue=revenue, cost=cost,
    ).groupby("year").agg(
        ciclos=("cycle_code", "count"),
        libras=("harvested_qty", "sum"),
        precio_prom=("sale_price_usd_lb", "mean"),
        ingreso=("revenue", "sum"),
        costo=("cost", "sum"),
    )
    yearly["costo_lb"] = yearly["costo"] / yearly["libras"]
    yearly["margen_pct"] = (yearly["ingreso"] - yearly["costo"]) / yearly["ingreso"] * 100

    print("\nPiscinas por finca:")
    print(df.groupby("province")["pond_code"].nunique().to_string())
    print(f"\nCiclos: {len(df)} | fechas de cosecha: "
          f"{df['harvest_date'].min()} → {df['harvest_date'].max()}")
    print("\nRangos de variables clave:")
    print(df[["days_of_culture", "density_pl_m2", "survival_pct",
              "avg_weight_g", "fcr", "sale_price_usd_lb"]]
          .describe().loc[["min", "mean", "max"]].round(2).to_string())
    print("\nTallas cosechadas:")
    print(df["size_grade"].value_counts().to_string())
    print("\nResumen por año de cosecha:")
    print(yearly.round(2).to_string(float_format=lambda x: f"{x:,.2f}"))

    # Comprobación de las relaciones del enunciado (correlaciones)
    corr_dw = df["density_pl_m2"].corr(df["avg_weight_g"])
    corr_fc = df["fcr"].corr(cost / df["harvested_qty"])
    print(f"\nCorrelación densidad ↔ peso      : {corr_dw:+.2f}  (esperado: negativa)")
    print(f"Correlación FCR ↔ costo por libra : {corr_fc:+.2f}  (esperado: positiva)")


# ------------------------------------------------------------------ Parte 2: problemas de calidad
# Proporción de filas (o de celdas) afectadas por cada problema (supuestos de simulación)
ISSUE_RATES = {
    "talla_formato": 0.30,      # '20/30' → '20-30' / '20 30'
    "unidad_kg": 0.15,          # cantidad expresada en kg en vez de lb
    "fecha_formato": 0.25,      # por cada columna de fecha
    "coma_decimal": 0.10,       # por cada columna numérica con decimales
    "nulo": 0.02,               # por cada columna no crítica
    "fcr_outlier": 0.01,        # FCR = 0 o 15
    "pais_variante_usa": 0.40,  # de las filas con 'Estados Unidos'
    "pais_variante_china": 0.10,
    "duplicado": 0.03,          # filas repetidas exactamente
}
DATE_COLS = ["stocking_date", "harvest_date"]
# Formatos alternativos: TODOS con el día antes que el mes (convención ecuatoriana),
# para que la limpieza tenga una regla clara y no haya fechas ambiguas.
DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]
DECIMAL_COLS = ["pond_area_ha", "avg_weight_g", "sale_price_usd_lb"]
NULLABLE_COLS = ["density_pl_m2", "survival_pct", "avg_weight_g",
                 "cost_energy_usd", "destination_market"]
USA_VARIANTS = ["EEUU", "USA", "U.S.A.", "usa ", "Estados Unidos de América"]
CHINA_VARIANTS = ["china", "CHINA", "China "]
CONTENT_COLS = [
    "farm_name", "province", "pond_code", "pond_area_ha", "cycle_code", "stocking_date",
    "harvest_date", "days_of_culture", "density_pl_m2", "survival_pct", "avg_weight_g",
    "size_grade", "harvested_qty", "harvested_unit", "feed_kg", "fcr", "cost_larvae_usd",
    "cost_feed_usd", "cost_energy_usd", "cost_labor_usd", "sale_price_usd_lb",
    "destination_market",
]


def _to_text(value) -> str | None:
    """Texto canónico: fechas ISO, enteros sin '.0', decimales con punto."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def inject_issues(clean: pd.DataFrame,
                  seed: int = project.random_seed) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Copia los datos limpios a TEXTO y los ensucia con problemas típicos.
    Cada celda modificada queda en la bitácora (ciclo, columna, problema, original, sucio).
    Una celda nunca recibe dos problemas (así la bitácora es exacta).
    Devuelve (datos_sucios, bitacora).
    """
    rng = np.random.default_rng(seed + 1)      # semilla distinta a la Parte 1
    dirty = clean[CONTENT_COLS].map(_to_text).astype(object)
    touched: set[tuple[int, str]] = set()      # celdas ya modificadas
    log: list[dict] = []

    def pick(column: str, rate: float, mask: pd.Series | None = None) -> list[int]:
        """Elige al azar filas elegibles (no tocadas en esa columna y no nulas)."""
        ok = dirty[column].notna()
        if mask is not None:
            ok &= mask
        candidates = [i for i in dirty.index[ok] if (i, column) not in touched]
        n = int(round(len(candidates) * rate))
        return sorted(rng.choice(candidates, size=n, replace=False).tolist()) if n else []

    def change(i: int, column: str, issue: str, new) -> None:
        log.append({"cycle_code": dirty.at[i, "cycle_code"], "column": column,
                    "issue": issue, "original": dirty.at[i, column], "dirty": new})
        dirty.at[i, column] = new
        touched.add((i, column))

    # 1) Tallas escritas de otra forma
    for i in pick("size_grade", ISSUE_RATES["talla_formato"]):
        sep = str(rng.choice(["-", " "]))
        change(i, "size_grade", "talla_formato", dirty.at[i, "size_grade"].replace("/", sep))

    # 2) Cantidad en kg en vez de lb (la unidad cambia en la misma fila)
    for i in pick("harvested_qty", ISSUE_RATES["unidad_kg"]):
        kg = round(float(dirty.at[i, "harvested_qty"]) / LB_PER_KG)
        change(i, "harvested_qty", "unidad_kg", str(kg))
        change(i, "harvested_unit", "unidad_kg", "kg")

    # 3) Fechas en otros formatos
    for column in DATE_COLS:
        for i in pick(column, ISSUE_RATES["fecha_formato"]):
            d = date.fromisoformat(dirty.at[i, column])
            change(i, column, "fecha_formato", d.strftime(str(rng.choice(DATE_FORMATS))))

    # 4) Coma decimal
    for column in DECIMAL_COLS:
        has_point = dirty[column].fillna("").str.contains(".", regex=False)
        for i in pick(column, ISSUE_RATES["coma_decimal"], has_point):
            change(i, column, "coma_decimal", dirty.at[i, column].replace(".", ","))

    # 5) FCR imposibles (0 o 15)
    for i in pick("fcr", ISSUE_RATES["fcr_outlier"]):
        change(i, "fcr", "fcr_outlier", str(rng.choice(["0", "15"])))

    # 6) Países escritos de varias formas
    for name, variants, key in [("Estados Unidos", USA_VARIANTS, "pais_variante_usa"),
                                ("China", CHINA_VARIANTS, "pais_variante_china")]:
        for i in pick("destination_market", ISSUE_RATES[key],
                      dirty["destination_market"] == name):
            change(i, "destination_market", key, str(rng.choice(variants)))

    # 7) Nulos en columnas no críticas
    for column in NULLABLE_COLS:
        for i in pick(column, ISSUE_RATES["nulo"]):
            change(i, column, "nulo", None)

    # 8) Duplicados exactos (se copian filas ya "sucias"), luego se baraja el orden
    n_dup = int(round(len(dirty) * ISSUE_RATES["duplicado"]))
    dup_idx = sorted(rng.choice(dirty.index, size=n_dup, replace=False).tolist())
    for i in dup_idx:
        log.append({"cycle_code": dirty.at[i, "cycle_code"], "column": "(fila completa)",
                    "issue": "duplicado", "original": None, "dirty": None})
    dirty = pd.concat([dirty, dirty.loc[dup_idx]], ignore_index=True)
    dirty = dirty.iloc[rng.permutation(len(dirty))].reset_index(drop=True)

    issues = pd.DataFrame(log)
    logger.info("Problemas inyectados: %s cambios en %s filas (%s duplicados)",
                len(issues), len(dirty), n_dup)
    return dirty, issues


def load_to_bronze(dirty: pd.DataFrame) -> int:
    """
    Carga los datos sucios a bronze.farm_raw. La huella incluye la POSICIÓN de la fila
    en el archivo generado: así los duplicados exactos (problema a detectar en silver)
    llegan a bronze, y la carga sigue siendo idempotente (misma semilla → mismas huellas).
    """
    df = dirty.copy()
    df["source_row"] = range(1, len(df) + 1)
    df["record_hash"] = compute_record_hash(df, CONTENT_COLS + ["source_row"])
    df = df.drop(columns="source_row")
    df["source_name"] = SOURCE_NAME
    df["batch_id"] = make_batch_id("farm-sim")
    df["extracted_at"] = datetime.now()

    known = set(read_sql("SELECT record_hash FROM bronze.farm_raw")["record_hash"])
    new = df[~df["record_hash"].isin(known)]
    if len(df) - len(new):
        logger.info("Simulación: %s filas ya existían en bronze, se omiten", len(df) - len(new))
    if new.empty:
        logger.info("Simulación: no hay filas nuevas que cargar")
        return 0
    write_df(new, "bronze", "farm_raw")
    return len(new)


def save_csv(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info("Guardado %s", path.relative_to(ROOT_DIR))


# Ejecutar: python -m src.simulate_farm
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()

    # Parte 1: datos limpios (verdad de referencia)
    prices = load_monthly_price()
    clean = generate_clean(prices)
    summarize(clean)
    save_csv(clean, TRUTH_PATH)

    # Parte 2: problemas de calidad documentados → archivo crudo → bronze
    dirty, issues = inject_issues(clean)
    save_csv(issues, ISSUES_PATH)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    save_csv(dirty, RAW_DIR / f"farm_raw_seed{project.random_seed}_{stamp}.csv")
    n_new = load_to_bronze(dirty)

    print("\nProblemas inyectados (celdas afectadas por tipo):")
    print(issues["issue"].value_counts().to_string())
    print("\nEjemplos de valores sucios:")
    print(issues.dropna(subset=["dirty"]).groupby("issue").head(1)
          [["issue", "column", "original", "dirty"]].to_string(index=False))

    total = read_sql("SELECT COUNT(*) AS n FROM bronze.farm_raw")["n"].iloc[0]
    print(f"\nFilas generadas: {len(dirty)} (limpias {len(clean)} + duplicados "
          f"{len(dirty) - len(clean)}) | nuevas en bronze: {n_new} | total en bronze: {total}")