"""Charts for Q1 (fee income versus arbitrage loss). Reads out/q1_<venue>.json; writes charts/q1_*.png."""
from __future__ import annotations

import json
import sys

import numpy as np

from chartlib import C_FEE, C_LOSS, INK, INK2, SURFACE, VENUE_COLOR, fig, label_bars, note, save, zero_line
from lib_cl import OUT, SESSIONS, VENUES

HLAB = {0: "at the swap", 60: "+1 min", 300: "+5 min", 3600: "+1 h", 14400: "+4 h", 86400: "+24 h"}


def load(venues, tag=""):
    out = {}
    for v in venues:
        p = OUT / f"q1_{v}{tag}.json"
        if p.exists():
            out[v] = json.loads(p.read_text())
    return out


def chart_ratio_by_session(res, tag=""):
    """Fee / arbitrage loss by session, one panel per pool, two horizons, log scale (the ratios span 0.5 to 140)."""
    n = len(res)
    f, axes = fig(2.45 * n + 0.5, 3.9, ncols=n, sharey=True)
    axes = np.atleast_1d(axes)
    for ax, (v, o) in zip(axes, res.items()):
        x = np.arange(len(SESSIONS))
        r1 = [o["by_session"][s].get("fee_over_loss_60") or np.nan for s in SESSIONS]
        r2 = [o["by_session"][s].get("fee_over_loss_3600") or np.nan for s in SESSIONS]
        ax.vlines(x, r2, r1, color="#b9c4d0", linewidth=1.2, zorder=2)
        ax.plot(x, r1, linestyle="none", marker="o", markersize=7, markerfacecolor=SURFACE, markeredgecolor=C_FEE, markeredgewidth=1.6,
                label="loss valued 1 minute after each swap", zorder=3)
        ax.plot(x, r2, linestyle="none", marker="o", markersize=7, color=C_FEE, label="loss valued 1 hour after each swap", zorder=4)
        for xi, val in zip(x, r2):
            ax.annotate(f"{val:.1f}" if val < 10 else f"{val:.0f}", (xi, val), xytext=(8, -3), textcoords="offset points", fontsize=8, color=INK)
        ax.axhline(1.0, color=INK, linewidth=1.0, linestyle=(0, (4, 3)), zorder=1)
        ax.set_yscale("log")
        ax.set_ylim(0.3, 200)
        ax.set_yticks([0.5, 1, 2, 5, 10, 20, 50, 100]); ax.set_yticklabels(["0.5", "1", "2", "5", "10", "20", "50", "100"])
        ax.set_xticks(x); ax.set_xticklabels(["regular", "extended", "overnight", "weekend"], fontsize=8, rotation=25, ha="right", rotation_mode="anchor")
        ax.set_xlim(-0.5, len(SESSIONS) - 0.2)
        ax.set_title(VENUES[v].label.split(" (")[0].replace("Uniswap ", ""), fontsize=9.5)
    axes[0].set_ylabel("LP fee income / arbitrage loss (log scale)")
    axes[0].legend(loc="upper left", fontsize=7.5, handletextpad=0.3, borderaxespad=0.2)
    f.suptitle("Fee income divided by arbitrage loss, by session (dashed line = break-even)", x=0.01, ha="left", fontsize=11, fontweight="bold")
    note(f, "Above the dashed line the pool's liquidity earned more in fees than it lost to later price moves. Labels are the 1-hour values. Sessions in New York time; whole data window per pool. Pharaoh WAVAX/USDC is not shown: its LPs receive no swap fee, so its ratio is zero in every session.")
    return save(f, f"q1_ratio_by_session{tag}.png")


def chart_ratio_by_vol(res, tag=""):
    f, ax = fig(7.6, 3.5)
    qs = ("Q1", "Q2", "Q3", "Q4")
    w = 0.8 / len(res)
    for k, (v, o) in enumerate(res.items()):
        r = [o["by_vol_bucket"][q].get("fee_over_loss_3600") or np.nan for q in qs]
        b = ax.bar(np.arange(4) - 0.4 + w * (k + 0.5), [min(x, 9.0) for x in r], w * 0.92, color=VENUE_COLOR[v], label=VENUES[v].label.split(" (")[0])
        for bb, val in zip(b, r):
            ax.annotate(f"{val:.1f}" if val < 100 else f"{val:.0f}", (bb.get_x() + bb.get_width() / 2, min(val, 9.0)), xytext=(0, 2), textcoords="offset points",
                        ha="center", va="bottom", fontsize=7.5, color=INK)
        ax.set_ylim(0, 9.8)
    ax.axhline(1.0, color=INK, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.set_xticks(np.arange(4)); ax.set_xticklabels(["calmest quarter", "second", "third", "most volatile quarter"])
    ax.set_ylabel("LP fee income / arbitrage loss (+1 h)")
    ax.set_title("Fee income divided by arbitrage loss falls as volatility rises (dashed line = break-even)")
    ax.legend(loc="upper right")
    note(f, "Each session run (a regular session, an overnight, a weekend ...) is ranked by its own realised volatility within its pool; quarters of time. Bars above 9 are cut off and labelled.")
    return save(f, f"q1_ratio_by_vol{tag}.png")


def chart_horizon(res, tag=""):
    """Arbitrage loss as a share of LP fee income against the markout horizon."""
    f, ax = fig(7.6, 3.5)
    hs = (0, 60, 300, 3600, 14400, 86400)
    for v, o in res.items():
        t = o["total"]
        y = [t[f"loss_{h}_usd"] / t["lp_fee_usd_scored"] for h in hs]
        ax.plot(range(len(hs)), y, marker="o", markersize=5, color=VENUE_COLOR[v], label=VENUES[v].label.split(" (")[0])
        ax.annotate(f"{y[-1]:.2f}", (len(hs) - 1, y[-1]), xytext=(6, -3), textcoords="offset points", fontsize=8, color=INK)
    ax.axhline(1.0, color=INK, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.set_xticks(range(len(hs))); ax.set_xticklabels([HLAB[h] for h in hs])
    ax.set_xlim(-0.3, len(hs) - 0.4)
    ax.set_yscale("log")
    ax.set_yticks([0.02, 0.05, 0.1, 0.2, 0.5, 1, 2]); ax.set_yticklabels(["0.02", "0.05", "0.1", "0.2", "0.5", "1", "2"])
    ax.set_ylabel("arbitrage loss / LP fee income (log scale)")
    ax.set_xlabel("price used to value each swap's inventory change")
    ax.set_title("Arbitrage loss relative to fees, by markout horizon (above 1.0 = loss exceeds fees)")
    ax.legend(loc="lower right")
    note(f, "Pool mid price h after each swap. Short horizons miss slow information; long horizons add inventory noise. +1 min and +1 h are the report's horizons.")
    return save(f, f"q1_horizon{tag}.png")


def chart_daily(res, tag=""):
    n = len(res)
    f, axes = fig(8.4, 2.5 * n, nrows=n)
    axes = np.atleast_1d(axes)
    for ax, (v, o) in zip(axes, res.items()):
        d = o["by_day"]
        keys = sorted(d)[:-1]
        fee = np.cumsum([d[k]["lp_fee_usd_scored"] for k in keys])
        loss = np.cumsum([d[k]["loss_3600"] for k in keys])
        x = np.arange(len(keys))
        ax.plot(x, fee, color=C_FEE, label="LP fee income (cumulative)")
        ax.plot(x, loss, color=C_LOSS, label="arbitrage loss, +1 h (cumulative)")
        ax.annotate(f"${fee[-1]:,.0f}", (x[-1], fee[-1]), xytext=(5, 0), textcoords="offset points", fontsize=8, color=INK, va="center")
        ax.annotate(f"${loss[-1]:,.0f}", (x[-1], loss[-1]), xytext=(5, 0), textcoords="offset points", fontsize=8, color=INK, va="center")
        step = max(1, len(keys) // 8)
        ax.set_xticks(x[::step]); ax.set_xticklabels([k[5:] for k in keys[::step]])
        ax.set_xlim(0, len(keys) * 1.08)
        ax.set_title(VENUES[v].label.split(" (")[0] + ": whole pool, USD", fontsize=10)
        ax.yaxis.set_major_formatter(lambda y, _: f"${y:,.0f}")
    axes[0].legend(loc="upper left")
    note(f, "All liquidity in the pool together. Fee = LP share of the swap fee in force; loss = each swap's inventory change valued one hour later.")
    return save(f, f"q1_cumulative{tag}.png")


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    stock = load(["v4_nvda", "v4_spy", "ramses_nvda", "ramses_spy"], tag)      # Pharaoh pays LPs no fee: its ratio is zero everywhere
    print(chart_ratio_by_session(stock, tag))
    print(chart_ratio_by_vol(stock, tag))
    print(chart_horizon(stock, tag))
    print(chart_daily(load(["v4_nvda", "ramses_nvda", "ramses_spy"], tag), tag))
