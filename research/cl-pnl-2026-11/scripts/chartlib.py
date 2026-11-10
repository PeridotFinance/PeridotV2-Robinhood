"""Static chart style for the report (PNG for a Markdown document).

One y-axis per chart, thin marks, recessive grid, text in ink colours (never the series colour), a legend whenever there
are two or more series plus direct value labels where they fit. Categorical colours are used in a fixed order and follow
the entity (fee income is always blue, arbitrage loss always orange, and so on), never its rank.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lib_cl import CHARTS  # noqa: E402

SURFACE = "#fcfcfb"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"
# fixed entity colours used across the report
C_FEE, C_LOSS, C_NET, C_HOLD, C_HEDGED, C_REWARD = BLUE, ORANGE, AQUA, INK2, VIOLET, YELLOW
VENUE_COLOR = {"v4_nvda": BLUE, "ramses_nvda": ORANGE, "ramses_spy": AQUA, "pharaoh_wavax": YELLOW, "v4_spy": VIOLET}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans", "font.size": 9.5, "text.color": INK, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 10,
    "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
    "axes.edgecolor": GRID, "axes.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.7, "axes.axisbelow": True,
    "lines.linewidth": 1.8, "legend.frameon": False, "legend.fontsize": 8.5, "figure.dpi": 110, "savefig.dpi": 160,
})


def fig(w: float = 8.4, h: float = 3.6, ncols: int = 1, nrows: int = 1, **kw):
    f, ax = plt.subplots(nrows, ncols, figsize=(w, h), **kw)
    return f, ax


def zero_line(ax):
    ax.axhline(0, color=INK2, linewidth=0.8, zorder=1)


def label_bars(ax, bars, fmt="{:.2f}", dy=2, fontsize=8):
    for b in bars:
        h = b.get_height()
        if h != h:
            continue
        ax.annotate(fmt.format(h), (b.get_x() + b.get_width() / 2, h), xytext=(0, dy if h >= 0 else -dy - 8), textcoords="offset points",
                    ha="center", va="bottom", fontsize=fontsize, color=INK)


def note(f, text: str):
    """Footnote under the plot, wrapped to the figure width (about 15.5 characters per inch at this size)."""
    import textwrap
    lines = textwrap.wrap(text, width=int(f.get_figwidth() * 15.5))
    f._note_lines = len(lines)
    f.text(0.008, 0.008, "\n".join(lines), fontsize=7.5, color=INK2, ha="left", va="bottom", linespacing=1.3)


def save(f, name: str):
    CHARTS.mkdir(exist_ok=True)
    pad = 0.02 + 0.13 * getattr(f, "_note_lines", 0) / f.get_figheight()
    f.tight_layout(rect=(0, pad, 1, 1))
    p = CHARTS / name
    f.savefig(p)
    plt.close(f)
    return p
