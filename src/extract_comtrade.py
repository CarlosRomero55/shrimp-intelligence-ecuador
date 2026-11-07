
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import comtradeapicall
import pandas as pd

from src.audit import compute_record_hash, make_batch_id
from src.config import ROOT_DIR, project, settings
from src.db import read_sql, write_df

logger = logging.getLogger(__name__)

# Carpeta de respuestas crudas de Comtrade (evidencia de lo que devolvió la API)
RAW_DIR = ROOT_DIR / "data" / "raw" / "comtrade"

SOURCE_NAME = "UN Comtrade"
FLOW_EXPORT = "X"          # X = exportaciones en Comtrade
WORLD_PARTNER_CODE = 0     # partnerCode 0 = "World" (total mundial, NO es un país)
START_YEAR = 2018          # desde 2018 para poder calcular YoY de 2019
PAUSE_SECONDS = 2          # pausa entre llamadas (límite de la API)
MAX_RETRIES = 3

# Columna de la API → columna de bronze.comtrade_raw (todas se guardan como texto)
COLUMN_MAP = {
    "freqCode": "freq_code",
    "period": "period",
    "reporterCode": "reporter_code",
    "flowCode": "flow_code",
    "partnerCode": "partner_code",
    "partnerISO": "partner_iso",
    "partnerDesc": "partner_desc",
    "cmdCode": "cmd_code",
    "cmdDesc": "cmd_desc",
    "qtyUnitAbbr": "qty_unit_abbr",
    "qty": "qty",
    "netWgt": "net_wgt",
    "fobvalue": "fob_value",
    "primaryValue": "primary_value",
}


# --------------------------------------------------------------------------- API
def get_availability(freq: str = "M") -> pd.DataFrame:
    """
    Qué períodos tiene publicados Ecuador en Comtrade (freq 'M' mensual o 'A' anual).
    Devuelve una fila por período, ordenada, con fechas de publicación si la API las trae.
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
    """Una llamada a la API: exportaciones mensuales de Ecuador para 'period'
    (uno o varios meses separados por coma, máximo 12)."""
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

    # La librería NO lanza excepción si la API falla: devuelve None.
    if df is None:
        raise RuntimeError(f"Comtrade no devolvió datos para period={period}")
    if df.empty:
        logger.warning("Comtrade: 0 filas para period=%s en %.1f s", period, elapsed)
    else:
        logger.info("Comtrade: %s filas para %s mes(es) en %.1f s",
                    len(df), len(period.split(",")), elapsed)
    return df


def fetch_with_retry(period: str) -> pd.DataFrame:
    """fetch_comtrade con reintentos y espera creciente (5 s, 10 s, 15 s)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch_comtrade(period)
        except RuntimeError:
            if attempt == MAX_RETRIES:
                logger.error("Comtrade: %s intentos fallidos para %s", MAX_RETRIES, period)
                raise
            wait = 5 * attempt
            logger.warning("Comtrade: intento %s falló, reintento en %s s", attempt, wait)
            time.sleep(wait)
    raise AssertionError("inalcanzable")


# --------------------------------------------------------------------------- archivos
def save_raw(df: pd.DataFrame, period_label: str) -> Path | None:
    """Guarda la respuesta tal cual (nunca sobrescribe: el nombre lleva fecha/hora).
    Una respuesta vacía no se guarda."""
    if df.empty:
        logger.warning("Respuesta vacía para %s: no se guarda archivo", period_label)
        return None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = RAW_DIR / f"comtrade_{project.reporter_code}_{FLOW_EXPORT}_{period_label}_{stamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info("Respuesta cruda guardada en %s", path.relative_to(ROOT_DIR))
    return path


# --------------------------------------------------------------------------- bronze
def to_bronze(df: pd.DataFrame, batch_id: str) -> pd.DataFrame:
    """Convierte la respuesta de la API al formato de bronze.comtrade_raw:
    campos rápidos como texto + fila completa en raw_payload (JSON) + auditoría."""
    # to_json convierte NaN → null y tipos de numpy → JSON válido (MySQL rechaza NaN)
    records = json.loads(df.to_json(orient="records", force_ascii=False, double_precision=15))

    rows = []
    for rec in records:
        row = {dst: (None if rec.get(src) is None else str(rec[src]))
               for src, dst in COLUMN_MAP.items()}
        # sort_keys: el mismo contenido produce siempre el mismo texto → misma huella
        row["raw_payload"] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        rows.append(row)

    out = pd.DataFrame(rows)
    out["record_hash"] = compute_record_hash(out, ["raw_payload"])
    out["source_name"] = SOURCE_NAME
    out["batch_id"] = batch_id
    out["extracted_at"] = datetime.now()
    return out


def existing_hashes() -> set[str]:
    """Huellas ya cargadas (la llave UNIQUE es por lote, así que la idempotencia
    entre ejecuciones se controla aquí)."""
    return set(read_sql("SELECT record_hash FROM bronze.comtrade_raw")["record_hash"])


def missing_periods(published: list[str], start_year: int = START_YEAR) -> list[str]:
    """Meses esperados (desde start_year hasta el último publicado) que NO están publicados."""
    last = max(published)
    expected = [
        f"{y}{m:02d}"
        for y in range(start_year, int(last[:4]) + 1)
        for m in range(1, 13)
        if f"{y}{m:02d}" <= last
    ]
    return [p for p in expected if p not in set(published)]


def load_comtrade(start_year: int = START_YEAR) -> None:
    """Descarga y carga a bronze todos los meses publicados desde start_year."""
    available = get_availability("M")
    published = [p for p in available["period"] if p >= f"{start_year}01"]

    by_year: dict[str, list[str]] = {}
    for p in published:
        by_year.setdefault(p[:4], []).append(p)

    known = existing_hashes()
    total_new = 0

    for i, (year, periods) in enumerate(sorted(by_year.items())):
        if i > 0:
            time.sleep(PAUSE_SECONDS)
        df = fetch_with_retry(",".join(periods))      # ≤ 12 meses por llamada
        save_raw(df, year)
        if df.empty:
            continue

        batch = to_bronze(df, make_batch_id("comtrade"))
        new = batch[~batch["record_hash"].isin(known)]
        skipped = len(batch) - len(new)
        if skipped:
            logger.info("Comtrade %s: %s filas ya existían en bronze, se omiten", year, skipped)
        if not new.empty:
            write_df(new, "bronze", "comtrade_raw", dedupe_on=["record_hash"])
            known.update(new["record_hash"])
            total_new += len(new)

    logger.info("Comtrade: carga terminada | %s filas nuevas en total", total_new)

    gaps = missing_periods(list(available["period"]), start_year)
    if gaps:
        logger.warning("Comtrade: %s meses NO publicados desde %s: %s",
                       len(gaps), start_year, ", ".join(gaps))


# Ejecutar: python -m src.extract_comtrade   (≈ 1 + 1 llamada por año)
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    load_comtrade()

    # Cobertura por año, leída desde la BASE
    coverage = read_sql(
        """
        SELECT LEFT(period, 4)        AS anio,
               COUNT(DISTINCT period) AS meses,
               COUNT(*)               AS filas
        FROM bronze.comtrade_raw
        GROUP BY LEFT(period, 4)
        ORDER BY anio
        """
    )
    print("\nCobertura de Comtrade en bronze (meses con datos por año):")
    print(coverage.to_string(index=False))