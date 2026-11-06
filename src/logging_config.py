
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "pipeline.log"

# Formato: fecha y hora | nivel | módulo | mensaje
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(module)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Envía los logs a la consola y a logs/pipeline.log (con rotación)."""
    root = logging.getLogger()


    if root.handlers:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Consola: para ver el avance mientras se ejecuta
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    # Archivo rotativo: al llegar a 5 MB crea pipeline.log.1, .2 ... hasta 5.
    # encoding utf-8 es clave en Windows para que tildes y ñ no salgan rotas.
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    # Silencia el detalle interno de SQLAlchemy (solo mostrará advertencias)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)