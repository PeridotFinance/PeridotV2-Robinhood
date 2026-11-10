"""Markdown tables for Q7 (Blackhole against Pharaoh) and Q8 (full range and +/-50%) from out/*.json."""
import json
import sys

from lib_cl import OUT, VENUES

Q8 = (("v4_nvda", "", "Uniswap v4 NVDA/USDG 0.30%"), ("univ3_nvda_005", "", "Uniswap V3 NVDA/USDG 0.05%"), ("ramses_nvda", "_share50", "Ramses NVDA/USDG at a 50% fee share"),
      ("v4_spy", "", "Uniswap v4 SPY/USDG 0.30%"), ("univ3_spy_005", "", "Uniswap V3 SPY/USDG 0.05%"), ("ramses_spy", "_share50", "Ramses SPY/USDG at a 50% fee share"))
GEOM = {"full": "full range", "pm50": "±50%"}


def jl(name):
    p = OUT / name
    return json.loads(p.read_text()) if p.exists() else None


def unit_ratio(v):
    """Fee / loss (+1 h) per unit of liquidity over the Q8 window, from the q1 run made on that window: exactly the
    ratio of a position that stays in range with constant liquidity (LP fee before the vault's reserve cut)."""
    o = jl(f"q1_{v}_q8.json")
    if not o:
        return None
    d = o["by_day"]
    ks = sorted(d)[:-1]
    f = [d[k]["ref_fee"] for k in ks]
    l = [d[k]["ref_loss_3600"] for k in ks]
    roll = [sum(f[i:i + 30]) / sum(l[i:i + 30]) for i in range(len(ks) - 29)]
    return {"whole": sum(f) / sum(l), "min": min(roll), "median": sorted(roll)[len(roll) // 2], "above": sum(x > 1 for x in roll) / len(roll), "n": len(roll)}


def q8():
    print("### Q8.0 Fee ÷ loss (+1 h) per unit of liquidity over the same windows (the ratio of any position that stays in range; LP fee before the reserve cut)\n")
    print("| Pool | Whole window | Lowest 30-day window | Median 30-day window | 30-day windows above 1.0 |")
    print("| --- | --- | --- | --- | --- |")
    for v, tag, lab in Q8:
        u = unit_ratio(v)
        if u:
            print(f"| {lab} | {u['whole']:.2f} | {u['min']:.2f} | {u['median']:.2f} | {100 * u['above']:.0f}% of {u['n']} |")
    print()
    print("### Q8.A Net return against holding per rolling 30-day window ($1,000; after protocol share and the 20% reserve cut)\n")
    print("| Pool | Window | Geometry | Capital deployed | Whole window | 30-day windows | Worst | 5th percentile | Median | Best | At or above holding |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v, tag, lab in Q8:
        o = jl(f"q8_{v}{tag}.json")
        if not o:
            continue
        for g in ("full", "pm50"):
            s = o["summary"].get(g)
            if not s:
                continue
            f = s["full_window"]
            print(f"| {lab} | {o['data_window'][0][:10]} to {o['data_window'][1][:10]} | {GEOM[g]} | {100 * f['deployed_frac_avg']:.0f}% | {f['excess_mark_pct']:+.2f}% | {s['windows']} | "
                  f"{s['return_min']:+.2f}% | {s['return_p05']:+.2f}% | {s['return_median']:+.2f}% | {s['return_max']:+.2f}% | {100 * s['share_at_or_above_holding']:.0f}% |")
    print("\n### Q8.B Loss-protection reserve for a $1,000 depositor held 30 days (% of deposits)\n")
    print("| Pool | Geometry | Standing size: 95th-percentile shortfall of one window | Largest shortfall seen | Cost per year, paying shortfalls up to that size | Cost per year, paying every shortfall in full | Brought in per year by the 20% fee cut | Fee cut ÷ cost | Holders' fee income per year |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v, tag, lab in Q8:
        o = jl(f"q8_{v}{tag}.json")
        if not o:
            continue
        for g in ("full", "pm50"):
            s = o["summary"].get(g)
            if not s:
                continue
            c = s["cost_pct_per_year_capped_at_p95"]
            inc = s["reserve_income_pct_per_year_from_20pct_fee_cut"]
            print(f"| {lab} | {GEOM[g]} | {s['shortfall_p95_pct']:.2f}% | {s['shortfall_max_pct']:.2f}% | {c:.2f}% | {s['cost_pct_per_year_uncapped']:.2f}% | {inc:.2f}% | "
                  f"{(inc / c if c > 0 else float('inf')):.2f} | {s['holder_fee_income_pct_per_year']:.2f}% |")


def q7():
    o = jl("q7_blackhole_vs_pharaoh.json")
    if not o:
        return
    print("### Q7.A Whole pool, full weeks, per $1,000 of in-range liquidity\n")
    print("| Pool | Weeks | Reward token | Emissions, value if sold | Swap fees paid (none to LPs) | Loss at +1 h | Mean in-range liquidity (±1%) | Emissions per $1,000 in range per week | Loss per $1,000 in range per week | Emissions ÷ loss | Weeks emissions exceeded loss |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in ("pharaoh_wavax", "blackhole_wavax"):
        t = o[v]["total"]
        print(f"| {VENUES[v].label.split(' (')[0]} | {t['weeks']} | {o[v]['token']} | ${t['reward_usd']:,.0f} | ${t['swap_fee_usd']:,.0f} | ${t['loss_3600_usd']:,.0f} | "
              f"${t['in_range_1pct_usd_mean']:,.0f} | ${t['reward_per_1000_in_range_per_week']:.1f} | ${t['loss_per_1000_in_range_per_week']:.1f} | {t['reward_over_loss']:.2f} | "
              f"{t['weeks_reward_above_loss']} of {t['weeks']} |")
    print("\n### Q7.B Week by week\n")
    print("| Week from | Pharaoh: PHAR if sold | Pharaoh: loss | Pharaoh: emissions / loss per $1,000 in range | Blackhole: BLACK if sold | Blackhole: loss | Blackhole: emissions / loss per $1,000 in range |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    pw = {r["week_start"]: r for r in o["pharaoh_wavax"]["weeks"]}
    bw = {r["week_start"]: r for r in o["blackhole_wavax"]["weeks"]}
    for wk in sorted(set(pw) | set(bw)):
        a, b = pw.get(wk), bw.get(wk)
        fa = f"${a['reward_usd']:,.0f} | ${a['loss_3600_usd']:,.0f} | ${a['reward_per_1000_in_range']:.0f} / ${a['loss_per_1000_in_range']:.0f}" if a else "n/a | n/a | n/a"
        fb = f"${b['reward_usd']:,.0f} | ${b['loss_3600_usd']:,.0f} | ${b['reward_per_1000_in_range']:.0f} / ${b['loss_per_1000_in_range']:.0f}" if b else "n/a | n/a | n/a"
        print(f"| {wk} | {fa} | {fb} |")
    print("\n### Q7.C Latest full week against the balances and the quoted depth at the end block\n")
    print("| Pool | Balances at the end block | Within ±1% by quote | Emissions in the latest full week | Per $1,000 of balances | Per $1,000 within ±1% | Loss per $1,000 of balances |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for v in ("pharaoh_wavax", "blackhole_wavax"):
        e = o[v]["at_end"]
        print(f"| {VENUES[v].label.split(' (')[0]} | ${e['balances_usd']:,.0f} | ${e['within_1pct_usd_by_quote']:,.0f} | ${e['last_week_reward_usd']:,.0f} ({e['last_full_week']}) | "
              f"${e['last_week_reward_per_1000_of_balances']:.1f} | ${e['last_week_reward_per_1000_within_1pct_by_quote']:.0f} | ${e['last_week_loss_per_1000_of_balances']:.1f} |")
    print("\n### Q7.D A $1,000 staked position over the whole window\n")
    print("| Pool | Range | Against holding | Swap fees | LVR | Reward tokens | Reward if sold | Against holding with reward | Recentres | Capital deployed |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in ("pharaoh_wavax", "blackhole_wavax"):
        for k, r in o[v]["position_1000"].items():
            h, t = k.split("_")
            print(f"| {VENUES[v].label.split(' (')[0]} | ±{int(h):,} / {t} | {r['excess_mark_pct']:+.2f}% | ${r['fees_usd']:.2f} | ${r['lvr_usd']:.2f} | {r['reward_tokens']:,.0f} {o[v]['token']} | "
                  f"${r['reward_usd_if_sold']:.2f} | {r['excess_mark_pct'] + r['reward_pct_if_sold']:+.2f}% | {r['recenters_core']} | {100 * r['deployed_frac_avg']:.0f}% |")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("q7", "all"):
        q7()
        print()
    if which in ("q8", "all"):
        q8()
