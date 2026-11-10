"""
main.py — API REST de Shrimp Intelligence Ecuador (Fase 11).

Expone los datos de gold (vistas gold.api_*) en JSON, de solo lectura.

Ejecutar (desde la raíz del proyecto, con el .venv activo):
    uvicorn src.api.main:app --reload
Documentación interactiva (Swagger):  http://127.0.0.1:8000/docs
Documentación alternativa (ReDoc):    http://127.0.0.1:8000/redoc

Cómo está armado cada endpoint:
  1. FastAPI valida los parámetros (tipo, rango, formato) antes de tocar la base:
     /kpis?year=abc → 422 con el motivo, sin consultar MySQL.
  2. database.fetch_rows() hace SELECT sobre la vista con parámetros enlazados.
  3. El modelo de respuesta (Pydantic) convierte los tipos de MySQL (Decimal) a JSON
     y documenta cada campo en /docs.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from src.api import database

logger = logging.getLogger(__name__)

DESCRIPTION = """
Datos de exportación de camarón del Ecuador (2018–2026) y de una finca camaronera **SIMULADA**,
servidos desde la capa **gold** del proyecto (arquitectura Medallion en MySQL).

* **Total anual = Banco Central del Ecuador** (FOB). El detalle por país sale de UN Comtrade:
  lo que declara Ecuador (FOB) o, en años sin ese dato, lo que declaran los compradores (espejo, CIF).
* `XXX` = **"Sin reporte por país"**: parte del total oficial que ninguna fuente detalla
  (en 2025 es más de la mitad porque China no reportó).
* `/ponds/ranking` usa datos **SIMULADOS** (finca "Acuícola Del Golfo").
"""

TAGS = [
    {"name": "Exportaciones", "description": "Totales, detalle por país y precios (datos reales)."},
    {"name": "Mercados", "description": "Concentración de la demanda y conciliación de fuentes."},
    {"name": "Finca (SIMULADO)", "description": "Ranking de piscinas de la finca simulada."},
    {"name": "Sistema", "description": "Estado de la API."},
]

app = FastAPI(
    title="Shrimp Intelligence Ecuador API",
    version="1.0.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
)

# Tipos de parámetros reutilizables (validación + texto de ayuda en /docs)
Year = Annotated[int | None, Query(ge=2018, le=2100, description="Año, ej. 2025")]
Iso3 = Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$",
                                   description="Código ISO3 del país, ej. CHN, USA, ESP (XXX = sin reporte)")]


# ------------------------------------------------------------------ modelos de respuesta
class Health(BaseModel):
    status: str
    database: str


class Kpi(BaseModel):
    year: int
    months: int = Field(description="Meses con dato (12 = año completo)")
    value_usd: float = Field(description="Valor exportado, USD FOB (= BCE)")
    value_yoy_pct: float | None = Field(description="Variación vs mismos meses del año anterior")
    weight_lb_identified: float | None = Field(description="Libras con país identificado")
    usd_per_lb: float | None
    price_yoy_pct: float | None
    price_source: str | None = Field(description="UN_COMTRADE (FOB), COMTRADE_MIRROR (CIF) o MIXTA")
    price_yoy_comparable: bool | None = Field(description="False si cambió la fuente del precio")
    country_coverage_pct: float = Field(description="% del valor con país identificado")
    weight_coverage_pct: float | None


class ExportRow(BaseModel):
    period: str
    year: int
    month: int
    iso3: str
    country: str
    market_group: str
    source: str
    valuation_basis: str | None
    value_usd: float
    weight_lb: float | None


class PriceRow(BaseModel):
    year: int
    market_group: str
    months: int
    value_usd: float
    weight_lb: float
    usd_per_lb: float
    vs_china_pct: float | None = Field(description="Cuánto más (o menos) paga este mercado que China")


class ConcentrationRow(BaseModel):
    year: int
    hhi: float = Field(description="Índice Herfindahl-Hirschman (> 2.500 = muy concentrado)")
    level: str
    top_country: str
    top_country_pct: float
    top3_pct: float
    countries: int
    coverage_pct: float
    is_comparable: bool = Field(description="False si menos del 80 % del valor tiene país")


class PondRow(BaseModel):
    farm_code: str
    farm_name: str
    pond_code: str
    area_band: str
    cycles: int
    revenue_usd: float
    profit_usd: float
    margin_pct: float
    margin_quartile: int = Field(description="1 = 25 % más rentable")
    cost_per_lb: float
    price_per_lb: float
    fcr: float = Field(description="kg de alimento ÷ kg cosechados (menor es mejor)")
    fcr_rank: int
    survival_pct: float
    is_simulated: bool


class ReconciliationRow(BaseModel):
    level: str
    year: int | None
    iso3: str
    country: str
    market_group: str
    source: str
    reference_source: str
    months: int
    value_usd: float | None
    reference_value_usd: float | None
    gap_pct: float | None = Field(description="valor ÷ referencia − 1 (ponderado)")
    median_gap_pct: float | None
    worst_month_gap_pct: float | None
    best_month_gap_pct: float | None
    classification: str | None


# ------------------------------------------------------------------ errores
@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """MySQL apagado o error de base: 503 con mensaje corto (el detalle va al log, no al cliente)."""
    logger.error("Error de base de datos en %s: %s", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible"})


@app.exception_handler(database.ApiConfigError)
async def config_error(request: Request, exc: database.ApiConfigError) -> JSONResponse:
    """Falta API_DB_USER / API_DB_PASSWORD en el .env."""
    logger.error("Configuración incompleta: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def rows_or_404(rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Si se filtró y no hay nada, 404 con los filtros usados (más claro que una lista vacía)."""
    used = {k: v for k, v in filters.items() if v is not None}
    if not rows and used:
        raise HTTPException(status_code=404, detail=f"No hay datos para {used}")
    return rows


# ------------------------------------------------------------------ endpoints
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=Health, tags=["Sistema"])
def health() -> dict[str, str]:
    """¿Responde la API y su conexión a MySQL?"""
    database.ping()
    return {"status": "ok", "database": "ok"}


@app.get("/kpis", response_model=list[Kpi], tags=["Exportaciones"])
def kpis(year: Year = None) -> list[dict[str, Any]]:
    """KPIs anuales: valor (= BCE), YoY, libras, USD/lb y cobertura por país."""
    filters = {"year": year}
    return rows_or_404(database.fetch_rows("kpis", filters, ["year"]), filters)


@app.get("/exports", response_model=list[ExportRow], tags=["Exportaciones"])
def exports(country: Iso3 = None, year: Year = None,
            limit: Annotated[int, Query(ge=1, le=5000, description="Filas por página")] = 500,
            offset: Annotated[int, Query(ge=0, description="Filas a saltar (paginación)")] = 0,
            ) -> list[dict[str, Any]]:
    """Exportaciones mensuales por país (solo cifras preferidas; la suma de un mes = BCE)."""
    filters = {"iso3": country.upper() if country else None, "year": year}
    rows = database.fetch_rows("exports", filters, ["period", "value_usd DESC"], limit, offset)
    return rows_or_404(rows, filters) if offset == 0 else rows


@app.get("/prices", response_model=list[PriceRow], tags=["Exportaciones"])
def prices(year: Year = None,
           market: Annotated[str | None, Query(description="Grupo de mercado: China, EE. UU., "
                                                           "Europa, Resto de Asia, Resto de América, Total")] = None,
           ) -> list[dict[str, Any]]:
    """USD por libra por grupo de mercado. Solo lo que declara Ecuador (FOB)."""
    filters = {"year": year, "market_group": market}
    return rows_or_404(database.fetch_rows("prices", filters, ["year", "usd_per_lb DESC"]), filters)


@app.get("/concentration", response_model=list[ConcentrationRow], tags=["Mercados"])
def concentration(year: Year = None,
                  comparable_only: Annotated[bool, Query(description="Solo años con ≥ 80 % del "
                                                                     "valor con país")] = False,
                  ) -> list[dict[str, Any]]:
    """HHI, principal destino y top 3 por año."""
    filters = {"year": year, "is_comparable": 1 if comparable_only else None}
    return rows_or_404(database.fetch_rows("concentration", filters, ["year"]), filters)


@app.get("/ponds/ranking", response_model=list[PondRow], tags=["Finca (SIMULADO)"])
def ponds_ranking(farm: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}-[A-Za-z]{2}$",
                                                    description="Código de finca, ej. ADG-GY")] = None,
                  order_by: Annotated[Literal["margin", "fcr"],
                                      Query(description="margin = mayor margen primero; "
                                                        "fcr = mejor conversión primero")] = "margin",
                  limit: Annotated[int, Query(ge=1, le=100)] = 50,
                  ) -> list[dict[str, Any]]:
    """Ranking de piscinas por margen o por FCR (datos SIMULADOS)."""
    filters = {"farm_code": farm.upper() if farm else None}
    order = ["margin_pct DESC", "pond_code"] if order_by == "margin" else ["fcr", "pond_code"]
    return rows_or_404(database.fetch_rows("ponds", filters, order, limit), filters)


@app.get("/reconciliation", response_model=list[ReconciliationRow], tags=["Mercados"])
def reconciliation(level: Annotated[Literal["year", "country"] | None,
                                    Query(description="year = cada fuente vs BCE por año; "
                                                      "country = espejo vs Ecuador por país")] = None,
                   year: Year = None, country: Iso3 = None,
                   ) -> list[dict[str, Any]]:
    """Conciliación de fuentes. Las causas de las brechas son hipótesis (ADR-006)."""
    filters = {"level": level, "year": year, "iso3": country.upper() if country else None}
    order = ["level DESC", "year", "source", "reference_value_usd DESC"]
    return rows_or_404(database.fetch_rows("reconciliation", filters, order), filters)
