
from __future__ import annotations

import logging

import pandas as pd

from src.db import read_sql, upsert

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ fuentes de datos
DATA_SOURCES = [
    {
        "source_id": 1,
        "source_code": "UN_COMTRADE",
        "source_name": "UN Comtrade Database",
        "publisher": "Naciones Unidas (UNSD)",
        "source_url": "https://comtradeplus.un.org",
        "weight_unit": "kg",
        "valuation_basis": "FOB",
        "is_simulated": 0,
        "notes": "Exportaciones reportadas por Ecuador (reporter 218, flow X), país x HS6 x mes. "
                 "No publicados: Feb 2022-Dic 2023, Abr 2024 (sin HS camarón), 2025 (ver ADR-006).",
    },
    {
        "source_id": 2,
        "source_code": "CNA",
        "source_name": "Estadísticas de exportación",
        "publisher": "Cámara Nacional de Acuacultura",
        "source_url": None,
        "weight_unit": "lb",
        "valuation_basis": "FOB",
        "is_simulated": 0,
        "notes": "Sin API pública; no cargada en esta versión (mejora futura). "
                 "Reporta por mercado en libras.",
    },
    {
        "source_id": 3,
        "source_code": "BCE",
        "source_name": "BCEData 3.1.2 Exportaciones FOB por Grupos de Productos",
        "publisher": "Banco Central del Ecuador",
        "source_url": "https://www.bce.fin.ec",
        "weight_unit": None,                 # el cuadro no trae peso
        "valuation_basis": "FOB",
        "is_simulated": 0,
        "notes": "Serie 'Camarón', miles de USD, mensual. TOTAL NACIONAL sin país ni HS: "
                 "referencia oficial en la conciliación. Descarga manual "
                 "(docs/fuentes/bce_descarga_manual.md).",
    },
    {
        "source_id": 4,
        "source_code": "FARM_SIM",
        "source_name": "Operación de Acuícola Del Golfo S.A.",
        "publisher": "Generado con Python (numpy, semilla 42)",
        "source_url": None,
        "weight_unit": "lb",
        "valuation_basis": None,
        "is_simulated": 1,
        "notes": "DATOS SIMULADOS para demostrar análisis de producción; precio ligado al "
                 "USD/lb real de exportación del mes.",
    },
    {
        "source_id": 5,
        "source_code": "COMTRADE_MIRROR",
        "source_name": "UN Comtrade - estadísticas espejo",
        "publisher": "Naciones Unidas (UNSD)",
        "source_url": "https://comtradeplus.un.org",
        "weight_unit": "kg",
        "valuation_basis": "CIF",
        "is_simulated": 0,
        "notes": "Importaciones reportadas por otros países DESDE Ecuador (partner 218, flow M). "
                 "Detalle por país en los meses sin dato de Ecuador y calibración. "
                 "CIF: incluye flete y seguro; posible desfase por fecha de llegada (ADR-006).",
    },
]

# Atajo para los demás módulos: SOURCE_IDS["COMTRADE_MIRROR"] → 5
SOURCE_IDS = {s["source_code"]: s["source_id"] for s in DATA_SOURCES}


def load_data_sources(conn=None) -> dict[str, int]:
    """Carga/actualiza el catálogo de fuentes (llave de negocio: source_code)."""
    df = pd.DataFrame(DATA_SOURCES)
    return upsert(df, "silver", "data_sources", key_cols=["source_code"], conn=conn)


# Ejecutar: python -m src.catalogs
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    load_data_sources()

    check = read_sql(
        """
        SELECT source_id, source_code, weight_unit, valuation_basis, is_simulated
        FROM silver.data_sources
        ORDER BY source_id
        """
    )
    print("\nsilver.data_sources:")
    print(check.to_string(index=False))