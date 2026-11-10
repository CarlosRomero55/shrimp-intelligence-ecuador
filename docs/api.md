# API REST — Shrimp Intelligence Ecuador

API de **solo lectura** que expone en JSON los datos de la capa gold. Decisión de diseño: `docs/adr/ADR-004-fastapi-capa-de-servicio.md`.

## 1. Encenderla

```powershell
cd D:\shrimp-intelligence-ecuador
.\.venv\Scripts\Activate.ps1
uvicorn src.api.main:app --reload        # --reload: reinicia al cambiar un .py (solo desarrollo)
```

| Dirección | Qué es |
|---|---|
| http://127.0.0.1:8000/docs | Documentación interactiva (Swagger): probar cada endpoint con **Try it out** |
| http://127.0.0.1:8000/redoc | Documentación de lectura (ReDoc) |
| http://127.0.0.1:8000/openapi.json | Contrato OpenAPI (para generar clientes o importarlo en Postman) |

Requisitos:

- MySQL encendido con gold cargado (`python -m src.pipeline`).
- Vistas `gold.api_*` creadas (`sql/04_api_views.sql`, como root).
- Usuario `api_reader` con `SELECT` solo sobre esas vistas, y en el `.env`: `API_DB_USER` y `API_DB_PASSWORD` (ver `.env.example`).
- El `.env` se lee **al arrancar**: si lo cambias, reinicia uvicorn (`Ctrl + C` y otra vez).

## 2. Endpoints

| Endpoint | Filtros | Vista de gold | Ejemplo |
|---|---|---|---|
| `GET /health` | — | `SELECT 1` | `/health` |
| `GET /kpis` | `year` | `api_kpis_annual` | `/kpis?year=2025` |
| `GET /exports` | `country` (ISO3), `year`, `limit` (≤ 5000), `offset` | `api_exports_monthly` | `/exports?country=CHN&year=2024` |
| `GET /prices` | `year`, `market` | `api_prices` | `/prices?market=China` |
| `GET /concentration` | `year`, `comparable_only` | `api_concentration` | `/concentration?comparable_only=true` |
| `GET /ponds/ranking` | `farm`, `order_by` (`margin` \| `fcr`), `limit` (≤ 100) | `api_pond_ranking` | `/ponds/ranking?order_by=fcr&limit=5` |
| `GET /reconciliation` | `level` (`year` \| `country`), `year`, `country` | `api_reconciliation` | `/reconciliation?level=country` |

Cómo leer los datos:

- **Total anual = BCE** (FOB). En `/exports`, sumar todos los países de un mes da el total del BCE de ese mes.
- `iso3 = "XXX"` es **"Sin reporte por país"**: la parte del total que ninguna fuente detalla. En 2025 es más de la mitad, porque China no reportó; por eso `/kpis` trae `country_coverage_pct`.
- `/prices` usa **solo lo que declara Ecuador (FOB)**. El espejo es CIF y no se mezcla.
- `price_yoy_comparable = false`: el precio del año anterior viene de otra fuente (FOB vs CIF).
- `/ponds/ranking` es de la finca **SIMULADA** (`is_simulated = true`).

Ejemplo de respuesta (`GET /kpis?year=2025`):

```json
[
  {
    "year": 2025,
    "months": 12,
    "value_usd": 8401253486.31,
    "value_yoy_pct": 20.2,
    "weight_lb_identified": 1297962296,
    "usd_per_lb": 3.035,
    "price_yoy_pct": 17,
    "price_source": "COMTRADE_MIRROR",
    "price_yoy_comparable": false,
    "country_coverage_pct": 46.9,
    "weight_coverage_pct": 46.9
  }
]
```

## 3. Códigos de respuesta

| Código | Cuándo | Ejemplo |
|---|---|---|
| 200 | Hay datos (o una página vacía de `/exports` con `offset`) | `/kpis` |
| 404 | Se filtró y no hay datos | `/kpis?year=2030` → `"No hay datos para {'year': 2030}"` |
| 422 | Parámetro inválido; **no se consulta la base** | `/kpis?year=abc`, `/exports?country=CHINA` |
| 503 | MySQL no responde o faltan credenciales en el `.env` | `{"detail": "Base de datos no disponible"}` |

Los errores de base de datos se registran en el log del servidor; al cliente solo le llega un mensaje corto, sin detalles internos.

## 4. Consumirla

PowerShell (`curl.exe`, no el alias de PowerShell):

```powershell
curl.exe "http://127.0.0.1:8000/kpis?year=2025"
curl.exe "http://127.0.0.1:8000/exports?country=USA&year=2024&limit=12"
```

Python (`httpx`), con la API encendida en otra ventana:

```powershell
python examples/consumir_api.py
```

```python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
    china = client.get("/prices", params={"market": "China"}).json()
    for row in china:
        print(row["year"], row["usd_per_lb"])
```

Power BI: **Obtener datos → Web** → `http://127.0.0.1:8000/kpis` (alternativa a la conexión directa a MySQL de la Fase 12).

## 5. Seguridad

- **Mínimo privilegio:** `api_reader` solo tiene `SELECT` sobre las 6 vistas `gold.api_*`; no puede leer bronze, silver ni las tablas de gold, ni escribir. Lo prueban `test_api_reader_no_puede_leer_silver` y `test_api_reader_no_puede_borrar` (error 1142 de MySQL).
- **Sin inyección SQL:** los nombres de vista, columnas y orden salen del código (lista blanca); los valores del usuario viajan como parámetros enlazados (`:year`). Además, FastAPI valida el formato antes (ej. `country` debe ser `^[A-Za-z]{3}$`).
- **Sin secretos en el código:** credenciales solo en el `.env` (fuera de GitHub).
- Pensada para uso **local**. Para publicarla haría falta: HTTPS, autenticación (API key o OAuth2), límite de peticiones y CORS solo para los orígenes permitidos.

## 6. Pruebas

```powershell
python -m pytest tests/test_api.py -v              # 26 pruebas
python -m pytest tests/test_api.py -m "not db" -v  # 20 unitarias (SQLite en memoria, sin MySQL)
```
