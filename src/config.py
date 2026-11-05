"""
config.py — Configuración central del proyecto Shrimp Intelligence Ecuador.

Todos los módulos importan desde aquí:
    from src.config import settings, paths, project
Nunca se escriben credenciales en el código: se leen del archivo .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 1. Rutas del proyecto (calculadas a partir de la ubicación de este archivo,
#    así funcionan sin importar desde qué carpeta se ejecute el script)
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]

# Cargar el .env de la raíz del proyecto
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Paths:
    """Carpetas del proyecto. frozen=True: nadie puede modificarlas en ejecución."""
    root: Path = ROOT_DIR
    data_raw: Path = ROOT_DIR / "data" / "raw"
    data_processed: Path = ROOT_DIR / "data" / "processed"
    logs: Path = ROOT_DIR / "logs"
    sql: Path = ROOT_DIR / "sql"

    def ensure(self) -> None:
        """Crea las carpetas de trabajo si no existen."""
        for p in (self.data_raw, self.data_processed, self.logs):
            p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 2. Constantes de negocio (un solo lugar para cambiarlas)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectConstants:
    reporter_code: int = 218                      # Ecuador en UN Comtrade (código M49)
    hs_codes: tuple[str, ...] = ("030617",        # Camarones y langostinos congelados
                                 "160521",        # Preparados, sin envase hermético
                                 "160529")        # Preparados, otros
    start_year: int = 2019
    end_year: int = date.today().year             # Hasta el año en curso
    kg_to_lb: float = 2.20462                     # Factor de conversión oficial
    random_seed: int = 42                         # Semilla fija para la simulación
    schemas: tuple[str, ...] = ("bronze", "silver", "gold", "ops")


# ---------------------------------------------------------------------------
# 3. Credenciales y parámetros leídos del .env
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_user: str
    db_password: str = field(repr=False)          # repr=False: nunca se imprime
    comtrade_api_key: str | None = field(default=None, repr=False)
    log_level: str = "INFO"

    def require_comtrade_key(self) -> str:
        """Exige la llave de Comtrade solo cuando se va a usar (Fase 4)."""
        if not self.comtrade_api_key:
            raise RuntimeError(
                "Falta COMTRADE_API_KEY en el archivo .env. "
                "Obtén la llave gratuita en comtradedeveloper.un.org."
            )
        return self.comtrade_api_key


# Variables obligatorias: sin ellas el pipeline no puede arrancar
REQUIRED_VARS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD")


@lru_cache(maxsize=1)  # Se lee una sola vez y se reutiliza en todo el programa
def get_settings() -> Settings:
    """Lee y valida el .env. Falla rápido y con mensaje claro si falta algo."""
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise RuntimeError(
            f"Faltan variables en el .env: {', '.join(missing)}. "
            "Copia .env.example a .env y complétalo."
        )
    return Settings(
        db_host=os.environ["DB_HOST"],
        db_port=int(os.environ["DB_PORT"]),
        db_user=os.environ["DB_USER"],
        db_password=os.environ["DB_PASSWORD"],
        comtrade_api_key=os.getenv("COMTRADE_API_KEY") or None,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


# Objetos listos para importar desde cualquier módulo
paths = Paths()
project = ProjectConstants()
settings = get_settings()


# ---------------------------------------------------------------------------
# 4. Autodiagnóstico: python -m src.config
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    paths.ensure()
    print("=== Configuración de Shrimp Intelligence Ecuador ===")
    print(f"Raíz del proyecto : {paths.root}")
    print(f"MySQL             : {settings.db_user}@{settings.db_host}:{settings.db_port}"
          f" | password cargada: {bool(settings.db_password)}")
    print(f"Llave Comtrade    : {bool(settings.comtrade_api_key)}")
    print(f"Esquemas          : {', '.join(project.schemas)}")
    print(f"Partidas HS       : {', '.join(project.hs_codes)}"
          f" | Periodo: {project.start_year}–{project.end_year}")
    print("Carpetas          : OK (data/raw, data/processed, logs)")