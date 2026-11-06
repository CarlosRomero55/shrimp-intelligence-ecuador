
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import comtradeapicall
import pandas as pd

from src.config import ROOT_DIR, project, settings

logger = logging.getLogger(__name__)

# Carpeta de respuestas crudas de Comtrade (evidencia de lo que devolvió la API)
RAW_DIR = ROOT_DIR / "data" / "raw" / "comtrade"

FLOW_EXPORT = "X"          # X = exportaciones en Comtrade
WORLD_PARTNER_CODE = 0     # partnerCode 0 = "World" (total mundial, NO es un país)


def build_periods(year: int, months: list[int] | range = range(1, 13)) -> str:
    """Arma el parámetro period: build_periods(2025, [1]) → '202501'.
    La API admite como máximo 12 meses por llamada."""
    months = list(months)
    if not 1 <= len(months) <= 12:
        raise ValueError("Comtrade admite entre 1 y 12 meses por llamada")
    return ",".join(f"{year}{m:02d}" for m in months)


def get_availability(freq: str = "M") -> pd.DataFrame:
    """
    Qué períodos tiene publicados Ecuador en Comtrade (freq 'M' mensual o 'A' anual).
    Devuelve una fila por período, ordenada, con fechas de publicación si la API las trae.
    Sirve para diagnosticar respuestas vacías y, en la Fase 8, para la carga incremental.
    """
    key = settings.require_comtrade_key()
    df = comtradeapicall.getFinalDataAvailability(
        key,
        typeCode="C",
        freqCode=freq,
        clCode="HS",
        period=None,                           # None = todos los períodos
        reporterCode=str(project.reporter_code),
    )
    if df is None:
        raise RuntimeError("Comtrade no respondió la consulta de disponibilidad "
                           "(revisar llave en .env o cuota diaria).")
    if df.empty:
        logger.warning("Disponibilidad: Ecuador no tiene períodos publicados (freq=%s)", freq)
        return df

    # Columnas útiles, solo si existen (la API puede variar)
    wanted = ["period", "classificationCode", "totalRecords", "firstReleased", "lastReleased"]
    cols = [c for c in wanted if c in df.columns]
    out = (
        df[cols]
        .assign(period=df["period"].astype(str))
        .drop_duplicates(subset="period", keep="last")   # una fila por período
        .sort_values("period")
        .reset_index(drop=True)
    )
    logger.info("Disponibilidad (freq=%s): %s períodos, de %s a %s",
                freq, len(out), out["period"].iloc[0], out["period"].iloc[-1])
    return out


def fetch_comtrade(period: str) -> pd.DataFrame:
    """Descarga exportaciones mensuales de Ecuador para el/los período(s) indicado(s)."""
    key = settings.require_comtrade_key()   # falla con mensaje claro si falta en .env

    start = time.perf_counter()
    df = comtradeapicall.getFinalData(
        key,
        typeCode="C",                          # C = bienes (no servicios)
        freqCode="M",                          # M = mensual
        clCode="HS",                           # clasificación Sistema Armonizado
        period=period,
        reporterCode=str(project.reporter_code),
        cmdCode=",".join(project.hs_codes),    # '030617,160521,160529'
        flowCode=FLOW_EXPORT,
        partnerCode=None,                      # None = todos los países destino
        partner2Code=None,
        customsCode=None,
        motCode=None,
        maxRecords=100000,                     # tope de la cuenta gratuita por llamada
        format_output="JSON",
        aggregateBy=None,
        breakdownMode="classic",               # desglose por país y producto
        countOnly=None,
        includeDesc=True,                      # incluye nombres de país y producto
    )
    elapsed = time.perf_counter() - start

    # La librería NO lanza excepción si la API falla (llave inválida, cuota agotada):
    # devuelve None. Lo convertimos en error explícito para que el pipeline se detenga.
    if df is None:
        raise RuntimeError(
            f"Comtrade no devolvió datos para period={period}. "
            "Revisar llave en .env, cuota diaria (500 llamadas) o parámetros."
        )
    if df.empty:
        logger.warning("Comtrade: 0 filas para period=%s en %.1f s (¿mes no publicado?)",
                       period, elapsed)
    else:
        logger.info("Comtrade: %s filas para period=%s en %.1f s", len(df), period, elapsed)
    return df


def save_raw(df: pd.DataFrame, period_label: str) -> Path | None:
    """Guarda la respuesta tal cual. El nombre lleva fecha/hora de extracción,
    así una nueva descarga nunca sobrescribe la anterior.
    Una respuesta vacía no se guarda (solo queda la advertencia en el log)."""
    if df.empty:
        logger.warning("Respuesta vacía para %s: no se guarda archivo", period_label)
        return None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = RAW_DIR / f"comtrade_{project.reporter_code}_{FLOW_EXPORT}_{period_label}_{stamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info("Respuesta cruda guardada en %s", path.relative_to(ROOT_DIR))
    return path


# Prueba de los pasos 4.2 / 4.2b: python -m src.extract_comtrade
# Usa 2–3 llamadas de las 500 diarias.
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()

    # 1) ¿Qué meses tiene publicados Ecuador?
    monthly = get_availability("M")

    if monthly.empty:
        # Sin datos mensuales: ver si al menos hay anuales
        annual = get_availability("A")
        print("\nEcuador NO tiene datos MENSUALES en Comtrade.")
        if not annual.empty:
            print(f"Sí tiene ANUALES: {annual['period'].iloc[0]}–{annual['period'].iloc[-1]}")
            print(annual.tail(6).to_string(index=False))
    else:
        periods = monthly["period"].tolist()
        print(f"\nEcuador en Comtrade (mensual): {len(periods)} meses publicados, "
              f"desde {periods[0]} hasta {periods[-1]}")
        print(f"¿202501 publicado?: {'sí' if '202501' in periods else 'NO'}")
        print("\nÚltimos 6 meses publicados:")
        print(monthly.tail(6).to_string(index=False))

        # 2) Descargar el último mes publicado
        last = periods[-1]
        df = fetch_comtrade(last)
        save_raw(df, last)

        print(f"\nColumnas ({len(df.columns)}): {list(df.columns)}\n")

        if not df.empty:
            world = df[df["partnerCode"] == WORLD_PARTNER_CODE]
            countries = df[df["partnerCode"] != WORLD_PARTNER_CODE]

            top5 = (
                countries.groupby("partnerDesc", as_index=False)[["primaryValue", "netWgt"]]
                .sum()
                .nlargest(5, "primaryValue")
            )
            print(f"Top 5 destinos en {last} (los 3 códigos HS sumados):")
            print(top5.to_string(index=False, float_format=lambda x: f"{x:,.0f}"))

            print(f"\nPaíses destino distintos : {countries['partnerCode'].nunique()}")
            print(f"Suma por países  (USD)   : {countries['primaryValue'].sum():,.0f}")
            print(f"Fila 'World'     (USD)   : {world['primaryValue'].sum():,.0f}")
            print(f"Suma por países  (kg)    : {countries['netWgt'].sum():,.0f}")