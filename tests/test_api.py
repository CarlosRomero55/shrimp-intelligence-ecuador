"""
test_api.py — Pruebas de la API REST (Fase 11, Paso 11.5).

Dos grupos, igual que en la Fase 6:

1. UNITARIAS (sin MySQL): la API se conecta a una base SQLite en memoria con tablas
   que imitan las vistas gold.api_*. Prueban el comportamiento de la API:
   validación (422), 404, 503, paginación, orden y protección contra inyección SQL.

2. DE DATOS (marca "db", MySQL real con el usuario api_reader): prueban que lo que
   devuelve la API cuadra con el BCE y que api_reader de verdad es de solo lectura.
   Se saltan si MySQL no responde o si faltan API_DB_USER / API_DB_PASSWORD en el .env.

Ejecutar:
    python -m pytest tests/test_api.py -v
    python -m pytest tests/test_api.py -m "not db" -v     # solo unitarias
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import StaticPool

from src.api import database
from src.api.main import app

client = TestClient(app)

# Tablas SQLite que imitan las vistas de gold (mismas columnas, pocas filas)
FAKE_VIEWS = [
    """CREATE TABLE gold.api_kpis_annual (year INT, months INT, value_usd REAL,
       value_yoy_pct REAL, weight_lb_identified REAL, usd_per_lb REAL, price_yoy_pct REAL,
       price_source TEXT, price_yoy_comparable INT, country_coverage_pct REAL,
       weight_coverage_pct REAL)""",
    """INSERT INTO gold.api_kpis_annual VALUES
       (2024, 12, 6991738465.09, -3.0, 2580000000, 2.593, 1.9, 'MIXTA', 0, 95.7, 95.0),
       (2025, 12, 8401253486.31, 20.2, 1297962296, 3.035, 17.0, 'COMTRADE_MIRROR', 0, 46.9, 46.9)""",
    """CREATE TABLE gold.api_exports_monthly (period TEXT, year INT, month INT, iso3 TEXT,
       country TEXT, market_group TEXT, source TEXT, valuation_basis TEXT, value_usd REAL,
       weight_lb REAL)""",
    """INSERT INTO gold.api_exports_monthly VALUES
       ('2024-01', 2024, 1, 'CHN', 'China', 'China', 'UN_COMTRADE', 'FOB', 250000000, 110000000),
       ('2024-01', 2024, 1, 'USA', 'Estados Unidos', 'EE. UU.', 'UN_COMTRADE', 'FOB', 90000000, 27000000),
       ('2024-02', 2024, 2, 'CHN', 'China', 'China', 'UN_COMTRADE', 'FOB', 230000000, 100000000)""",
    """CREATE TABLE gold.api_concentration (year INT, hhi REAL, level TEXT, top_country TEXT,
       top_country_pct REAL, top3_pct REAL, countries INT, coverage_pct REAL, is_comparable INT)""",
    """INSERT INTO gold.api_concentration VALUES
       (2024, 2800, 'Muy concentrado', 'China', 45.0, 74.0, 60, 95.7, 1),
       (2025, 1900, 'Moderado', 'Estados Unidos', 30.0, 60.0, 55, 46.9, 0)""",
    """CREATE TABLE gold.api_pond_ranking (farm_code TEXT, farm_name TEXT, pond_code TEXT,
       area_band TEXT, cycles INT, revenue_usd REAL, profit_usd REAL, margin_pct REAL,
       margin_quartile INT, cost_per_lb REAL, price_per_lb REAL, fcr REAL, fcr_rank INT,
       survival_pct REAL, is_simulated INT)""",
    """INSERT INTO gold.api_pond_ranking VALUES
       ('ADG-SE', 'Sede SE', 'SE-P06', '5-10 ha', 11, 857300, 410400, 47.9, 1, 0.957, 1.835, 1.264, 8, 73.1, 1),
       ('ADG-GY', 'Sede GY', 'GY-P07', '> 10 ha', 11, 1301000, 546200, 42.0, 3, 1.010, 1.741, 1.241, 1, 69.5, 1),
       ('ADG-GY', 'Sede GY', 'GY-P15', '> 10 ha', 11, 1329600, 491900, 37.0, 4, 1.037, 1.646, 1.325, 34, 69.8, 1)""",
]


def sqlite_engine(with_tables: bool = True):
    """Base SQLite en memoria con un esquema 'gold' (ATTACH) para que 'gold.api_x' funcione."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach_gold(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS gold")

    if with_tables:
        with engine.begin() as conn:
            for sql in FAKE_VIEWS:
                conn.execute(text(sql))
    return engine


@pytest.fixture
def fake_db():
    """La API lee de SQLite durante la prueba; al terminar, vuelve a su motor normal."""
    database.set_engine(sqlite_engine())
    yield
    database.set_engine(None)


# ================================================================== unitarias (sin MySQL)
def test_health(fake_db):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_kpis_filtra_por_anio(fake_db):
    assert len(client.get("/kpis").json()) == 2
    rows = client.get("/kpis", params={"year": 2025}).json()
    assert [r["year"] for r in rows] == [2025]
    assert rows[0]["value_usd"] == pytest.approx(8401253486.31)
    assert rows[0]["price_yoy_comparable"] is False          # 0 de la base → false en JSON


@pytest.mark.parametrize("year", ["abc", "1990", "2101"])
def test_anio_invalido_da_422_sin_consultar(fake_db, year):
    assert client.get("/kpis", params={"year": year}).status_code == 422


def test_filtro_sin_datos_da_404(fake_db):
    response = client.get("/kpis", params={"year": 2030})
    assert response.status_code == 404
    assert "2030" in response.json()["detail"]


def test_pais_en_minusculas_se_normaliza(fake_db):
    rows = client.get("/exports", params={"country": "chn", "year": 2024}).json()
    assert {r["iso3"] for r in rows} == {"CHN"}
    assert len(rows) == 2


@pytest.mark.parametrize("country", ["CHN' OR '1'='1", "CHINA", "C1N", "CH"])
def test_pais_con_formato_invalido_da_422(fake_db, country):
    """Un intento de inyección SQL ni siquiera llega a la base: el patrón ^[A-Za-z]{3}$ lo rechaza."""
    assert client.get("/exports", params={"country": country}).status_code == 422


def test_paginacion_de_exports(fake_db):
    first = client.get("/exports", params={"limit": 2}).json()
    rest = client.get("/exports", params={"limit": 2, "offset": 2}).json()
    beyond = client.get("/exports", params={"limit": 2, "offset": 50})
    assert len(first) == 2 and len(rest) == 1
    assert beyond.status_code == 200 and beyond.json() == []   # página vacía, no 404


def test_ranking_por_margen_y_por_fcr(fake_db):
    by_margin = client.get("/ponds/ranking").json()
    by_fcr = client.get("/ponds/ranking", params={"order_by": "fcr", "limit": 1}).json()
    assert [r["pond_code"] for r in by_margin] == ["SE-P06", "GY-P07", "GY-P15"]
    assert [r["pond_code"] for r in by_fcr] == ["GY-P07"]
    assert all(r["is_simulated"] is True for r in by_margin)


def test_ranking_filtra_finca_y_valida_parametros(fake_db):
    rows = client.get("/ponds/ranking", params={"farm": "adg-gy"}).json()
    assert {r["farm_code"] for r in rows} == {"ADG-GY"}
    assert client.get("/ponds/ranking", params={"order_by": "precio"}).status_code == 422
    assert client.get("/ponds/ranking", params={"limit": 0}).status_code == 422


def test_concentracion_solo_comparables(fake_db):
    years = [r["year"] for r in client.get("/concentration",
                                           params={"comparable_only": True}).json()]
    assert years == [2024]


def test_build_query_usa_parametros_y_no_pega_valores():
    sql, params = database.build_query("exports", {"iso3": "CHN", "year": None},
                                       ["period"], limit=10)
    assert "CHN" not in sql                                   # el valor NO va en el texto
    assert ":iso3" in sql and params["iso3"] == "CHN"
    assert "year" not in params                               # filtro None = no se envió


def test_build_query_solo_acepta_vistas_y_columnas_validas():
    with pytest.raises(ValueError):
        database.build_query("silver_tables")                 # fuera de la lista blanca
    with pytest.raises(ValueError):
        database.build_query("kpis", {"year; DROP TABLE x": 2025})


def test_sin_credenciales_da_503(monkeypatch):
    database.set_engine(None)
    monkeypatch.setattr(database, "settings",
                        dataclasses.replace(database.settings, api_db_user=None))
    response = client.get("/kpis")
    assert response.status_code == 503
    assert "API_DB_USER" in response.json()["detail"]


def test_error_de_base_da_503_sin_detalles():
    """Si la consulta falla, el cliente recibe un mensaje corto; el detalle va al log."""
    database.set_engine(sqlite_engine(with_tables=False))    # no existen las vistas
    try:
        response = client.get("/kpis")
    finally:
        database.set_engine(None)
    assert response.status_code == 503
    assert response.json() == {"detail": "Base de datos no disponible"}


def test_documentacion_openapi():
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/health", "/kpis", "/exports", "/prices", "/concentration",
                          "/ponds/ranking", "/reconciliation"}


# ================================================================== de datos (MySQL real)
@pytest.fixture
def real_api():
    """Motor real con api_reader. Se salta si faltan las credenciales de la API en el .env."""
    database.set_engine(None)
    try:
        database.get_engine()
    except database.ApiConfigError as exc:
        pytest.skip(str(exc))
    yield
    database.set_engine(None)


@pytest.mark.db
def test_kpis_igual_al_bce_cada_anio(real_api):
    from src.db import read_sql                               # usuario del pipeline (lee silver)

    bce = read_sql("""
        SELECT YEAR(period_date) AS year, SUM(reference_value_usd) AS bce_usd
        FROM (SELECT DISTINCT period_date, reference_value_usd
              FROM silver.source_reconciliation
              WHERE comparison_level = 'total') AS m
        GROUP BY YEAR(period_date)
    """)
    api = {r["year"]: r["value_usd"] for r in client.get("/kpis").json()}
    assert set(api) == set(bce["year"].astype(int))
    for year, bce_usd in zip(bce["year"].astype(int), bce["bce_usd"].astype(float)):
        assert api[year] == pytest.approx(bce_usd, abs=1.0), f"{year}: API ≠ BCE"


@pytest.mark.db
def test_exports_suma_el_total_del_anio(real_api):
    year = 2024
    rows, offset = [], 0
    while True:                                               # recorre todas las páginas
        page = client.get("/exports", params={"year": year, "limit": 5000,
                                              "offset": offset}).json()
        rows += page
        if len(page) < 5000:
            break
        offset += 5000
    kpi = client.get("/kpis", params={"year": year}).json()[0]
    assert sum(r["value_usd"] for r in rows) == pytest.approx(kpi["value_usd"], abs=1.0)


@pytest.mark.db
def test_concentracion_comparable_excluye_anios_sin_china(real_api):
    rows = client.get("/concentration", params={"comparable_only": True}).json()
    assert rows, "debería haber años comparables"
    assert all(r["coverage_pct"] >= 80 for r in rows)
    assert 2025 not in {r["year"] for r in rows}             # 2025: China no reportó


@pytest.mark.db
def test_reconciliation_por_pais_cumple_minimo_de_meses(real_api):
    rows = client.get("/reconciliation", params={"level": "country"}).json()
    assert rows and all(r["months"] >= 12 and r["year"] is None for r in rows)
    assert all(r["classification"] for r in rows)


@pytest.mark.db
def test_api_reader_no_puede_leer_silver(real_api):
    with pytest.raises(DBAPIError) as error:
        with database.get_engine().connect() as conn:
            conn.execute(text("SELECT COUNT(*) FROM silver.source_reconciliation"))
    assert "1142" in str(error.value)                         # 1142 = comando denegado


@pytest.mark.db
def test_api_reader_no_puede_borrar(real_api):
    """WHERE 1 = 0: aunque tuviera permiso, no borraría nada. MySQL rechaza por permisos."""
    with pytest.raises(DBAPIError) as error:
        with database.get_engine().begin() as conn:
            conn.execute(text("DELETE FROM gold.fact_exports WHERE 1 = 0"))
    assert "1142" in str(error.value)
