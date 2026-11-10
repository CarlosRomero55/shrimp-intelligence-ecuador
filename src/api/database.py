"""
database.py — Conexión de SOLO LECTURA de la API (Fase 11).

Diferencias con src/db.py (la conexión del pipeline):
  - Usa el usuario api_reader (API_DB_USER / API_DB_PASSWORD del .env), que solo
    tiene SELECT sobre las 6 vistas gold.api_*. Aunque el código tuviera un error,
    la base no le deja escribir ni leer otras tablas (mínimo privilegio).
  - Solo existe una función de lectura, fetch_rows(), y únicamente sobre las vistas
    de la lista blanca VIEWS.

Seguridad contra inyección SQL:
  - Los NOMBRES (vista, columnas, orden) salen de este código, nunca del usuario.
  - Los VALORES que manda el usuario (year=2025, country=CHN) viajan como
    parámetros enlazados (:year), no se pegan dentro del texto del SQL.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from src.config import settings

logger = logging.getLogger(__name__)

# Lista blanca: nombre corto del endpoint → vista de gold
VIEWS = {
    "exports": "gold.api_exports_monthly",
    "kpis": "gold.api_kpis_annual",
    "prices": "gold.api_prices",
    "concentration": "gold.api_concentration",
    "ponds": "gold.api_pond_ranking",
    "reconciliation": "gold.api_reconciliation",
}

_engine: Engine | None = None


class ApiConfigError(RuntimeError):
    """Falta configuración de la API en el .env (se responde 503 con el motivo)."""


def get_engine() -> Engine:
    """Crea el motor de solo lectura la primera vez que se usa (no al importar)."""
    global _engine
    if _engine is None:
        try:
            user, password = settings.require_api_credentials()
        except RuntimeError as exc:
            raise ApiConfigError(str(exc)) from exc
        url = URL.create(
            drivername="mysql+pymysql",
            username=user,
            password=password,
            host=settings.db_host,
            port=int(settings.db_port),
            query={"charset": "utf8mb4"},
        )
        _engine = create_engine(
            url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,      # descarta conexiones muertas antes de usarlas
            pool_recycle=3600,
        )
        logger.info("Motor de la API creado: %s", url.render_as_string(hide_password=True))
    return _engine


def set_engine(engine: Engine | None) -> None:
    """Reemplaza el motor (las pruebas usan una base SQLite en memoria)."""
    global _engine
    _engine = engine


def ping() -> bool:
    """SELECT 1: ¿responde la base? (para /health)."""
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT 1")).scalar() == 1


def build_query(view_key: str, filters: dict[str, Any] | None = None,
                order_by: list[str] | None = None, limit: int | None = None,
                offset: int = 0) -> tuple[str, dict[str, Any]]:
    """
    Arma el SELECT y sus parámetros. Separada de fetch_rows para poder probarla.
      filters:  {columna: valor}; los valores None se ignoran (filtro no enviado).
      order_by: lista de "columna" o "columna DESC" (definida en main.py, nunca por el usuario).
    """
    if view_key not in VIEWS:
        raise ValueError(f"Vista no permitida: {view_key}")
    sql = f"SELECT * FROM {VIEWS[view_key]}"
    params: dict[str, Any] = {}
    conditions = []
    for column, value in (filters or {}).items():
        if value is None:
            continue
        if not column.isidentifier():                 # defensa extra: solo nombres simples
            raise ValueError(f"Columna no válida: {column}")
        conditions.append(f"{column} = :{column}")
        params[column] = value
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    if order_by:
        sql += " ORDER BY " + ", ".join(order_by)
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        params.update(limit=int(limit), offset=int(offset))
    return sql, params


def fetch_rows(view_key: str, filters: dict[str, Any] | None = None,
               order_by: list[str] | None = None, limit: int | None = None,
               offset: int = 0) -> list[dict[str, Any]]:
    """Ejecuta el SELECT y devuelve una lista de diccionarios (una fila = un dict)."""
    sql, params = build_query(view_key, filters, order_by, limit, offset)
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(row) for row in result.mappings()]
