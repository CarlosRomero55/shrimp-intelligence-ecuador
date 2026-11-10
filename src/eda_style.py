"""
eda_style.py — Estilo común y utilidades para los gráficos del EDA (Fase 10).

Un solo lugar para:
  - la PALETA (azul océano = dato principal · coral = lo que hay que mirar ·
    gris = "Sin reporte" o no comparable). Se reutiliza en Power BI (Fase 12);
  - load_query("q03"): ejecuta sql/queries/q03_*.sql (misma fuente que run_queries);
  - save_fig(fig, "03_hhi"): guarda el PNG en docs/img/ (README y LinkedIn);
  - formatos en español (miles con punto, % y M USD).

Uso en el notebook:
    from src.eda_style import COLORS, apply_style, load_query, save_fig, titled
    apply_style()
"""
from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter

from src.config import ROOT_DIR
from src.db import read_sql
from src.run_queries import QUERIES_DIR, read_query

IMG_DIR = ROOT_DIR / "docs" / "img"

# Paleta del proyecto (HEX) — misma que se usará en Power BI
COLORS = {
    "ocean": "#0B4F6C",        # azul océano: serie principal
    "ocean_mid": "#1F7A8C",
    "ocean_light": "#7FB7BE",
    "coral": "#FF6F59",        # coral: lo que hay que mirar
    "coral_light": "#FFB4A2",
    "sand": "#E9C46A",
    "grey": "#B0B7BF",         # "Sin reporte" / no comparable
    "grey_light": "#E4E7EB",
    "text": "#22313F",
}

# Color fijo por grupo de mercado (el mismo grupo siempre con el mismo color)
MARKET_COLORS = {
    "China": COLORS["coral"],
    "EE. UU.": COLORS["ocean"],
    "Europa": COLORS["ocean_mid"],
    "Resto de Asia": COLORS["ocean_light"],
    "Resto de América": COLORS["sand"],
    "Otros": "#C9CED6",
    "Sin reporte": COLORS["grey"],
}

SOURCE_NOTE = ("Fuentes: Banco Central del Ecuador (total) y UN Comtrade (detalle por país). "
               "Shrimp Intelligence Ecuador.")


def apply_style() -> None:
    """Estilo limpio y legible para todos los gráficos."""
    mpl.rcParams.update({
        "figure.figsize": (10, 5.2),
        "figure.dpi": 110,
        "savefig.dpi": 160,
        "savefig.bbox": "tight",
        "font.size": 10,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.titlepad": 26,               # espacio para el subtítulo
        "axes.labelcolor": COLORS["text"],
        "axes.edgecolor": "#9AA5B1",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": COLORS["grey_light"],
        "grid.linewidth": 0.8,
        "xtick.color": COLORS["text"],
        "ytick.color": COLORS["text"],
        "legend.frameon": False,
        "text.color": COLORS["text"],
    })


def load_query(prefix: str) -> pd.DataFrame:
    """Ejecuta sql/queries/<prefix>*.sql y devuelve el resultado (ej. 'q03')."""
    files = sorted(QUERIES_DIR.glob(f"{prefix}*.sql"))
    if len(files) != 1:
        raise FileNotFoundError(f"Se esperaba 1 consulta '{prefix}*.sql' en {QUERIES_DIR}, "
                                f"hay {len(files)}")
    _, sql = read_query(files[0])
    return read_sql(sql)


# ------------------------------------------------------------------ formatos (español)
def es_number(x: float, decimals: int = 0) -> str:
    """1234567.8 → '1.234.568' · con decimales usa coma: 2.5 → '2,5'."""
    text = f"{x:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


thousands = FuncFormatter(lambda x, _: es_number(x))
percent = FuncFormatter(lambda x, _: f"{es_number(x)} %")


def titled(ax, title: str, subtitle: str | None = None) -> None:
    """Título que DICE LA CONCLUSIÓN + subtítulo con qué se mide."""
    ax.set_title(title)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5,
                color="#52606D", va="bottom")


def source_note(fig, text: str = SOURCE_NOTE) -> None:
    fig.text(0.01, -0.02, text, fontsize=7.5, color="#7B8794", ha="left", va="top")


def save_fig(fig, name: str) -> Path:
    """Guarda docs/img/<name>.png y devuelve la ruta."""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = IMG_DIR / f"{name}.png"
    fig.savefig(path)
    return path


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Consulta puntual desde el notebook (series que no tienen archivo en sql/queries/)."""
    return read_sql(sql, params)


# ------------------------------------------------------------------ diagrama de flujos
def draw_flows(ax, columns: list[list[tuple[str, float, str]]],
               links: list[tuple[str, str, float, str]], *, gap: float = 0.012,
               node_width: float = 0.018, label_min: float = 0.0, value_fmt=None) -> None:
    """
    Diagrama de flujos (alluvial) dibujado con curvas de Bézier, sin librerías extra.
      columns: una lista por columna con nodos (nombre, valor, color), de arriba hacia abajo.
      links:   (origen, destino, valor, color) entre columnas consecutivas.
    El alto de cada nodo y el grosor de cada banda son proporcionales al valor.
    """
    from matplotlib.patches import PathPatch, Rectangle
    from matplotlib.path import Path as MplPath

    n_cols = len(columns)
    xs = [0.04 + i * (0.86 / (n_cols - 1)) for i in range(n_cols)]
    total = max(sum(v for _, v, _ in col) for col in columns)
    scale = (1 - gap * (max(len(c) for c in columns) - 1)) / total

    pos = {}                                   # nodo → (x, y_arriba, alto)
    for x, col in zip(xs, columns):
        used = sum(v for _, v, _ in col) * scale + gap * (len(col) - 1)
        y = 1 - (1 - used) / 2
        for name, value, color in col:
            h = value * scale
            pos[name] = (x, y, h)
            ax.add_patch(Rectangle((x, y - h), node_width, h, color=color, zorder=3))
            y -= h + gap

    out_offset = {k: 0.0 for k in pos}         # cuánto ya salió / entró de cada nodo
    in_offset = {k: 0.0 for k in pos}
    for src, dst, value, color in links:
        x0, y0, _ = pos[src]
        x1, y1, _ = pos[dst]
        h = value * scale
        top0, top1 = y0 - out_offset[src], y1 - in_offset[dst]
        out_offset[src] += h
        in_offset[dst] += h
        xa, xb = x0 + node_width, x1
        mid = (xa + xb) / 2
        verts = [(xa, top0), (mid, top0), (mid, top1), (xb, top1),
                 (xb, top1 - h), (mid, top1 - h), (mid, top0 - h), (xa, top0 - h), (xa, top0)]
        codes = [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                 MplPath.LINETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=color, alpha=0.45,
                               edgecolor="none", zorder=2))

    fmt = value_fmt or (lambda v: es_number(v))
    for i, col in enumerate(columns):
        for name, value, _ in col:
            x, y, h = pos[name]
            if value < label_min:
                continue
            last = i == n_cols - 1
            ax.text(x + (node_width + 0.006 if last or i > 0 else -0.006), y - h / 2,
                    f"{name}  {fmt(value)}" if last or i > 0 else f"{name}\n{fmt(value)}",
                    ha="left" if (last or i > 0) else "right", va="center",
                    fontsize=9 if i < n_cols - 1 else 8.5, color=COLORS["text"],
                    fontweight="bold" if i == 0 else "normal", zorder=4)
    ax.set_xlim(-0.08, 1.1)
    ax.set_ylim(0, 1)
    ax.axis("off")