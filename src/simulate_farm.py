"""
simulate_farm.py — Datos simulados de la camaronera ficticia "Acuícola Del Golfo S.A."

Parte 1 (Paso 4.6a): genera los ciclos de cultivo LIMPIOS (la "verdad") con relaciones
realistas y semilla fija. Se guardan en data/processed/farm_clean_truth.csv para medir,
en la Fase 6, qué tan bien la limpieza recupera los valores reales.
Parte 2 (Paso 4.6b): inyectará problemas de calidad y cargará a bronze.farm_raw.

Grano: una fila = un ciclo de cultivo de una piscina (siembra → cosecha → venta).

IMPORTANTE: todos los valores de PARAMS son SUPUESTOS de simulación (no datos reales),
elegidos para producir órdenes de magnitud verosímiles. Solo el precio mensual de
exportación es real (sale de bronze.comtrade_raw).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.config import ROOT_DIR, project
from src.db import read_sql

logger = logging.getLogger(__name__)

TRUTH_PATH = ROOT_DIR / "data" / "processed" / "farm_clean_truth.csv"
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


# ------------------------------------------------------------------ generación
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


# Ejecutar: python -m src.simulate_farm   (no escribe en la base; solo el CSV de verdad)
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    prices = load_monthly_price()
    clean = generate_clean(prices)
    summarize(clean)

    TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(TRUTH_PATH, index=False, encoding="utf-8")
    logger.info("Verdad de referencia guardada en %s", TRUTH_PATH.relative_to(ROOT_DIR))