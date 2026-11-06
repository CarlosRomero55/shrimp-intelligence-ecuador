
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.audit import compute_record_hash, make_batch_id
from src.config import ROOT_DIR
from src.db import read_sql, write_df

logger = logging.getLogger(__name__)

RAW_DIR = ROOT_DIR / "data" / "raw" / "bce"
SOURCE_NAME = "BCE"
HASH_COLS = ["table_code", "series_name", "period_label", "value_raw"]


def find_latest_file(table_code: str = "3.1.2") -> Path:
    """Devuelve el Excel más reciente del cuadro (el nombre trae fecha y hora)."""
    files = sorted(RAW_DIR.glob(f"BCEData-{table_code}-*.xlsx"))
    if not files:
        raise FileNotFoundError(
            f"No hay archivos BCEData-{table_code}-*.xlsx en {RAW_DIR.relative_to(ROOT_DIR)}. "
            "Descargar el cuadro desde BCEData y guardarlo ahí sin renombrar."
        )
    return files[-1]


def parse_bce_table(path: Path) -> tuple[pd.DataFrame, dict]:
    """
    Lee un cuadro BCEData (formato ancho: una columna por mes) y lo devuelve en
    formato largo: una fila por serie y mes. Todos los valores quedan como TEXTO.
    """
    raw = pd.read_excel(path, header=None, dtype=str, engine="openpyxl")
    col0 = raw[0].fillna("").str.strip()

    # --- Metadatos del encabezado del cuadro
    title = next(t for t in col0 if re.match(r"^\d+(\.\d+)+\s", t))      # '3.1.2 EXPORTACIONES ...'
    unit_line = next((t for t in col0 if t.startswith("Unidad del cuadro:")), "")
    date_line = next((t for t in col0 if t.startswith("Fecha de consulta:")), "")
    date_match = re.search(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2})", date_line)
    if not date_match:
        raise ValueError("No se encontró 'Fecha de consulta' en el cuadro")
    meta = {
        "table_code": title.split()[0],
        "table_title": title,
        "table_unit": unit_line.split(":", 1)[1].strip() if unit_line else None,
        "extracted_at": datetime.strptime(date_match.group(1), "%d/%m/%Y %H:%M"),
    }

    # --- Fila de encabezados: la que dice 'Serie' en la primera columna
    header_rows = col0[col0 == "Serie"].index
    if header_rows.empty:
        raise ValueError("No se encontró la fila 'Serie' (¿cambió el formato del cuadro?)")
    header_idx = header_rows[0]
    period_cols = raw.loc[header_idx, 1:].dropna().index        # columnas con un mes
    periods = raw.loc[header_idx, period_cols].str.strip().tolist()

    # --- Recorrer las series. Filas sin números = títulos de sección (series_group)
    records, group, order = [], None, 0
    for i in range(header_idx + 1, len(raw)):
        name = col0[i]
        if not name:
            continue
        values = raw.loc[i, period_cols]
        if values.isna().all():
            group = name                    # ej. 'Tradicionales'
            continue
        order += 1
        for label, val in zip(periods, values):
            records.append({
                "series_order": order,
                "series_group": group,
                "series_name": name,
                "period_label": label,
                "value_raw": None if pd.isna(val) else str(val).strip(),
            })

    df = pd.DataFrame(records)
    df.insert(0, "table_code", meta["table_code"])
    df.insert(1, "table_unit", meta["table_unit"])
    logger.info("BCE %s: %s series × %s meses (%s – %s)",
                meta["table_code"], order, len(periods), periods[0], periods[-1])
    return df, meta


def load_bce_table(path: Path) -> str:
    """Lee el cuadro, agrega auditoría y carga a bronze SOLO las filas nuevas.
    Devuelve el batch_id usado."""
    df, meta = parse_bce_table(path)

    batch_id = make_batch_id(f"bce-{meta['table_code']}")
    df["source_name"] = SOURCE_NAME
    df["batch_id"] = batch_id
    df["extracted_at"] = meta["extracted_at"]
    df["source_file"] = path.name
    df["record_hash"] = compute_record_hash(df, HASH_COLS)

    # Idempotencia: descartar huellas que ya están en bronze
    existing = read_sql(
        "SELECT record_hash FROM bronze.bce_raw WHERE table_code = :t",
        {"t": meta["table_code"]},
    )
    new = df[~df["record_hash"].isin(existing["record_hash"])]
    skipped = len(df) - len(new)
    if skipped:
        logger.info("BCE: %s filas ya existían en bronze (sin cambios), se omiten", skipped)

    if new.empty:
        logger.info("BCE: no hay filas nuevas que cargar")
    else:
        write_df(new, "bronze", "bce_raw", dedupe_on=["record_hash"])
    return batch_id


def _to_number(text: str) -> float:
    """'596.248,67089' → 596248.67089 (solo para la verificación; silver hará la limpieza)."""
    return float(text.replace(".", "").replace(",", "."))


# Ejecutar: python -m src.extract_bce
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    path = find_latest_file("3.1.2")
    logger.info("Archivo: %s", path.relative_to(ROOT_DIR))
    load_bce_table(path)

    # Verificación contra la BASE (no contra el DataFrame): Camarón 2025
    check = read_sql(
        """
        SELECT period_label, value_raw
        FROM bronze.bce_raw
        WHERE table_code = '3.1.2' AND series_name = 'Camarón' AND period_label LIKE :p
        """,
        {"p": "% 2025"},
    )
    total_musd = check["value_raw"].map(_to_number).sum() / 1000   # miles → millones
    print(f"\nFilas totales en bronze.bce_raw : "
          f"{read_sql('SELECT COUNT(*) AS n FROM bronze.bce_raw')['n'].iloc[0]:,}")
    print(f"Camarón 2025 (verificación)    : {len(check)} meses | "
          f"USD {total_musd:,.1f} millones (oficial BCE: 8,401.3)")