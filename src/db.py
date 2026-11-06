"""
db.py — Única puerta de entrada entre Python y MySQL.

Todos los scripts del pipeline (extracción, limpieza, conciliación, gold)
leen y escriben a través de este módulo. Así la conexión, el manejo de
errores y la forma de escribir datos son iguales en todo el proyecto.

Paso 3.1: get_engine() + check_connection().
"""
from __future__ import annotations

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError


from src.config import settings

# Esquemas de la arquitectura Medallion (+ ops para la bitácora del ETL)
SCHEMAS = ("ops", "bronze", "silver", "gold")

# Motor único del proceso. Empieza vacío y se crea en el primer uso.
_engine: Engine | None = None


def get_engine() -> Engine:
    """Devuelve el motor de SQLAlchemy y lo crea solo la primera vez."""
    global _engine
    if _engine is None:
        # URL.create escapa caracteres especiales de la contraseña (@, #, :)
        # que romperían una URL armada a mano con f-strings.
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
                                 # (evita "MySQL server has gone away")
        )
    return _engine


def check_connection() -> bool:
    """Prueba de vida: versión de MySQL, usuario y tablas visibles por esquema."""
    try:
        # "with" devuelve la conexión al pool al terminar, aunque haya error
        with get_engine().connect() as conn:
            version, user = conn.execute(
                text("SELECT VERSION(), CURRENT_USER()")
            ).one()

            # expanding=True convierte la tupla en IN ('ops','bronze',...)
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

        print(f"Conexión OK | MySQL {version} | usuario: {user}")
        visibles = {schema: n for schema, n in tables}
        for schema in SCHEMAS:
            # Si un esquema sale 0, el usuario no tiene permisos sobre él
            print(f"  - {schema:<7}: {visibles.get(schema, 0)} tablas visibles")
        return True

    except OperationalError as exc:
        # Errores de red o credenciales. El mensaje de PyMySQL no incluye
        # la contraseña, así que es seguro mostrarlo.
        print(f"Conexión FALLIDA: {exc.orig}")
        print("Pistas: 1045 = usuario/contraseña en .env | "
              "2003 = MySQL apagado o host/puerto incorrecto")
        return False
    except SQLAlchemyError as exc:
        print(f"Error de SQLAlchemy: {exc}")
        return False


# Permite probar el módulo directamente con: python -m src.db
if __name__ == "__main__":
    check_connection()