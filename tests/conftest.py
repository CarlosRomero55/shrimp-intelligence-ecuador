"""
conftest.py — Configuración compartida de pytest (Fase 6, Paso 6.2).

pytest lee este archivo automáticamente antes de correr las pruebas.

1. Registra la marca "db": pruebas que necesitan MySQL encendido y con datos cargados.
2. Marca como "db" TODAS las pruebas de los archivos que usan la base (no hay que
   escribir @pytest.mark.db en cada una).
3. Si MySQL no responde, esas pruebas se SALTAN con un aviso en vez de fallar.

Uso:
    python -m pytest -q                # todo
    python -m pytest -m db -q          # solo las que usan MySQL
    python -m pytest -m "not db" -q    # sin MySQL (lo que correría en GitHub Actions)
"""
from __future__ import annotations

import pytest

# Archivos cuyas pruebas consultan MySQL
DB_TEST_FILES = {"test_db.py", "test_sql_scripts.py", "test_silver_data.py"}


def pytest_configure(config):
    config.addinivalue_line("markers", "db: necesita MySQL encendido y con datos cargados")


def _mysql_available() -> tuple[bool, str]:
    """Intenta un SELECT 1. Cualquier error (MySQL apagado, .env incompleto) → no disponible."""
    try:
        from sqlalchemy import text

        from src.db import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:                       # noqa: BLE001 — solo para decidir el skip
        return False, type(exc).__name__


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Primero: marcar como 'db' las pruebas de los archivos que usan la base.
    tryfirst=True asegura que la marca exista antes de que -m "not db" filtre."""
    for item in items:
        if item.path.name in DB_TEST_FILES:
            item.add_marker(pytest.mark.db)


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session):
    """Después del filtro: si quedan pruebas 'db' y MySQL no responde, se saltan."""
    db_items = [i for i in session.items if i.get_closest_marker("db")]
    if not db_items:
        return
    ok, reason = _mysql_available()
    if not ok:
        skip = pytest.mark.skip(reason=f"MySQL no disponible ({reason})")
        for item in db_items:
            item.add_marker(skip)