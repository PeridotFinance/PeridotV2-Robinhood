"""Markdown tables for Q1 from out/q1_*.json (printed; the report quotes them verbatim)."""
import json

from lib_cl import OUT, SESSIONS, VENUES

ORDER = ("v4_nvda", "univ3_nvda", "univ3_nvda_005", "ramses_nvda", "v4_spy", "univ3_spy", "univ3_spy_005", "ramses_spy", "pharaoh_wavax")
R = {v: json.loads((OUT / f"q1_{v}.json").read_text()) for v in ORDER if (OUT / f"q1_{v}.json").exists()}
short = lambda v: VENUES[v].label.replace(" (0.30%)", " 0.30%").replace(" (0.05%)", " 0.05%").split(" (")[0]
f2 = lambda x: "n/a" if x is None else f"{x:.2f}"

print("### A. Whole window, all liquidity in the pool\n")
print("| Pool | Window (days) | Volume a day | Swap fee paid | LP share of fee | LP fee income | Loss at +1 min | Loss at +1 h | Fee ÷ loss (+1 min) | Fee ÷ loss (+1 h) |")
print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
for v, o in R.items():
    t = o["total"]
    share = t["lp_fee_usd"] / t["fee_usd"] if t["fee_usd"] else 0
    print(f"| {short(v)} | {o['window'][0][:10]} to {o['window'][1][:10]} ({o['days']:.0f}) | ${t['volume_usd_per_day']:,.0f} | ${t['fee_usd']:,.0f} | {100 * share:.0f}% | "
          f"${t['lp_fee_usd_scored']:,.0f} | ${t['loss_60_usd']:,.0f} | ${t['loss_3600_usd']:,.0f} | {f2(t['fee_over_loss_60'])} | {f2(t['fee_over_loss_3600'])} |")

print("\n### B. Fee ÷ loss (+1 h) by session\n")
print("| Pool | Regular | Extended | Overnight | Weekend |")
print("| --- | --- | --- | --- | --- |")
for v, o in R.items():
    print(f"| {short(v)} | " + " | ".join(f2(o["by_session"][s].get("fee_over_loss_3600")) for s in SESSIONS) + " |")

print("\n### C. Fee ÷ loss (+1 h) by realised-volatility quarter (annualised volatility of the quarter in brackets)\n")
print("| Pool | Calmest | Second | Third | Most volatile |")
print("| --- | --- | --- | --- | --- |")
for v, o in R.items():
    cells = []
    for q in ("Q1", "Q2", "Q3", "Q4"):
        a = o["by_vol_bucket"][q]
        cells.append(f"{f2(a.get('fee_over_loss_3600'))} ({100 * a['sigma_time_weighted']:.0f}%)")
    print(f"| {short(v)} | " + " | ".join(cells) + " |")

print("\n### D. Dollars a day by session: LP fee income and loss at +1 h\n")
print("| Pool | Regular fee / loss | Extended fee / loss | Overnight fee / loss | Weekend fee / loss |")
print("| --- | --- | --- | --- | --- |")
for v, o in R.items():
    cells = []
    for s in SESSIONS:
        a = o["by_session"][s]
        cells.append(f"${a['lp_fee_usd_scored'] / a['days']:,.0f} / ${a['loss_3600_usd'] / a['days']:,.0f}")
    print(f"| {short(v)} | " + " | ".join(cells) + " |")

print("\n### E. Rolling 30-day windows (one-day step), fee ÷ loss (+1 h)\n")
print("| Pool | Windows | Lowest (start) | Highest (start) | Share above 1.0 | Latest (start) |")
print("| --- | --- | --- | --- | --- | --- |")
for v, o in R.items():
    r = o["rolling_30d"]
    a = [x["fee_over_loss_3600"] for x in r]
    if not o["total"]["lp_fee_usd"]:
        print(f"| {short(v)} | {len(r)} | 0 | 0 | 0% | 0 |"); continue
    print(f"| {short(v)} | {len(r)} | {min(a):.2f} ({r[a.index(min(a))]['start']}) | {max(a):.2f} ({r[a.index(max(a))]['start']}) | {100 * sum(x > 1 for x in a) / len(a):.0f}% | {a[-1]:.2f} ({r[-1]['start']}) |")

print("\n### F. Forecast beside measurement, whole window, all liquidity\n")
print("| Pool | Realised volatility | Mean P_trade | Naive LVR (σ²/4 · L√P) | Fee-adjusted forecast (LVR × P_trade) | Measured loss, +1 h | Measured LP fee − loss |")
print("| --- | --- | --- | --- | --- | --- | --- |")
for v, o in R.items():
    t = o["total"]; fc = t["forecast"]
    print(f"| {short(v)} | {100 * o['sigma_whole_window_5min']:.0f}% | {100 * fc['p_trade_time_weighted']:.2f}% | ${fc['lvr_naive_usd']:,.0f} | ${fc['arb_fee_adjusted_usd']:,.0f} | "
          f"${t['loss_3600_usd']:,.0f} | ${t['net_3600_usd']:,.0f} |")

print("\n### G. Horizon sensitivity: fee ÷ loss when each swap is valued h later\n")
print("| Pool | At the swap | +1 min | +5 min | +1 h | +4 h | +24 h |")
print("| --- | --- | --- | --- | --- | --- | --- |")
for v, o in R.items():
    t = o["total"]
    fee = t["lp_fee_usd_scored"]
    print(f"| {short(v)} | " + " | ".join(f2(fee / t[f"loss_{h}_usd"]) for h in (0, 60, 300, 3600, 14400, 86400)) + " |")

