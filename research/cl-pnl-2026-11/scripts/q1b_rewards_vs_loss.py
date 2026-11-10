"""Q1 addendum. Gauge emissions beside fee income and arbitrage loss, week by week, for the whole pool.

Emissions are what the gauge was funded with for each weekly period (NotifyReward), valued at the reward token's
volume-weighted price in that week ("if sold": no sale cost is taken here because the figure is for all LPs together and
the token's own pool could not absorb it; it is a ceiling, not an income). Fee and loss are Q1's per-day numbers summed
over the same week. Nothing is annualised or projected.
"""
import datetime as dt
import json

import numpy as np

from lib_cl import OUT, VENUES, dump, load_bars, load_feed, load_swaps
from rewards import WEEK, emission_rate

PAR = json.load(open("params.json"))["rewards"]
out = {}
for venue in ("pharaoh_wavax", "ramses_nvda", "ramses_spy"):
    cfg = PAR[venue]
    q1 = json.loads((OUT / f"q1_{venue}.json").read_text())
    rate = emission_rate(cfg)
    ps = load_swaps(cfg["price_venue"])
    px = ps.p * (load_bars(cfg["quote_usd_bars"]).at(ps.ts) if cfg.get("quote_usd_bars") else load_feed(cfg["quote_usd_feed"], cfg.get("quote_feed_decimals", 8)).at(ps.ts))
    vol = np.abs(ps.base_in)
    days = q1["by_day"]
    per = {}
    for period, ts, amt in rate.notes:
        per[period] = per.get(period, 0.0) + amt
    rows = []
    for period in sorted(per):
        a, b = period * WEEK, (period + 1) * WEEK
        m = (ps.ts >= a) & (ps.ts < b)
        price = float(np.sum(px[m] * vol[m]) / np.sum(vol[m])) if m.any() and vol[m].sum() > 0 else float("nan")
        dsel = [v for k, v in days.items() if a <= dt.datetime.fromisoformat(k).replace(tzinfo=dt.timezone.utc).timestamp() < b]
        if not dsel:
            continue
        rows.append({"period": period, "week_start": dt.datetime.fromtimestamp(a, tz=dt.timezone.utc).strftime("%Y-%m-%d"), "tokens": per[period],
                     "token_usd": price, "reward_usd": per[period] * price, "lp_fee_usd": sum(v["lp_fee_usd_scored"] for v in dsel),
                     "swap_fee_usd": sum(v["fee_usd_scored"] for v in dsel), "loss_60_usd": sum(v["loss_60"] for v in dsel),
                     "loss_3600_usd": sum(v["loss_3600"] for v in dsel), "days_in_data": len(dsel)})
    full = [r for r in rows if r["days_in_data"] == 7]
    tot = {k: sum(r[k] for r in full) for k in ("tokens", "reward_usd", "lp_fee_usd", "swap_fee_usd", "loss_60_usd", "loss_3600_usd")}
    tot["weeks"] = len(full)
    tot["reward_plus_fee_over_loss_3600"] = (tot["reward_usd"] + tot["lp_fee_usd"]) / tot["loss_3600_usd"]
    tot["weeks_reward_plus_fee_above_loss"] = sum(1 for r in full if r["reward_usd"] + r["lp_fee_usd"] > r["loss_3600_usd"])
    out[venue] = {"label": VENUES[venue].label, "token": cfg["token_symbol"], "weeks": rows, "total_full_weeks": tot}
    print(f"\n{VENUES[venue].label}: {cfg['token_symbol']} emissions to the gauge vs the pool's fee income and loss (full weeks only in the total)")
    print(f"{'week':<12}{'tokens':>12}{'price $':>9}{'reward $':>11}{'LP fee $':>10}{'loss 1h $':>11}{'(rew+fee)/loss':>15}")
    for r in rows:
        print(f"{r['week_start']:<12}{r['tokens']:>12,.0f}{r['token_usd']:>9.4f}{r['reward_usd']:>11,.0f}{r['lp_fee_usd']:>10,.0f}{r['loss_3600_usd']:>11,.0f}"
              f"{(r['reward_usd'] + r['lp_fee_usd']) / r['loss_3600_usd']:>15.2f}{'' if r['days_in_data'] == 7 else '   (part week)'}")
    print(f"{'TOTAL':<12}{tot['tokens']:>12,.0f}{'':>9}{tot['reward_usd']:>11,.0f}{tot['lp_fee_usd']:>10,.0f}{tot['loss_3600_usd']:>11,.0f}{tot['reward_plus_fee_over_loss_3600']:>15.2f}"
          f"   above loss in {tot['weeks_reward_plus_fee_above_loss']} of {tot['weeks']} weeks")
dump(out, "q1b_rewards_vs_loss.json")
