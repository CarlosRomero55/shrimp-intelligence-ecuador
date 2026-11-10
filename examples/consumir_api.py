"""
consumir_api.py — Ejemplo de consumo de la API de Shrimp Intelligence Ecuador (Fase 11).

Muestra cómo otro sistema (un dashboard, otro equipo, un script de un analista)
usaría los datos SIN conectarse a MySQL: solo HTTP y JSON.

Requisitos: la API encendida en otra ventana de PowerShell:
    uvicorn src.api.main:app
Ejecutar:
    python examples/consumir_api.py
    $env:API_URL = "http://otro-servidor:8000"; python examples/consumir_api.py   # otra dirección
"""
from __future__ import annotations

import os
import sys

import httpx

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


def get_json(client: httpx.Client, path: str, **params) -> list[dict]:
    """GET + manejo de errores: 404 = sin datos, 422 = parámetro inválido, 503 = base caída."""
    response = client.get(path, params=params)
    if response.status_code != 200:
        detail = response.json().get("detail")
        raise RuntimeError(f"{path} → HTTP {response.status_code}: {detail}")
    return response.json()


def num(value: float, decimals: int = 2) -> str:
    """Número en formato español: 1234.5 → '1.234,50'."""
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def millions(value: float | None) -> str:
    return "—" if value is None else f"{num(value / 1e6, 1)} M"


def pct(value: float | None) -> str:
    return "—" if value is None else ("+" if value > 0 else "") + f"{num(value, 1)} %"


def show_kpis(client: httpx.Client) -> None:
    print("\n1) KPIs anuales  →  GET /kpis")
    print(f"   {'Año':<6}{'Meses':>6}{'Valor USD':>14}{'YoY':>10}{'USD/lb':>9}  {'Fuente precio':<16}{'Con país':>10}")
    for k in get_json(client, "/kpis"):
        usd_lb = "—" if k["usd_per_lb"] is None else num(k["usd_per_lb"])
        print(f"   {k['year']:<6}{k['months']:>6}{millions(k['value_usd']):>14}{pct(k['value_yoy_pct']):>10}"
              f"{usd_lb:>9}  {k['price_source'] or '—':<16}{num(k['country_coverage_pct'], 1) + ' %':>10}")


def show_china_price(client: httpx.Client) -> None:
    print("\n2) Precio de China por año (FOB)  →  GET /prices?market=China")
    for p in get_json(client, "/prices", market="China"):
        bar = "█" * round(p["usd_per_lb"] * 10)
        print(f"   {p['year']}  {num(p['usd_per_lb'])} USD/lb  {bar}")


def show_top_ponds(client: httpx.Client, n: int = 3) -> None:
    print(f"\n3) Top {n} piscinas por margen (SIMULADO)  →  GET /ponds/ranking?limit={n}")
    for i, p in enumerate(get_json(client, "/ponds/ranking", limit=n), start=1):
        print(f"   {i}. {p['pond_code']:<8} margen {num(p['margin_pct'], 1)} %  ·  "
              f"precio {num(p['price_per_lb'])} USD/lb  ·  FCR {num(p['fcr'])}")


def main(client: httpx.Client | None = None) -> int:
    own_client = client is None
    client = client or httpx.Client(base_url=API_URL, timeout=10)
    try:
        health = get_json(client, "/health")
        print(f"API en {API_URL}: {health}")
        show_kpis(client)
        show_china_price(client)
        show_top_ponds(client)
        return 0
    except httpx.ConnectError:
        print(f"No se pudo conectar a {API_URL}. ¿Está encendida? → uvicorn src.api.main:app")
        return 1
    except RuntimeError as exc:
        print(f"Error de la API: {exc}")
        return 1
    finally:
        if own_client:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
