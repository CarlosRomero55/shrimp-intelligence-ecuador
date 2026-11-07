
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import pandas as pd

BATCH_ID_MAX_LEN = 40   # tamaño de batch_id en todas las tablas bronze


def make_batch_id(source: str) -> str:
    """Identificador de lote: fuente + fecha/hora + 8 caracteres aleatorios."""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    batch_id = f"{source}-{stamp}-{uuid.uuid4().hex[:8]}"
    if len(batch_id) > BATCH_ID_MAX_LEN:
        # Falla aquí, con mensaje claro, y no en MySQL con "Data too long"
        raise ValueError(f"batch_id de {len(batch_id)} caracteres (máx. {BATCH_ID_MAX_LEN}): "
                         f"acortar el nombre de la fuente '{source}'")
    return batch_id


def compute_record_hash(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """
    Huella SHA-256 de las columnas de CONTENIDO (nunca las de auditoría):
    el mismo dato extraído dos veces produce la misma huella.
    Los vacíos se representan como texto vacío para que la huella sea estable.
    """
    content = df[cols].astype(object).where(df[cols].notna(), "")
    joined = content.astype(str).agg("|".join, axis=1)
    return joined.map(lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())