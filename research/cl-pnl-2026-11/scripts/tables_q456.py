"""Markdown tables for Q4 (hedged), Q5 (reward timing) and Q6 (session-aware width) from out/*.json."""
import json
import statistics
import sys

from lib_cl import OUT, VENUES


def short(v):
    lab = VENUES[v].label
    return lab if "Uniswap" in lab else lab.split(" (")[0]


def jl(name):
    p = OUT / name
    return json.loads(p.read_text()) if p.exists() else None


def q4(venue, half=2400, trig=600):
    o = jl(f"q4_{venue}_h{half}_t{trig}.json")
    if not o:
        return
    par = o["params"]
    print(f"\n### {short(venue)}: ±{half:,} / {trig}, hedge traded on {short(o['hedge_venue'])}, borrow at {100 * par['borrow_apr']:.0f}% a year, {o['data_window'][0][:10]} to {o['data_window'][1][:10]}\n")
    print("| Size | Rebalance at | Hedged | Unhedged LP | Hold | Hedged − hold | Hedged before hedge costs | Swap fees | Price impact | Interest (net) | Gas | Adjustments | Blocked samples | Rewards if sold | Hedged + rewards | Lowest margin ratio |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    rows = [r for r in o["rows"] if r.get("window") == "full" and "error" not in r]
    for r in rows:
        hl, s = r["hedge_leg"], r["start_equity"]
        pct = lambda x: f"{100 * x / s:.2f}%"
        rw = r["rewards"]["usd_if_sold"]
        print(f"| ${r['size']:,.0f} | {100 * r['threshold']:.0f}% | {r['return_pct']['hedged']:+.2f}% | {r['return_pct']['unhedged']:+.2f}% | {r['return_pct']['hold']:+.2f}% | "
              f"{r['hedged_vs_hold_pct']:+.2f}% | {r['hedged_before_hedge_costs_pct']:+.2f}% | {pct(hl['swap_fee'])} | {pct(hl['impact'])} | "
              f"{pct(hl['borrow_interest'] - hl['supply_interest'])} | {pct(hl['gas'])} | {hl['adjustments']} | {r['samples_wanting_adjust_but_blocked']} | "
              f"{'n/a' if rw is None else pct(rw)} | {'n/a' if r['hedged_plus_rewards_pct'] is None else format(r['hedged_plus_rewards_pct'], '+.2f') + '%'} | {r['worst_margin_ratio']:.2f} |")
    wins = sorted({r["window"] for r in o["rows"] if r.get("window") != "full" and "error" not in r})
    if wins:
        print(f"\nRolling 30-day windows ({len(wins)}), hedged return on starting equity (LP capital plus margin), and with rewards if sold:\n")
        print("| Size | Rebalance at | Worst | Median | Best | Windows above zero | With rewards: worst | median | windows above zero |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for size in sorted({r["size"] for r in rows}):
            for thr in sorted({r["threshold"] for r in rows}):
                xs = sorted(r["return_pct"]["hedged"] for r in o["rows"] if r.get("window") in wins and r["size"] == size and r["threshold"] == thr)
                ys = sorted(r["hedged_plus_rewards_pct"] for r in o["rows"] if r.get("window") in wins and r["size"] == size and r["threshold"] == thr
                            and r["hedged_plus_rewards_pct"] is not None)
                rw = f"{ys[0]:+.2f}% | {statistics.median(ys):+.2f}% | {sum(y > 0 for y in ys)} of {len(ys)}" if ys else "n/a | n/a | n/a"
                print(f"| ${size:,.0f} | {100 * thr:.0f}% | {xs[0]:+.2f}% | {statistics.median(xs):+.2f}% | {xs[-1]:+.2f}% | {sum(x > 0 for x in xs)} of {len(xs)} | {rw} |")


def q5(venue):
    o = jl(f"q5_{venue}.json")
    if not o:
        return
    print(f"\n### {short(venue)}: {o['token']}, withholding window {o['threshold_s']:.0f} s, ±{o['policy_range'][0]:,} / {o['policy_range'][1]}, {o['window'][0][:10]} to {o['window'][1][:10]}\n")
    print("| Position | Reward accrued | Value if sold | Share of starting capital | LP fees | LVR | Recentres |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for k, s in o["sizes"].items():
        print(f"| ${int(k):,} | {s['reward_tokens']:,.2f} {o['token']} | ${s['reward_usd_if_sold']:,.2f} | {s['reward_pct_of_start_if_sold']:.2f}% | ${s['lp_fees_usd']:,.2f} | ${s['lvr_usd']:,.2f} | {s['recentres']} |")
    print("\n| Policy | Hourly rebalance | User unwinds a day | Settlements a day | Reward lost (mean) | Highest trial |")
    print("| --- | --- | --- | --- | --- | --- |")
    for pol, rows in o["loss_share"].items():
        for r in rows:
            if r["unwinds_per_day"] in (1.0,) or (pol == "settle_before_admit" and r["unwinds_per_day"] > 0) or (pol != "settle_before_admit" and r["hourly_rebalance"] and r["unwinds_per_day"] == 100):
                continue
            print(f"| {pol.replace('_', ' ')} | {'yes' if r['hourly_rebalance'] else 'no'} | {r['unwinds_per_day']:.0f} | {r['admits_per_day']:.0f} | {r['lost_pct_mean']:.3f}% | {r['lost_pct_max']:.3f}% |")


def q6(venue, capital=1000):
    o = jl(f"q6_{venue}_{capital}.json")
    if not o:
        return
    wins = [w for w in o["windows"] if w != "full"]
    print(f"\n### {short(venue)}, ${capital:,}, {o['data_window'][0][:10]} to {o['data_window'][1][:10]}, {len(wins)} rolling 30-day windows\n")
    print("| Narrow / wide | Plan | Full window | Fees | LVR | Recentres (plan switches) | Capital deployed | Worst 30 d | Median 30 d | Best 30 d | Windows beating both fixed ranges |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    names = {"fixed_narrow": "fixed narrow", "fixed_wide": "fixed wide", "weekend_wide": "wide over the weekend", "weekend_narrow": "narrow over the weekend",
             "hours_wide": "wide in regular hours"}
    pairs = []
    for r in o["rows"]:
        k = (tuple(r["narrow"]), tuple(r["wide"]))
        if k not in pairs:
            pairs.append(k)
    for n, w in pairs:
        sel = lambda plan, win: next((r for r in o["rows"] if tuple(r["narrow"]) == n and tuple(r["wide"]) == w and r["plan"] == plan and r["window"] == win and "error" not in r), None)
        for plan in names:
            f = sel(plan, "full")
            if not f:
                continue
            xs = {wn: sel(plan, wn)["excess_mark_pct"] for wn in wins if sel(plan, wn)}
            vals = sorted(xs.values())
            beats = ""
            if not plan.startswith("fixed"):
                fn = {wn: sel("fixed_narrow", wn)["excess_mark_pct"] for wn in wins if sel("fixed_narrow", wn)}
                fw = {wn: sel("fixed_wide", wn)["excess_mark_pct"] for wn in wins if sel("fixed_wide", wn)}
                both = [wn for wn in xs if wn in fn and wn in fw]
                beats = f"{sum(xs[wn] > max(fn[wn], fw[wn]) for wn in both)} of {len(both)}"
            print(f"| ±{n[0]:,}/{n[1]} and ±{w[0]:,}/{w[1]} | {names[plan]} | {f['excess_mark_pct']:+.2f}% | ${f['fees_usd']:.2f} | ${f['lvr_usd']:.2f} | {f['recenters_core']} ({f.get('plan_switches', 0)}) | "
                  f"{100 * f['deployed_frac_avg']:.0f}% | {vals[0]:+.2f}% | {statistics.median(vals):+.2f}% | {vals[-1]:+.2f}% | {beats} |")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("q4", "all"):
        for v in ("v4_nvda", "ramses_nvda", "pharaoh_wavax"):
            q4(v)
    if which in ("q5", "all"):
        for v in ("ramses_nvda", "ramses_spy", "pharaoh_wavax"):
            q5(v)
    if which in ("q6", "all"):
        for v in ("v4_nvda", "ramses_nvda", "ramses_spy", "pharaoh_wavax"):
            q6(v)
