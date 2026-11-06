
from __future__ import annotations

import logging
import time

import pandas as pd
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from src.config import settings

# Logger del módulo: solo emite mensajes; NO decide a dónde van
logger = logging.getLogger(__name__)

# Esquemas de la arquitectura Medallion (+ ops para la bitácora del ETL)
SCHEMAS = ("ops", "bronze", "silver", "gold")

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
        read_sql("SELECT * FROM gold.dim_date WHERE year = :year", {"year": 2025})
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
    except SQLAlchemyError:
        # Registrar con traza completa y relanzar: el pipeline debe detenerse
        logger.exception("read_sql falló | params=%s", params)
        raise

    elapsed = time.perf_counter() - start
    logger.info("read_sql: %s filas en %.2f s", len(df), elapsed)
    return df


# Punto de entrada para pruebas: python -m src.db
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    if check_connection():
        # Prueba de read_sql con parámetro enlazado
        df = read_sql(
            """
            SELECT date_key, full_date, calendar_year, calendar_month,
                   month_name_es, ec_coastal_season
            FROM gold.dim_date
            WHERE calendar_year = :year
            ORDER BY date_key
            """,
            {"year": 2025},
        )
        print(df.head(3))
        print(f"Forma del DataFrame: {df.shape}")   # (filas, columnas)