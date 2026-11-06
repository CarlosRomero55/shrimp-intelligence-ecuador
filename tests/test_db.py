
import hashlib
import logging
import uuid
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import bindparam, text

from src.db import DB_ERRORS, check_connection, get_engine, read_sql, upsert, write_df

# Códigos M49 de prueba: 3 dígitos (cumplen el CHECK) y no existen en el estándar
TEST_M49_CODES = ["991", "992", "993"]


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def conn():
    """Conexión con transacción abierta. Al terminar la prueba: ROLLBACK siempre."""
    with get_engine().connect() as connection:
        trans = connection.begin()
        try:
            yield connection          # aquí corre la prueba
        finally:
            trans.rollback()          # pase lo que pase, la base queda igual


# ---------------------------------------------------------------- conexión y lectura
def test_check_connection():
    """La base responde y el usuario ve los 4 esquemas."""
    assert check_connection() is True


def test_read_sql_bound_param():
    """read_sql con parámetro enlazado: 2025 tiene 365 días en dim_date."""
    df = read_sql(
        "SELECT date_key FROM gold.dim_date WHERE calendar_year = :year",
        {"year": 2025},
    )
    assert len(df) == 365


def test_read_sql_error_is_logged(caplog):
    """Un SQL inválido debe fallar (no seguir en silencio) Y quedar en el log."""
    with caplog.at_level(logging.ERROR):
        with pytest.raises(DB_ERRORS):
            read_sql("SELECT columna_que_no_existe FROM gold.dim_date")
    assert "read_sql falló" in caplog.text


# ---------------------------------------------------------------- escritura en bronze
def test_write_df_dedupe_and_rollback(conn):
    """write_df elimina duplicados del lote: entran 3 filas, se insertan 2."""
    df = pd.DataFrame([
        {"farm_name": "TEST Finca A", "pond_code": "P01", "size_grade": "20/30"},
        {"farm_name": "TEST Finca B", "pond_code": "P02", "size_grade": "20-30"},
    ])
    df["record_hash"] = [
        hashlib.sha256("|".join(map(str, row)).encode("utf-8")).hexdigest()
        for row in df.itertuples(index=False)
    ]
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)   # duplicado exacto

    batch_id = f"test-{uuid.uuid4().hex}"                   # 37 caracteres
    df["source_name"] = "pytest"
    df["batch_id"] = batch_id
    df["extracted_at"] = datetime.now()

    n_sent = write_df(df, "bronze", "farm_raw", dedupe_on=["record_hash"], conn=conn)

    n_db = conn.execute(
        text("SELECT COUNT(*) FROM bronze.farm_raw WHERE batch_id = :b"),
        {"b": batch_id},
    ).scalar_one()
    assert n_sent == 2
    assert n_db == 2


# ---------------------------------------------------------------- upsert en silver
def test_upsert_is_idempotent(conn):
    """Carga, re-carga con cambios y re-carga idéntica: 3 filas, nunca duplicados."""
    first = pd.DataFrame([
        {"m49_region_code": "991", "region_name_es": "TEST Región A",
         "region_name_en": "TEST Region A"},
        {"m49_region_code": "992", "region_name_es": "TEST Región B",
         "region_name_en": "TEST Region B"},
    ])
    second = pd.DataFrame([
        {"m49_region_code": "992", "region_name_es": "TEST Región B (editada)",
         "region_name_en": "TEST Region B (edited)"},
        {"m49_region_code": "993", "region_name_es": "TEST Región C",
         "region_name_en": "TEST Region C"},
    ])
    keys = ["m49_region_code"]

    r1 = upsert(first, "silver", "regions", key_cols=keys, conn=conn)
    r2 = upsert(second, "silver", "regions", key_cols=keys, conn=conn)
    r3 = upsert(second, "silver", "regions", key_cols=keys, conn=conn)  # misma carga otra vez

    assert r1 == {"rows": 2, "inserted": 2, "existing": 0}
    assert r2 == {"rows": 2, "inserted": 1, "existing": 1}
    assert r3 == {"rows": 2, "inserted": 0, "existing": 2}   # idempotencia

    count_sql = text(
        "SELECT COUNT(*) FROM silver.regions WHERE m49_region_code IN :codes"
    ).bindparams(bindparam("codes", expanding=True))
    assert conn.execute(count_sql, {"codes": TEST_M49_CODES}).scalar_one() == 3

    name = conn.execute(
        text("SELECT region_name_es FROM silver.regions WHERE m49_region_code = '992'")
    ).scalar_one()
    assert name == "TEST Región B (editada)"


def test_upsert_rejects_bronze():
    """bronze es inmutable: upsert debe negarse antes de tocar la base."""
    df = pd.DataFrame([{"record_hash": "x", "farm_name": "TEST"}])
    with pytest.raises(ValueError, match="bronze es inmutable"):
        upsert(df, "bronze", "farm_raw", key_cols=["record_hash"])


def test_upsert_rejects_unknown_column(conn):
    """Una columna que no existe en la tabla debe detener la carga con un error claro."""
    df = pd.DataFrame([{
        "m49_region_code": "991",
        "region_name_es": "TEST",
        "region_name_en": "TEST",
        "columna_falsa": 1,
    }])
    with pytest.raises(ValueError, match="columna_falsa"):
        upsert(df, "silver", "regions", key_cols=["m49_region_code"], conn=conn)