
from __future__ import annotations

import gettext
import logging

import pandas as pd
import pycountry
from country_converter.country_converter import COUNTRY_DATA_FILE

from src.catalogs import normalize_alias
from src.config import ROOT_DIR
from src.db import read_sql

logger = logging.getLogger(__name__)

SEED_DIR = ROOT_DIR / "data" / "reference"

# Códigos ISO3 propios de Comtrade → ISO3 oficial
SPECIAL_ISO3 = {"S19": "TWN"}        # 'Other Asia, nes' = Taiwán en Comtrade

# Nombres en español más usados que el nombre oficial ISO (para el dashboard)
NAME_ES_OVERRIDES = {
    "KOR": "Corea del Sur", "RUS": "Rusia", "MDA": "Moldavia", "TWN": "Taiwán",
    "BOL": "Bolivia", "CIV": "Costa de Marfil", "TUN": "Túnez", "MMR": "Myanmar",
}

# Variantes escritas a mano que aparecen en la simulación u otras fuentes
MANUAL_ALIASES = {
    "USA": ["EEUU", "EE.UU.", "EE. UU.", "U.S.A.", "Estados Unidos de América",
            "United States of America"],
}


def load_un_reference() -> pd.DataFrame:
    """
    Tabla de países que trae country_converter (nombre corto en inglés, continente y
    subregión ONU), leída DIRECTAMENTE con pandas. No se usa su función de conversión
    porque con pandas 3.1 falla al leer su propio archivo: el código ISO2 de Namibia es
    'NA' y pandas lo interpreta como vacío. keep_default_na=False evita ese problema.
    """
    df = pd.read_csv(COUNTRY_DATA_FILE, sep="\t", encoding="utf-8",
                     dtype=str, keep_default_na=False)
    return df.drop_duplicates("ISO3").set_index("ISO3")[["name_short", "continent", "UNregion"]]


def region_m49(un_ref: pd.DataFrame, iso3: str) -> str:
    """Una de las 6 regiones del proyecto (códigos M49 de la ONU)."""
    continent = un_ref.at[iso3, "continent"]
    if continent == "America":
        return "021" if un_ref.at[iso3, "UNregion"] == "Northern America" else "419"
    return {"Europe": "150", "Asia": "142", "Africa": "002", "Oceania": "009"}[continent]


def bronze_countries() -> pd.DataFrame:
    """ISO3, código y nombre de Comtrade de cada país presente en bronze."""
    return read_sql(
        """
        SELECT DISTINCT iso3, code, descr
        FROM (
            SELECT partner_iso AS iso3, partner_code AS code, partner_desc AS descr
            FROM bronze.comtrade_raw
            WHERE flow_code = 'X' AND partner_code <> '0'
            UNION
            SELECT JSON_UNQUOTE(JSON_EXTRACT(raw_payload, '$.reporterISO')),
                   reporter_code,
                   JSON_UNQUOTE(JSON_EXTRACT(raw_payload, '$.reporterDesc'))
            FROM bronze.comtrade_raw
            WHERE flow_code = 'M'
        ) AS t
        """
    )


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    es = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["es"])
    un_ref = load_un_reference()
    countries, aliases = {}, []

    for row in bronze_countries().itertuples(index=False):
        iso3 = SPECIAL_ISO3.get(row.iso3, row.iso3)
        iso = pycountry.countries.get(alpha_3=iso3)
        if iso is None or iso3 not in un_ref.index:
            raise ValueError(f"ISO3 desconocido '{row.iso3}' ({row.descr}): "
                             "agregarlo a SPECIAL_ISO3")

        if iso3 not in countries:
            countries[iso3] = {
                "iso3": iso3,
                "m49_code": int(iso.numeric),          # código oficial (USA = 840)
                "country_name_es": NAME_ES_OVERRIDES.get(iso3, es.gettext(iso.name)),
                "country_name_en": un_ref.at[iso3, "name_short"],
                "region_m49": region_m49(un_ref, iso3),
                "comtrade_codes": set(),               # informativo (USA = 842 en Comtrade)
            }
            c = countries[iso3]
            for text, source in [(iso3, "ISO_3166"), (c["country_name_es"], "ISO_3166"),
                                 (es.gettext(iso.name), "ISO_3166"),
                                 (c["country_name_en"], "ISO_3166"), (iso.name, "ISO_3166")]:
                aliases.append((text, iso3, source))
            for text in MANUAL_ALIASES.get(iso3, []):
                aliases.append((text, iso3, "MANUAL"))

        countries[iso3]["comtrade_codes"].add(str(row.code))
        aliases.append((row.descr, iso3, "UN_COMTRADE"))
        aliases.append((row.iso3, iso3, "UN_COMTRADE"))          # ej. 'S19'

    country_df = pd.DataFrame(countries.values())
    country_df["comtrade_codes"] = country_df["comtrade_codes"].map(
        lambda s: "|".join(sorted(s)))
    country_df = country_df.sort_values("iso3").reset_index(drop=True)

    # Alias normalizados (minúsculas, sin tildes, sin espacios extra); el primero gana
    alias_df = pd.DataFrame(aliases, columns=["alias_raw", "iso3", "seen_in_source"])
    alias_df["alias_text"] = alias_df["alias_raw"].map(normalize_alias)
    conflicts = alias_df.groupby("alias_text")["iso3"].nunique()
    if (conflicts > 1).any():
        raise ValueError(f"Alias asignados a más de un país: {list(conflicts[conflicts > 1].index)}")
    alias_df = (alias_df.drop_duplicates("alias_text")
                [["alias_text", "iso3", "seen_in_source"]]
                .sort_values(["iso3", "alias_text"]).reset_index(drop=True))
    return country_df, alias_df


if __name__ == "__main__":
    from src.logging_config import setup_logging

    setup_logging()
    country_df, alias_df = build()
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    country_df.to_csv(SEED_DIR / "countries_seed.csv", index=False, encoding="utf-8")
    alias_df.to_csv(SEED_DIR / "country_aliases_seed.csv", index=False, encoding="utf-8")
    logger.info("Semillas: %s países, %s alias → %s", len(country_df), len(alias_df),
                SEED_DIR.relative_to(ROOT_DIR))
    print("\nPaíses por región (M49):")
    print(country_df["region_m49"].value_counts().sort_index().to_string())
    print("\nCasos especiales:")
    print(country_df[country_df["iso3"].isin(["USA", "TWN", "KOR", "RUS", "FRA", "IND"])]
          [["iso3", "m49_code", "comtrade_codes", "country_name_es", "region_m49"]]
          .to_string(index=False))