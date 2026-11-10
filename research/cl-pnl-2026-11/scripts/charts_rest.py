"""Charts for questions 2 to 6. Reads out/*.json; writes charts/*.png. Each chart function is skipped if its inputs are missing."""
from __future__ import annotations

import json

import numpy as np

from chartlib import (AQUA, BLUE, C_FEE, C_HEDGED, C_HOLD, C_LOSS, C_REWARD, GRID, INK, INK2, ORANGE, SURFACE, VENUE_COLOR, VIOLET, YELLOW, fig, note,
                      save, zero_line)
from lib_cl import OUT, VENUES

short = lambda v: VENUES[v].label.split(" (")[0].replace("Uniswap ", "")


def jload(name):
    p = OUT / name
    return json.loads(p.read_text()) if p.exists() else None


# ------------------------------------------------------------------------------------------------ Q2
def chart_q2_rolling():
    venues = [v for v in ("v4_nvda", "v4_spy", "ramses_nvda", "ramses_spy", "pharaoh_wavax") if jload(f"q2_{v}_1000_mpd4.json")]
    if not venues:
        return
    f, axes = fig(8.6, 1.75 * len(venues) + 0.5, nrows=len(venues))
    cells = (((600, 300), ORANGE, "±600 / 300"), ((2400, 600), BLUE, "±2,400 / 600 (live policy)"), ((4800, 1200), AQUA, "±4,800 / 1,200"))
    for ax, v in zip(np.atleast_1d(axes), venues):
        o = jload(f"q2_{v}_1000_mpd4.json")
        labs = [w for w in o["windows"] if w != "full"]
        x = np.arange(len(labs))
        for k, ((h, t), col, lab) in enumerate(cells):
            y = []
            for w in labs:
                r = next((r for r in o["rows"] if r["half"] == h and r["trigger"] == t and r["window"] == w and "error" not in r), None)
                y.append(np.nan if r is None else r["excess_mark_pct"])
            ax.bar(x - 0.28 + 0.28 * k, y, 0.26, color=col, label=lab)
        zero_line(ax)
        ax.set_xticks(x); ax.set_xticklabels([w[9:] for w in labs], fontsize=7.5)
        ax.set_title(short(v), fontsize=9.5)
        ax.yaxis.set_major_formatter(lambda val, _: "0%" if val == 0 else (f"{val:+.0f}%" if abs(val) >= 1 else f"{val:+.1f}%"))
    np.atleast_1d(axes)[0].legend(loc="upper right", ncol=3, fontsize=7.5)
    f.suptitle("Net return against holding, by rolling 30-day window ($1,000; axis = window start)", x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    note(f, "After the pool's protocol share, the 20% reserve cut and recentre gas; rewards not included. Each panel has its own scale. Windows start seven days apart and overlap.")
    return save(f, "q2_rolling_windows.png")


# ------------------------------------------------------------------------------------------------ Q3
def chart_q3_bands():
    venues = [("ramses_nvda", ""), ("ramses_spy", ""), ("pharaoh_wavax", ""), ("v4_nvda", "")]
    data = [(v, jload(f"q3_{v}_1000_7d{t}.json")) for v, t in venues]
    data = [(v, o) for v, o in data if o]
    if not data:
        return
    f, axes = fig(2.25 * len(data) + 0.6, 4.1, ncols=len(data), sharey=True)
    lab = {100: "1%", 300: "3%", 490: "5%"}
    for ax, (v, o) in zip(np.atleast_1d(axes), data):
        ys = np.arange(3)[::-1]
        for y, d in zip(ys, (100, 300, 490)):
            r = o["results"][f"sell_{d}"]
            x = r["diff_vs_limit_bps"]
            ax.hlines(y, x["p05"], x["p95"], color="#b9c4d0", linewidth=5, zorder=2)
            ax.plot([x["mean"]], [y], marker="D", markersize=6.5, color=C_LOSS, linestyle="none", zorder=4, label="mean difference" if d == 100 else None)
            ax.plot([r["fees_bps_mean"]], [y + 0.26], marker="o", markersize=6, color=C_FEE, linestyle="none", zorder=4, label="fees kept (mean)" if d == 100 else None)
            if r["reward_bps_if_sold_mean"] > 0:
                ax.plot([r["reward_bps_if_sold_mean"]], [y - 0.26], marker="o", markersize=6, color=C_REWARD, linestyle="none", zorder=4,
                        label="reward if sold (mean)" if d == 100 else None)
            ax.annotate(f"filled {100 * r['fill_prob']:.0f}% | limit {100 * r['limit_fill_prob']:.0f}%", (0.02, y + 0.36), xycoords=("axes fraction", "data"), fontsize=7,
                        color=INK2, va="bottom")
        ax.axvline(0, color=INK2, linewidth=0.8, zorder=1)
        ax.set_yticks(ys); ax.set_yticklabels([lab[d] + " away" for d in (100, 300, 490)])
        ax.set_ylim(-0.6, 2.75)
        ax.set_xlim(-760, 420)
        ax.set_title(short(v), fontsize=9.5)
        ax.grid(axis="y", visible=False); ax.grid(axis="x", visible=True)
        ax.set_xlabel("bp of the order", fontsize=8)
    handles, labels = np.atleast_1d(axes)[0].get_legend_handles_labels()
    h2, l2 = np.atleast_1d(axes)[2].get_legend_handles_labels() if len(data) > 2 else ([], [])
    for h, l in zip(h2, l2):
        if l not in labels:
            handles.append(h); labels.append(l)
    from matplotlib.lines import Line2D
    handles.append(Line2D([0], [0], color="#b9c4d0", linewidth=5)); labels.append("5th to 95th percentile of band minus limit")
    f.legend(handles, labels, loc="lower center", ncol=4, fontsize=7.5, bbox_to_anchor=(0.5, 0.075))
    f.suptitle("Take-profit band against a limit order at the same price: fees are small beside the spread of outcomes", x=0.01, ha="left", fontsize=10.5,
               fontweight="bold")
    note(f, "$1,000 take-profit bands, 2% wide, followed for 7 days on real swaps. The grey bar is the 5th to 95th percentile of (band result minus limit-order result); the v4 pool is not a venue the prototype targets and is shown for its 0.30% fee.")
    f._note_lines = getattr(f, "_note_lines", 2) + 2
    return save(f, "q3_bands.png")


# ------------------------------------------------------------------------------------------------ Q4
def chart_q4_hedged():
    venues = [v for v in ("v4_nvda", "ramses_nvda", "pharaoh_wavax") if jload(f"q4_{v}_h2400_t600.json")]
    if not venues:
        return
    f, ax = fig(8.4, 3.9)
    groups, pre, post, rew = [], [], [], []
    for v in venues:
        o = jload(f"q4_{v}_h2400_t600.json")
        for size in (1000, 10000):
            r = next(x for x in o["rows"] if x.get("window") == "full" and x["size"] == size and abs(x["threshold"] - 0.05) < 1e-9)
            groups.append(f"{short(v).split('/')[0]}\n${size:,}")
            pre.append(r["hedged_before_hedge_costs_pct"]); post.append(r["return_pct"]["hedged"])
            rew.append(np.nan if r["hedged_plus_rewards_pct"] is None else r["hedged_plus_rewards_pct"])
    x = np.arange(len(groups))
    b1 = ax.bar(x - 0.27, pre, 0.25, color="#9ec3ee", label="before hedge costs")
    b2 = ax.bar(x, post, 0.25, color=C_HEDGED, label="after swap fees, price impact, interest and gas")
    b3 = ax.bar(x + 0.27, rew, 0.25, color=C_REWARD, label="after costs, plus rewards if sold")
    for bars in (b1, b2, b3):
        for b in bars:
            h = b.get_height()
            if h == h:
                ax.annotate(f"{h:+.1f}%", (b.get_x() + b.get_width() / 2, h), xytext=(0, 3 if h >= 0 else -10), textcoords="offset points", ha="center", fontsize=7.5, color=INK)
    zero_line(ax)
    ax.set_xticks(x); ax.set_xticklabels(groups, fontsize=8)
    ax.set_ylabel("return on starting equity (LP capital + margin)")
    ax.yaxis.set_major_formatter(lambda val, _: "0%" if val == 0 else f"{val:+.0f}%")
    ax.set_title("Hedged range position, whole window: negative after costs at both sizes")
    ax.legend(loc="lower left", fontsize=7.5)
    note(f, "±2,400 / 600 range with its stock delta shorted, re-hedged when drift exceeds 5% of LP value, borrow at 12% a year. Windows: v4 NVDA 16 Jul to 10 Nov, Ramses NVDA 4 Aug to 10 Nov, Pharaoh 1 Aug to 10 Nov. Uniswap pays no rewards. None of these hedges can be opened today.")
    return save(f, "q4_hedged.png")


# ------------------------------------------------------------------------------------------------ Q5
def chart_q5_timing():
    o = jload("q5_ramses_nvda.json")
    if not o:
        return
    f, ax = fig(7.6, 3.5)
    rows = o["loss_share"]["settle_before_admit"]
    for hourly, col, lab in ((True, C_LOSS, "settle before admit, keeper also rebalances hourly"), (False, C_FEE, "settle before admit, keeper only recentres")):
        pts = sorted((r["admits_per_day"], r["lost_pct_mean"]) for r in rows if r["hourly_rebalance"] == hourly and r["unwinds_per_day"] == 0)
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", markersize=5, color=col, label=lab)
        ax.annotate(f"{pts[-1][1]:.2f}%", (pts[-1][0], pts[-1][1]), xytext=(6, -3), textcoords="offset points", fontsize=8, color=INK)
    cur = next(r for r in o["loss_share"]["current"] if not r["hourly_rebalance"] and r["unwinds_per_day"] == 0)
    ax.axhline(cur["lost_pct_mean"], color=INK2, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.annotate("current keeper rules, and claim only at a recentre: 0%", (10, cur["lost_pct_mean"]), xytext=(0, 5), textcoords="offset points", fontsize=8, color=INK2)
    ax.set_xscale("log")
    ax.set_xlim(8, 30000)
    ax.set_xlabel("deposits and withdrawals settled per day")
    ax.set_ylabel("share of RAM lost to the 99-second rule")
    ax.yaxis.set_major_formatter(lambda val, _: f"{val:.1f}%")
    ax.set_title("RAM lost to the gauge's 99-second rule, by policy and user activity")
    ax.legend(loc="upper left", fontsize=7.5)
    note(f, "Simulated on the engine's recentre timeline, 30 Sep to 10 Nov; no position of ours has earned RAM on mainnet. The ceiling with an hourly rebalance is 99 s of every hour, 2.75%.")
    return save(f, "q5_reward_timing.png")


# ------------------------------------------------------------------------------------------------ Q6
def chart_q6_session():
    venues = [v for v in ("v4_nvda", "ramses_nvda", "ramses_spy") if jload(f"q6_{v}_1000.json")]
    if not venues:
        return
    plans = (("weekend_wide", "wide over\nthe weekend"), ("weekend_narrow", "narrow over\nthe weekend"), ("hours_wide", "wide in\nregular hours"))
    f, axes = fig(2.9 * len(venues) + 0.4, 3.8, ncols=len(venues))
    for ax, v in zip(np.atleast_1d(axes), venues):
        o = jload(f"q6_{v}_1000.json")
        n, w = [1200, 300], [2400, 600]
        wins = [x for x in o["windows"] if x != "full"]
        get = lambda plan, win: next((r["excess_mark_pct"] for r in o["rows"] if r["narrow"] == n and r["wide"] == w and r["plan"] == plan and r["window"] == win and "error" not in r), None)
        for k, (plan, lab) in enumerate(plans):
            d = [get(plan, wn) - max(get("fixed_narrow", wn), get("fixed_wide", wn)) for wn in wins
                 if None not in (get(plan, wn), get("fixed_narrow", wn), get("fixed_wide", wn))]
            jitter = (np.arange(len(d)) - (len(d) - 1) / 2) * (0.5 / max(len(d), 1))
            ax.plot(k + jitter, d, linestyle="none", marker="o", markersize=5, markerfacecolor=SURFACE, markeredgecolor=BLUE, markeredgewidth=1.3, zorder=3)
            ax.hlines(np.median(d), k - 0.32, k + 0.32, color=ORANGE, linewidth=2.2, zorder=4)
            ax.annotate(f"{sum(x > 0 for x in d)} of {len(d)}", (k, ax.get_ylim()[1]), xytext=(0, 0), textcoords="offset points", fontsize=1, color=SURFACE)
            ax.text(k, 0.97, f"{sum(x > 0 for x in d)} of {len(d)}\nahead", transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=7.5, color=INK2)
        zero_line(ax)
        ax.set_xticks(range(len(plans))); ax.set_xticklabels([p[1] for p in plans], fontsize=8)
        ax.set_xlim(-0.6, len(plans) - 0.4)
        ax.set_title(short(v), fontsize=9.5)
        ax.yaxis.set_major_formatter(lambda val, _: "0" if val == 0 else f"{val:+.1f}")
        lo, hi = ax.get_ylim(); ax.set_ylim(lo, hi + 0.3 * (hi - lo))
    np.atleast_1d(axes)[0].set_ylabel("plan minus the better fixed range,\npoints of return per 30-day window")
    f.suptitle("Session-aware width minus the better fixed range (dot = one 30-day window, bar = median)", x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    note(f, "$1,000 position; narrow = ±1,200 / 300, wide = ±2,400 / 600. Above zero the plan beat both fixed ranges in that window. Each panel has its own scale.")
    return save(f, "q6_session.png")


# ------------------------------------------------------------------------------------------------ Q7
def chart_q7_blackhole():
    o = jload("q7_blackhole_vs_pharaoh.json")
    if not o:
        return
    f, axes = fig(8.6, 3.7, ncols=2, sharey=True)
    for ax, v in zip(axes, ("pharaoh_wavax", "blackhole_wavax")):
        w = o[v]["weeks"]
        x = np.arange(len(w))
        ax.bar(x - 0.2, [r["reward_per_1000_in_range"] for r in w], 0.38, color=C_REWARD, label="emissions, value if sold")
        ax.bar(x + 0.2, [r["loss_per_1000_in_range"] for r in w], 0.38, color=C_LOSS, label="arbitrage loss (+1 h)")
        ax.set_xticks(x[::2]); ax.set_xticklabels([r["week_start"][5:] for r in w][::2], fontsize=8)
        t = o[v]["total"]
        ax.set_title(f"{short(v).split(' ')[0]} ({o[v]['token']}): {100 * t['reward_over_loss']:.0f}% of the loss covered", fontsize=9.5)
        ax.yaxis.set_major_formatter(lambda val, _: f"${val:,.0f}")
    axes[0].set_ylabel("per $1,000 of in-range liquidity, per week")
    axes[0].legend(loc="upper left", fontsize=7.5)
    f.suptitle("Emissions against arbitrage loss on the two WAVAX/USDC pools, week by week", x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    note(f, "Whole pool. In-range liquidity = dollar value of active liquidity within ±1% of the price, time-weighted over the week. Neither pool pays its LPs any swap fee. Emissions are valued at the reward token's volume-weighted price in the week, with no sale cost.")
    return save(f, "q7_blackhole_vs_pharaoh.png")


# ------------------------------------------------------------------------------------------------ Q8
Q8_SET = (("v4_nvda", "", "v4 NVDA\n0.30%"), ("univ3_nvda_005", "", "V3 NVDA\n0.05%"), ("ramses_nvda", "_share50", "Ramses NVDA\n50% share"),
          ("v4_spy", "", "v4 SPY\n0.30%"), ("univ3_spy_005", "", "V3 SPY\n0.05%"), ("ramses_spy", "_share50", "Ramses SPY\n50% share"))


def chart_q8_windows():
    data = [(lab, jload(f"q8_{v}{tag}.json")) for v, tag, lab in Q8_SET]
    data = [(lab, o) for lab, o in data if o]
    if not data:
        return
    f, ax = fig(8.6, 4.0)
    for i, (lab, o) in enumerate(data):
        for g, off, col, name in (("full", -0.18, BLUE, "full range"), ("pm50", 0.18, ORANGE, "±50% band")):
            xs = np.array([r["excess_mark_pct"] for r in o["rows"] if r["geometry"] == g and r["window"] != "full" and "error" not in r])
            if not len(xs):
                continue
            jit = (np.arange(len(xs)) % 9 - 4) * 0.012
            ax.plot(i + off + jit, xs, linestyle="none", marker="o", markersize=2.6, color=col, alpha=0.55, zorder=2, label=name if i == 0 else None)
            ax.hlines(np.median(xs), i + off - 0.13, i + off + 0.13, color=INK, linewidth=1.6, zorder=4)
            ax.plot([i + off], [np.percentile(xs, 5)], marker="_", markersize=12, markeredgewidth=2.0, color=col, zorder=5)
    zero_line(ax)
    ax.set_xticks(range(len(data))); ax.set_xticklabels([d[0] for d in data], fontsize=8)
    ax.set_ylabel("net return against holding, 30-day window")
    ax.yaxis.set_major_formatter(lambda val, _: "0%" if val == 0 else f"{val:+.1f}%")
    ax.set_title("Full range and ±50%: every rolling 30-day window")
    ax.legend(loc="lower left", fontsize=7.5, markerscale=2.5)
    note(f, "Each dot is one window; black bar = median, coloured dash = 5th percentile. $1,000 position, 50/50 deposit, no recentre; after the pool's protocol share and the 20% reserve cut. Windows start one day apart and overlap heavily. Ramses pools use today's 50% LP share for the whole history.")
    return save(f, "q8_windows.png")


def chart_q8_reserve():
    data = [(lab, jload(f"q8_{v}{tag}.json")) for v, tag, lab in Q8_SET]
    data = [(lab, o) for lab, o in data if o]
    if not data:
        return
    f, axes = fig(8.6, 3.7, ncols=2, sharey=True)
    for ax, g, name in zip(axes, ("full", "pm50"), ("Full range", "±50% band")):
        x = np.arange(len(data))
        cost = [o["summary"][g]["cost_pct_per_year_capped_at_p95"] if g in o["summary"] else np.nan for _, o in data]
        inc = [o["summary"][g]["reserve_income_pct_per_year_from_20pct_fee_cut"] if g in o["summary"] else np.nan for _, o in data]
        b1 = ax.bar(x - 0.2, cost, 0.38, color=C_LOSS, label="reserve cost per year (95% of windows made whole)")
        b2 = ax.bar(x + 0.2, inc, 0.38, color=C_FEE, label="brought in per year by the 20% fee cut")
        for bars in (b1, b2):
            for b in bars:
                h = b.get_height()
                if h == h:
                    ax.annotate(f"{h:.2f}", (b.get_x() + b.get_width() / 2, h), xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7, color=INK)
        ax.set_xticks(x); ax.set_xticklabels([d[0] for d in data], fontsize=7.2)
        ax.set_title(name, fontsize=9.5)
        ax.set_yticks([0, 0.5, 1.0, 1.5, 2.0, 2.5])
        ax.yaxis.set_major_formatter(lambda val, _: f"{val:.1f}%")
    axes[0].set_ylabel("% of deposits per year")
    axes[0].legend(loc="upper right", fontsize=7.2)
    f.suptitle("Loss-protection reserve: yearly payout against what the 20% fee cut brings in", x=0.01, ha="left", fontsize=10.5, fontweight="bold")
    note(f, "Cost = average payout per 30-day window when each depositor's shortfall against holding is paid up to the 95th-percentile shortfall, times 365/30. Realised over each pool's history; not a forecast.")
    return save(f, "q8_reserve.png")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("chart_q")]:
        try:
            print(fn.__name__, fn())
        except Exception as e:  # a missing input must not hide the charts that can be drawn
            print(fn.__name__, "FAILED", repr(e))
