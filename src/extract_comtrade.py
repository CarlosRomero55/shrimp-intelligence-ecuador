"""
extract_comtrade.py — Extracción de exportaciones de camarón de Ecuador desde UN Comtrade.

Dos modos (misma tabla bronze.comtrade_raw, distinguidos por flow_code y source_name):
  export  → lo que ECUADOR reporta como exportado (flow X, reporter 218, todos los destinos)
  mirror  → lo que el RESTO DEL MUNDO reporta como importado desde Ecuador
            (flow M, todos los reporters, partner 218). Valores CIF.

Hallazgo documentado: Comtrade no tiene publicados de Ecuador Feb 2022–Dic 2023,
Abr 2024 ni 2025. El espejo cubre el detalle por país de esos meses; el total
nacional oficial viene del BCE (extract_bce.py).

Uso:
    python -m src.extract_comtrade            # exportaciones de Ecuador
    python -m src.extract_comtrade --mirror   # estadísticas espejo
"""
from __future__ import annotations

import argparse
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

ECUADOR = str(project.reporter_code)   # '218'
WORLD_PARTNER_CODE = 0     # partnerCode 0 = "World" (total mundial, NO es un país)
START_YEAR = 2018          # desde 2018 para poder calcular YoY de 2019
PAUSE_SECONDS = 2          # pausa entre llamadas (límite de la API)
MAX_RETRIES = 3

# Qué cambia entre los dos modos de consulta
MODES = {
    "export": {
        "flow": "X",                      # exportaciones
        "reporter": ECUADOR,              # quien reporta: Ecuador
        "partner": None,                  # hacia: todos los países
        "source_name": "UN Comtrade",
        "file_tag": "X",
        "batch_prefix": "comtrade",
    },
    "mirror": {
        "flow": "M",                      # importaciones
        "reporter": None,                 # quien reporta: todos los países
        "partner": ECUADOR,               # desde: Ecuador
        "source_name": "UN Comtrade (espejo)",
        "file_tag": "M_espejo",
        "batch_prefix": "comtrade-m",
    },
}

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
        reporterCode=ECUADOR,
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


def fetch_comtrade(period: str, mode: str = "export") -> pd.DataFrame:
    """Una llamada a la API para 'period' (uno o varios meses separados por coma,
    máximo 12) en el modo indicado ('export' o 'mirror')."""
    cfg = MODES[mode]
    key = settings.require_comtrade_key()   # falla con mensaje claro si falta en .env

    start = time.perf_counter()
    df = comtradeapicall.getFinalData(
        key,
        typeCode="C",                          # C = bienes (no servicios)
        freqCode="M",                          # M = mensual
        clCode="HS",                           # clasificación Sistema Armonizado
        period=period,
        reporterCode=cfg["reporter"],
        cmdCode=",".join(project.hs_codes),    # '030617,160521,160529'
        flowCode=cfg["flow"],
        partnerCode=cfg["partner"],
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
        raise RuntimeError(f"Comtrade no devolvió datos para period={period} (modo {mode})")
    n_months = len(period.split(","))
    if df.empty:
        logger.warning("Comtrade [%s]: 0 filas para %s mes(es) en %.1f s",
                       mode, n_months, elapsed)
    else:
        logger.info("Comtrade [%s]: %s filas para %s mes(es) en %.1f s",
                    mode, len(df), n_months, elapsed)
    return df


def fetch_with_retry(period: str, mode: str = "export") -> pd.DataFrame:
    """fetch_comtrade con reintentos y espera creciente (5 s, 10 s, 15 s)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch_comtrade(period, mode)
        except RuntimeError:
            if attempt == MAX_RETRIES:
                logger.error("Comtrade [%s]: %s intentos fallidos para %s",
                             mode, MAX_RETRIES, period)
                raise
            wait = 5 * attempt
            logger.warning("Comtrade [%s]: intento %s falló, reintento en %s s",
                           mode, attempt, wait)
            time.sleep(wait)
    raise AssertionError("inalcanzable")


# --------------------------------------------------------------------------- archivos
def save_raw(df: pd.DataFrame, period_label: str, mode: str = "export") -> Path | None:
    """Guarda la respuesta tal cual (nunca sobrescribe: el nombre lleva fecha/hora).
    Una respuesta vacía no se guarda."""
    if df.empty:
        logger.warning("Respuesta vacía para %s (%s): no se guarda archivo", period_label, mode)
        return None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    tag = MODES[mode]["file_tag"]
    path = RAW_DIR / f"comtrade_{ECUADOR}_{tag}_{period_label}_{stamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info("Respuesta cruda guardada en %s", path.relative_to(ROOT_DIR))
    return path


# --------------------------------------------------------------------------- bronze
def to_bronze(df: pd.DataFrame, batch_id: str, source_name: str) -> pd.DataFrame:
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
    out["source_name"] = source_name
    out["batch_id"] = batch_id
    out["extracted_at"] = datetime.now()
    return out


def existing_hashes() -> set[str]:
    """Huellas ya cargadas (la llave UNIQUE es por lote, así que la idempotencia
    entre ejecuciones se controla aquí)."""
    return set(read_sql("SELECT record_hash FROM bronze.comtrade_raw")["record_hash"])


def expected_periods(start_year: int, last_period: str) -> list[str]:
    """Todos los meses AAAAMM desde enero de start_year hasta last_period (incluido)."""
    return [
        f"{y}{m:02d}"
        for y in range(start_year, int(last_period[:4]) + 1)
        for m in range(1, 13)
        if f"{y}{m:02d}" <= last_period
    ]


def missing_periods(published: list[str], start_year: int = START_YEAR) -> list[str]:
    """Meses esperados (desde start_year hasta el último publicado) que NO están publicados."""
    published_set = set(published)
    return [p for p in expected_periods(start_year, max(published)) if p not in published_set]


def _load_years(by_year: dict[str, list[str]], mode: str) -> int:
    """Descarga año por año (≤ 12 meses por llamada) y carga a bronze solo lo nuevo.
    Devuelve el número de filas nuevas cargadas."""
    cfg = MODES[mode]
    known = existing_hashes()
    total_new = 0

    for i, (year, periods) in enumerate(sorted(by_year.items())):
        if i > 0:
            time.sleep(PAUSE_SECONDS)
        df = fetch_with_retry(",".join(periods), mode)
        save_raw(df, year, mode)
        if df.empty:
            continue

        batch = to_bronze(df, make_batch_id(cfg["batch_prefix"]), cfg["source_name"])
        new = batch[~batch["record_hash"].isin(known)]
        skipped = len(batch) - len(new)
        if skipped:
            logger.info("Comtrade [%s] %s: %s filas ya existían en bronze, se omiten",
                        mode, year, skipped)
        if not new.empty:
            write_df(new, "bronze", "comtrade_raw", dedupe_on=["record_hash"])
            known.update(new["record_hash"])
            total_new += len(new)

    logger.info("Comtrade [%s]: carga terminada | %s filas nuevas en total", mode, total_new)
    return total_new


def load_comtrade(start_year: int = START_YEAR) -> None:
    """Exportaciones reportadas por Ecuador: solo los meses que Comtrade tiene publicados."""
    available = get_availability("M")
    published = [p for p in available["period"] if p >= f"{start_year}01"]

    by_year: dict[str, list[str]] = {}
    for p in published:
        by_year.setdefault(p[:4], []).append(p)
    _load_years(by_year, "export")

    gaps = missing_periods(list(available["period"]), start_year)
    if gaps:
        logger.warning("Comtrade: %s meses NO publicados por Ecuador desde %s: %s",
                       len(gaps), start_year, ", ".join(gaps))


def load_comtrade_mirror(start_year: int = START_YEAR) -> None:
    """Estadísticas espejo: todos los meses desde start_year hasta el mes anterior al actual
    (los meses que ningún país publicó todavía simplemente vuelven vacíos)."""
    today = datetime.now()
    last_year, last_month = (today.year, today.month - 1) if today.month > 1 \
        else (today.year - 1, 12)
    last_period = f"{last_year}{last_month:02d}"

    by_year: dict[str, list[str]] = {}
    for p in expected_periods(start_year, last_period):
        by_year.setdefault(p[:4], []).append(p)
    _load_years(by_year, "mirror")


# --------------------------------------------------------------------------- reportes
def print_coverage(flow: str) -> None:
    """Cobertura por año leída desde la BASE para un flujo ('X' o 'M')."""
    coverage = read_sql(
        """
        SELECT LEFT(period, 4)               AS anio,
               COUNT(DISTINCT period)        AS meses,
               COUNT(DISTINCT reporter_code) AS reportadores,
               COUNT(DISTINCT partner_code)  AS socios,
               COUNT(*)                      AS filas
        FROM bronze.comtrade_raw
        WHERE flow_code = :f
        GROUP BY LEFT(period, 4)
        ORDER BY anio
        """,
        {"f": flow},
    )
    title = "exportaciones de Ecuador (X)" if flow == "X" else "espejo (M)"
    print(f"\nCobertura en bronze — {title}:")
    print(coverage.to_string(index=False))


def print_gap_coverage(start_year: int = START_YEAR) -> None:
    """¿Qué meses sin dato de Ecuador (X) quedan cubiertos por el espejo (M)?"""
    periods = read_sql(
        "SELECT DISTINCT flow_code, period FROM bronze.comtrade_raw WHERE flow_code IN ('X','M')"
    )
    x = set(periods.loc[periods["flow_code"] == "X", "period"])
    m = set(periods.loc[periods["flow_code"] == "M", "period"])
    if not m:
        print("\nAún no hay datos espejo en bronze.")
        return

    gaps = [p for p in expected_periods(start_year, max(m | x)) if p not in x]
    covered = [p for p in gaps if p in m]
    uncovered = [p for p in gaps if p not in m]
    print(f"\nMeses sin dato de Ecuador desde {start_year}: {len(gaps)}")
    print(f"  cubiertos por el espejo : {len(covered)}")
    print(f"  sin cubrir              : {len(uncovered)} "
          f"{'→ ' + ', '.join(uncovered) if uncovered else ''}")


# Ejecutar:
#   python -m src.extract_comtrade            (≈ 1 + 1 llamada por año)
#   python -m src.extract_comtrade --mirror   (≈ 1 llamada por año)
if __name__ == "__main__":
    from src.logging_config import setup_logging

    parser = argparse.ArgumentParser(description="Extracción de UN Comtrade a bronze")
    parser.add_argument("--mirror", action="store_true",
                        help="estadísticas espejo (importaciones de otros países desde Ecuador)")
    args = parser.parse_args()

    setup_logging()
    if args.mirror:
        load_comtrade_mirror()
        print_coverage("M")
        print_gap_coverage()
    else:
        load_comtrade()
        print_coverage("X")