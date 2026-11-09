"""
catalogs.py — Datos de referencia de silver que NO vienen de bronze.

Son definiciones del proyecto, versionadas en Git y cargadas con upsert (idempotente):
- data_sources: qué fuentes usamos y cómo se valoran (IDs fijos: gold los usa como llaves).
- regions: 6 regiones M49 de la ONU.
- countries / country_aliases: desde las semillas data/reference/*.csv
  (generadas con tools/build_country_seed.py y revisadas a mano).
- products: los 3 códigos HS del proyecto.
- sizes: tallas comerciales de camarón entero (piezas por kilo).

Paso 5.1: data_sources · 5.2: regiones, países y alias · 5.3: productos y tallas.
"""
from __future__ import annotations

import logging
import re
import unicodedata

import pandas as pd

from src.config import ROOT_DIR
from src.db import read_sql, upsert

logger = logging.getLogger(__name__)

SEED_DIR = ROOT_DIR / "data" / "reference"

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

# ------------------------------------------------------------------ regiones (M49 de la ONU)
REGIONS = [
    {"m49_region_code": "002", "region_name_es": "África", "region_name_en": "Africa"},
    {"m49_region_code": "021", "region_name_es": "Norteamérica",
     "region_name_en": "Northern America"},
    {"m49_region_code": "419", "region_name_es": "América Latina y el Caribe",
     "region_name_en": "Latin America and the Caribbean"},
    {"m49_region_code": "142", "region_name_es": "Asia", "region_name_en": "Asia"},
    {"m49_region_code": "150", "region_name_es": "Europa", "region_name_en": "Europe"},
    {"m49_region_code": "009", "region_name_es": "Oceanía", "region_name_en": "Oceania"},
]

# ------------------------------------------------------------------ productos (HS 6 dígitos)
# product_category: 'frozen' o 'prepared' (regla CHECK chk_products_category).
# hs_heading NO se envía: es una columna GENERADA por MySQL (primeros 4 dígitos de hs_code).
PRODUCTS = [
    {"hs_code": "030617", "product_category": "frozen",
     "product_name_es": "Camarones y langostinos congelados",
     "product_name_en": "Frozen shrimps and prawns"},
    {"hs_code": "160521", "product_category": "prepared",
     "product_name_es": "Camarones y langostinos preparados, en envases no herméticos",
     "product_name_en": "Shrimps and prawns, prepared, not in airtight containers"},
    {"hs_code": "160529", "product_category": "prepared",
     "product_name_es": "Los demás camarones y langostinos preparados o conservados",
     "product_name_en": "Other shrimps and prawns, prepared or preserved"},
]

# ------------------------------------------------------------------ tallas comerciales
# Camarón ENTERO, piezas por kilo (más grande = menos piezas = mejor precio).
# size_code con barra: regla CHECK chk_sizes_format ('^[0-9]{2,3}/[0-9]{2,3}$').
SIZE_COUNT_BASIS = "pieces_per_kg"
SIZES = [
    {"size_code": code, "min_count": lo, "max_count": hi,
     "count_basis": SIZE_COUNT_BASIS, "sort_order": order}
    for order, (code, lo, hi) in enumerate([
        ("20/30", 20, 30), ("30/40", 30, 40), ("40/50", 40, 50), ("50/60", 50, 60),
        ("60/70", 60, 70), ("70/80", 70, 80), ("80/100", 80, 100), ("100/120", 100, 120),
    ], start=1)
]


# ------------------------------------------------------------------ normalización
def normalize_alias(text) -> str | None:
    """
    Forma estándar para comparar nombres de países: minúsculas, sin tildes y sin espacios
    extra.  'U.S.A. ' → 'u.s.a.' · 'España' → 'espana' · 'CHINA' → 'china'.
    La misma función se usa al guardar los alias y al buscarlos durante la limpieza.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))   # quita tildes
    return re.sub(r"\s+", " ", text).strip().lower()


_SIZE_RE = re.compile(r"^\s*(\d{2,3})\s*[/\-\s]\s*(\d{2,3})\s*$")


def normalize_size(text) -> str | None:
    """
    Talla al formato estándar con barra: '20-30' · '20 30' · ' 20/30 ' → '20/30'.
    Devuelve None si el texto no tiene forma de talla (la limpieza lo reporta).
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    match = _SIZE_RE.match(str(text))
    return f"{match.group(1)}/{match.group(2)}" if match else None


# ------------------------------------------------------------------ cargas
def load_data_sources(conn=None) -> dict[str, int]:
    """Catálogo de fuentes (llave de negocio: source_code)."""
    return upsert(pd.DataFrame(DATA_SOURCES), "silver", "data_sources",
                  key_cols=["source_code"], conn=conn)


def load_regions(conn=None) -> dict[str, int]:
    """6 regiones M49 (llave de negocio: m49_region_code)."""
    return upsert(pd.DataFrame(REGIONS), "silver", "regions",
                  key_cols=["m49_region_code"], conn=conn)


def load_countries(conn=None) -> dict[str, int]:
    """Países desde la semilla (llave de negocio: iso3). Requiere las regiones cargadas."""
    seed = pd.read_csv(SEED_DIR / "countries_seed.csv", dtype=str, keep_default_na=False)
    regions = read_sql("SELECT region_id, m49_region_code FROM silver.regions")
    df = seed.merge(regions, left_on="region_m49", right_on="m49_region_code", how="left")
    if df["region_id"].isna().any():
        missing = sorted(df.loc[df["region_id"].isna(), "region_m49"].unique())
        raise ValueError(f"Regiones no cargadas en silver.regions: {missing}")
    df["m49_code"] = df["m49_code"].astype(int)
    df["region_id"] = df["region_id"].astype(int)
    cols = ["iso3", "m49_code", "country_name_es", "country_name_en", "region_id"]
    return upsert(df[cols], "silver", "countries", key_cols=["iso3"], conn=conn)


def load_country_aliases(conn=None) -> dict[str, int]:
    """Alias normalizados desde la semilla (llave de negocio: alias_text)."""
    seed = pd.read_csv(SEED_DIR / "country_aliases_seed.csv", dtype=str,
                       keep_default_na=False)
    countries = read_sql("SELECT country_id, iso3 FROM silver.countries")
    df = seed.merge(countries, on="iso3", how="left")
    if df["country_id"].isna().any():
        missing = sorted(df.loc[df["country_id"].isna(), "iso3"].unique())
        raise ValueError(f"Países de los alias no cargados en silver.countries: {missing}")
    df["country_id"] = df["country_id"].astype(int)
    cols = ["alias_text", "country_id", "seen_in_source"]
    return upsert(df[cols], "silver", "country_aliases", key_cols=["alias_text"], conn=conn)


def load_products(conn=None) -> dict[str, int]:
    """3 códigos HS (llave de negocio: hs_code)."""
    return upsert(pd.DataFrame(PRODUCTS), "silver", "products",
                  key_cols=["hs_code"], conn=conn)


def load_sizes(conn=None) -> dict[str, int]:
    """7 tallas comerciales (llave de negocio: size_code)."""
    return upsert(pd.DataFrame(SIZES), "silver", "sizes",
                  key_cols=["size_code"], conn=conn)


def load_all() -> None:
    """Carga todos los catálogos en orden de dependencia (llaves foráneas)."""
    load_data_sources()
    load_regions()
    load_countries()
    load_country_aliases()
    load_products()
    load_sizes()


# Ejecutar: python -m src.catalogs
if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    load_all()

    summary = read_sql(
        """
        SELECT r.region_name_es AS region,
               COUNT(DISTINCT c.country_id) AS paises,
               COUNT(a.alias_id)            AS alias
        FROM silver.regions AS r
        LEFT JOIN silver.countries       AS c ON c.region_id  = r.region_id
        LEFT JOIN silver.country_aliases AS a ON a.country_id = c.country_id
        GROUP BY r.region_name_es
        ORDER BY paises DESC
        """
    )
    print("\nPaíses y alias por región:")
    print(summary.to_string(index=False))

    # Contenido COMPLETO: si la Fase 2 dejó filas que no están en este catálogo, se verán aquí.
    # hs_heading se muestra para comprobar que MySQL la calculó sola (columna generada).
    products = read_sql(
        "SELECT product_id, hs_code, hs_heading, product_category, product_name_es "
        "FROM silver.products ORDER BY hs_code"
    )
    print(f"\nsilver.products ({len(products)} filas; esperadas {len(PRODUCTS)}):")
    print(products.to_string(index=False))

    sizes = read_sql(
        "SELECT size_id, size_code, min_count, max_count, count_basis, sort_order "
        "FROM silver.sizes ORDER BY sort_order, size_code"
    )
    print(f"\nsilver.sizes ({len(sizes)} filas; esperadas {len(SIZES)}):")
    print(sizes.to_string(index=False))

    extra = sorted(set(sizes["size_code"]) - {s["size_code"] for s in SIZES})
    if extra:
        print(f"\n⚠ Tallas en silver que no están en el catálogo (revisar): {extra}")