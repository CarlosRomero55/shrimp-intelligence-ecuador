"""
db.py — Única puerta de entrada entre Python y MySQL.

Todos los scripts del pipeline (extracción, limpieza, conciliación, gold)
leen y escriben a través de este módulo. Así la conexión, el manejo de
errores y la forma de escribir datos son iguales en todo el proyecto.

Pasos: 3.1 get_engine() + check_connection() · 3.2 logging · 3.3 read_sql()
       3.4 write_df() (bronze) · 3.5 upsert() (silver/gold).
"""
from __future__ import annotations

import logging
import math
import time

import pandas as pd
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from src.config import settings

# Logger del módulo: solo emite mensajes; NO decide a dónde van
logger = logging.getLogger(__name__)

# Esquemas de la arquitectura Medallion (+ ops para la bitácora del ETL)
SCHEMAS = ("ops", "bronze", "silver", "gold")

# Esquemas donde se permite upsert (bronze es inmutable: solo write_df)
UPSERT_SCHEMAS = ("ops", "silver", "gold")

# Errores de base de datos que atrapamos al leer/escribir.
# pandas envuelve los de SQLAlchemy en pandas.errors.DatabaseError.
DB_ERRORS = (SQLAlchemyError, pd.errors.DatabaseError)

# Motor único del proceso. Empieza vacío y se crea en el primer uso.
_engine: Engine | None = None


def get_engine() -> Engine:
    """Devuelve el motor de SQLAlchemy y lo crea solo la primera vez."""
    global _engine
    if _engine is None:
        # URL.create escapa caracteres especiales de la contraseña (@, #, :)
        url = URL.create(
            drivername="mysql+pymysql",       # dialecto MySQL + driver PyMySQL
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=int(settings.db_port),
            query={"charset": "utf8mb4"},     # tildes, ñ y emojis sin errores
        )
        _engine = create_engine(
            url,
            pool_size=5,         # conexiones que el pool mantiene abiertas
            max_overflow=5,      # conexiones extra temporales en picos
            pool_pre_ping=True,  # verifica que la conexión siga viva antes de usarla
            pool_recycle=3600,   # renueva conexiones de más de 1 hora
        )
        # hide_password=True: la URL sale en el log con *** en vez de la clave
        logger.debug("Motor creado: %s", url.render_as_string(hide_password=True))
    return _engine


def check_connection() -> bool:
    """Prueba de vida: versión de MySQL, usuario y tablas visibles por esquema."""
    try:
        with get_engine().connect() as conn:
            version, user = conn.execute(
                text("SELECT VERSION(), CURRENT_USER()")
            ).one()

            # expanding=True convierte la lista en IN ('ops','bronze',...)
            # de forma segura (sin concatenar texto en el SQL)
            query = text(
                """
                SELECT table_schema, COUNT(*) AS n_tables
                FROM information_schema.tables
                WHERE table_schema IN :schemas
                GROUP BY table_schema
                ORDER BY table_schema
                """
            ).bindparams(bindparam("schemas", expanding=True))
            tables = conn.execute(query, {"schemas": list(SCHEMAS)}).all()

        # %s en vez de f-string: el texto solo se arma si el nivel se va a registrar
        logger.info("Conexión OK | MySQL %s | usuario: %s", version, user)
        visibles = {schema: n for schema, n in tables}
        for schema in SCHEMAS:
            n = visibles.get(schema, 0)
            if n == 0:
                # Sin tablas visibles = problema de permisos → WARNING
                logger.warning("Esquema %s: 0 tablas visibles (revisar GRANT)", schema)
            else:
                logger.info("Esquema %-7s: %s tablas visibles", schema, n)
        return True

    except OperationalError as exc:
        logger.error("Conexión FALLIDA: %s", exc.orig)
        logger.error("Pistas: 1045 = usuario/contraseña en .env | "
                     "2003 = MySQL apagado o host/puerto incorrecto")
        return False
    except SQLAlchemyError:
        # logger.exception = ERROR + traza completa del error en el archivo
        logger.exception("Error de SQLAlchemy al verificar la conexión")
        return False


def read_sql(query: str, params: dict | None = None) -> pd.DataFrame:
    """
    Ejecuta un SELECT y devuelve un DataFrame.

    Usar SIEMPRE parámetros enlazados, nunca f-strings:
        read_sql("SELECT * FROM gold.dim_date WHERE calendar_year = :year",
                 {"year": 2025})
    El driver envía el valor por separado del SQL, así un texto malicioso
    como "2025; DROP TABLE ..." se trata como dato y no como código.
    """
    start = time.perf_counter()
    try:
        with get_engine().connect() as conn:
            # text() permite los marcadores :nombre; pandas los llena con params.
            # Nota: pandas convierte DECIMAL a float por defecto (coerce_float=True),
            # suficiente para análisis; los cálculos de dinero se harán en SQL.
            df = pd.read_sql(text(query), conn, params=params or {})
    except DB_ERRORS:
        # Registrar con traza completa y relanzar: el pipeline debe detenerse
        logger.exception("read_sql falló | params=%s", params)
        raise

    elapsed = time.perf_counter() - start
    logger.info("read_sql: %s filas en %.2f s", len(df), elapsed)
    return df


def write_df(
    df: pd.DataFrame,
    schema: str,
    table: str,
    *,
    chunksize: int = 1000,
    dedupe_on: list[str] | None = None,
    conn: Connection | None = None,
) -> int:
    """
    Inserta un DataFrame en schema.table por lotes (solo INSERT / append).

    - Pensado para bronze: no actualiza ni borra (bronze no tiene esos permisos).
      Para silver/gold se usa upsert().
    - if_exists="append" está FIJO: no existe forma de hacer "replace" (DROP)
      desde aquí, ni por error.
    - dedupe_on: columnas que identifican una fila única (ej. ["record_hash"]);
      elimina duplicados DENTRO del lote antes de insertar.
    - conn: si se pasa, se usa esa transacción y NO se hace commit (lo decide
      quien llama). Si no, se abre una transacción propia: todo o nada.

    Devuelve el número de filas enviadas a insertar.
    """
    if df.empty:
        logger.warning("write_df: DataFrame vacío, nada que insertar en %s.%s",
                       schema, table)
        return 0

    if dedupe_on:
        before = len(df)
        df = df.drop_duplicates(subset=dedupe_on, keep="first")
        removed = before - len(df)
        if removed:
            logger.info("write_df: %s duplicado(s) eliminado(s) por %s",
                        removed, dedupe_on)

    n_batches = math.ceil(len(df) / chunksize)

    def _insert(connection: Connection) -> None:
        df.to_sql(
            name=table,
            con=connection,
            schema=schema,          # en MySQL, schema = base de datos
            if_exists="append",     # FIJO: nunca "replace" (DROP)
            index=False,            # no escribir el índice de pandas como columna
            chunksize=chunksize,    # filas por INSERT
            method="multi",         # INSERT ... VALUES (...), (...), ...
        )

    start = time.perf_counter()
    try:
        if conn is not None:
            # Transacción del que llama: no hacemos commit aquí
            _insert(conn)
        else:
            # Transacción propia: COMMIT al salir bien, ROLLBACK si hay error
            with get_engine().begin() as own_conn:
                _insert(own_conn)
    except DB_ERRORS:
        logger.exception("write_df falló en %s.%s (%s filas)", schema, table, len(df))
        raise

    elapsed = time.perf_counter() - start
    logger.info("write_df: %s filas → %s.%s en %s lote(s) | %.2f s",
                len(df), schema, table, n_batches, elapsed)
    return len(df)


def _table_columns(conn: Connection, schema: str, table: str) -> set[str]:
    """Devuelve los nombres reales de las columnas de schema.table (lista blanca)."""
    cols = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :s AND table_name = :t
            """
        ),
        {"s": schema, "t": table},
    ).scalars().all()
    if not cols:
        raise ValueError(f"La tabla {schema}.{table} no existe o el usuario no tiene permisos")
    return set(cols)


def upsert(
    df: pd.DataFrame,
    schema: str,
    table: str,
    key_cols: list[str],
    *,
    chunksize: int = 1000,
    conn: Connection | None = None,
) -> dict[str, int]:
    """
    Inserta filas nuevas y actualiza las existentes (silver/gold). Idempotente:
    ejecutarlo dos veces con los mismos datos no duplica nada.

    - key_cols: llave de NEGOCIO que identifica la fila (debe tener PK o UNIQUE
      en la tabla), ej. ["m49_region_code"]. No se actualizan; el resto sí.
    - No enviar llaves sustitutas auto_increment (ej. region_id): MySQL las asigna.
    - Los nombres de tabla/columna se validan contra information_schema antes
      de armar el SQL (no pueden ir como parámetros enlazados).
    - conn: igual que en write_df (transacción externa sin commit, o propia).

    Devuelve {"rows": n, "inserted": nuevas, "existing": ya existían}.
    """
    if schema not in UPSERT_SCHEMAS:
        raise ValueError(f"upsert no permitido en '{schema}'. "
                         "bronze es inmutable: usar write_df().")
    if df.empty:
        logger.warning("upsert: DataFrame vacío, nada que cargar en %s.%s", schema, table)
        return {"rows": 0, "inserted": 0, "existing": 0}

    cols = list(df.columns)
    missing_keys = [k for k in key_cols if k not in cols]
    if missing_keys:
        raise ValueError(f"upsert: faltan columnas llave en el DataFrame: {missing_keys}")
    update_cols = [c for c in cols if c not in key_cols]
    if not update_cols:
        raise ValueError("upsert: no hay columnas para actualizar (solo llaves)")

    # NaN de pandas → None, que el driver envía como NULL
    records = df.astype(object).where(df.notna(), None).to_dict("records")

    def _run(connection: Connection) -> dict[str, int]:
        # 1) Lista blanca: toda columna del DataFrame debe existir en la tabla
        unknown = set(cols) - _table_columns(connection, schema, table)
        if unknown:
            raise ValueError(f"upsert: columnas inexistentes en {schema}.{table}: "
                             f"{sorted(unknown)}")

        # 2) Partes fijas del SQL (nombres ya validados, entre backticks)
        target = f"`{schema}`.`{table}`"
        col_sql = ", ".join(f"`{c}`" for c in cols)
        update_sql = ", ".join(f"`{c}` = new_row.`{c}`" for c in update_cols)
        count_sql = text(f"SELECT COUNT(*) FROM {target}")

        before = connection.execute(count_sql).scalar_one()

        # 3) Un INSERT multi-fila por lote; cada valor va como parámetro enlazado
        for start_idx in range(0, len(records), chunksize):
            chunk = records[start_idx:start_idx + chunksize]
            placeholders, params = [], {}
            for j, rec in enumerate(chunk):
                placeholders.append("(" + ", ".join(f":{c}_{j}" for c in cols) + ")")
                for c in cols:
                    params[f"{c}_{j}"] = rec[c]
            stmt = text(
                f"INSERT INTO {target} ({col_sql}) VALUES {', '.join(placeholders)} "
                f"AS new_row ON DUPLICATE KEY UPDATE {update_sql}"
            )
            connection.execute(stmt, params)

        after = connection.execute(count_sql).scalar_one()
        inserted = after - before
        return {"rows": len(records), "inserted": inserted,
                "existing": len(records) - inserted}

    start = time.perf_counter()
    try:
        if conn is not None:
            result = _run(conn)
        else:
            with get_engine().begin() as own_conn:
                result = _run(own_conn)
    except DB_ERRORS:
        logger.exception("upsert falló en %s.%s (%s filas)", schema, table, len(records))
        raise

    elapsed = time.perf_counter() - start
    logger.info("upsert: %s filas → %s.%s | nuevas: %s | ya existían: %s | %.2f s",
                result["rows"], schema, table, result["inserted"],
                result["existing"], elapsed)
    return result


def _test_write_df() -> None:
    """
    Prueba de write_df SIN dejar rastro: inserta dentro de una transacción,
    cuenta las filas y hace ROLLBACK (bronze no permite DELETE).
    """
    import hashlib
    import uuid
    from datetime import datetime

    # 3 filas con problemas de calidad típicos (así llegarán desde la simulación)
    rows = [
        {"farm_name": "TEST Finca Guayas", "province": "Guayas", "pond_code": "GY-P01",
         "pond_area_ha": "8.5", "cycle_code": "GY-P01-2025-01",
         "stocking_date": "2025-01-10", "harvest_date": "15/04/2025",
         "size_grade": "20/30", "harvested_qty": "12500", "harvested_unit": "lb",
         "fcr": "1.35", "destination_market": "EEUU"},
        {"farm_name": "TEST Finca El Oro", "province": "El Oro", "pond_code": "EO-P03",
         "pond_area_ha": "6,2", "cycle_code": "EO-P03-2025-01",
         "stocking_date": "2025-02-03", "harvest_date": "2025-05-28",
         "size_grade": "20-30", "harvested_qty": "5600", "harvested_unit": "kg",
         "fcr": "1.42", "destination_market": "Estados Unidos"},
        {"farm_name": "TEST Finca Santa Elena", "province": "Santa Elena",
         "pond_code": "SE-P02", "pond_area_ha": "10", "cycle_code": "SE-P02-2025-01",
         "stocking_date": "03/03/2025", "harvest_date": "2025-06-20",
         "size_grade": "30 40", "harvested_qty": "18000", "harvested_unit": "lb",
         "fcr": "15", "destination_market": "USA"},
    ]
    df = pd.DataFrame(rows)

    # Huella digital del CONTENIDO (sin columnas de auditoría): el mismo dato
    # extraído dos veces produce el mismo hash → permite detectar duplicados
    df["record_hash"] = df.apply(
        lambda r: hashlib.sha256("|".join(map(str, r.values)).encode("utf-8")).hexdigest(),
        axis=1,
    )

    # Duplicado exacto de la primera fila: 4 filas entran, 3 deben insertarse
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)

    # Columnas de auditoría
    # uuid4().hex = 32 caracteres sin guiones → "test-" + 32 = 37 (cabe en varchar(40))
    batch_id = f"test-{uuid.uuid4().hex}"
    df["source_name"] = "test_write_df"
    df["batch_id"] = batch_id
    df["extracted_at"] = datetime.now()

    count_sql = text("SELECT COUNT(*) FROM bronze.farm_raw WHERE batch_id = :b")

    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            write_df(df, "bronze", "farm_raw", dedupe_on=["record_hash"], conn=conn)
            n_inside = conn.execute(count_sql, {"b": batch_id}).scalar_one()
        finally:
            trans.rollback()   # pase lo que pase, no queda nada en bronze
        n_after = conn.execute(count_sql, {"b": batch_id}).scalar_one()

    print(f"Filas dentro de la transacción: {n_inside} | después del ROLLBACK: {n_after}")


def _test_upsert() -> None:
    """
    Prueba de upsert SIN dejar rastro (transacción + ROLLBACK) en silver.regions:
    1) carga 2 regiones nuevas  2) re-carga 1 editada + 1 nueva  → 3 filas, no 4.
    Códigos 991..993: cumplen el CHECK (3 dígitos) y no existen en el estándar M49.
    """
    test_codes = ("991", "992", "993")
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
    count_sql = text(
        "SELECT COUNT(*) FROM silver.regions WHERE m49_region_code IN :codes"
    ).bindparams(bindparam("codes", expanding=True))
    name_sql = text("SELECT region_name_es FROM silver.regions WHERE m49_region_code = '992'")

    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            upsert(first, "silver", "regions", key_cols=["m49_region_code"], conn=conn)
            upsert(second, "silver", "regions", key_cols=["m49_region_code"], conn=conn)
            n_inside = conn.execute(count_sql, {"codes": list(test_codes)}).scalar_one()
            name_992 = conn.execute(name_sql).scalar_one()
        finally:
            trans.rollback()   # nada queda en silver
        n_after = conn.execute(count_sql, {"codes": list(test_codes)}).scalar_one()

    print(f"Filas de prueba: {n_inside} | nombre de 992: '{name_992}' "
          f"| después del ROLLBACK: {n_after}")


# Punto de entrada para pruebas: python -m src.db
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    if check_connection():
        # Prueba de read_sql con parámetro enlazado
        df_dates = read_sql(
            """
            SELECT date_key, full_date, calendar_year, calendar_month,
                   month_name_es, ec_coastal_season
            FROM gold.dim_date
            WHERE calendar_year = :year
            ORDER BY date_key
            """,
            {"year": 2025},
        )
        print(f"dim_date 2025: {df_dates.shape}")

        # Prueba de write_df con ROLLBACK (bronze)
        _test_write_df()

        # Prueba de upsert con ROLLBACK (silver)
        _test_upsert()