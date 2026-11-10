"""Q7. Blackhole WAVAX/USDC against Pharaoh WAVAX/USDC: emissions and arbitrage loss per dollar of liquidity, week by week.

Inputs: out/q1_<venue>.json (per-day fee and loss from q1_fee_vs_lvr.py), the gauges' reward notifications, the reward
tokens' own pools for price, and each pool's swap table for its active liquidity through time.

"Dollar of liquidity" is measured two ways, the same for both pools:
  in range   the dollar value of the pool's active liquidity within +/-1% of the price, time-weighted over the week from
             the swap stream (0.009976 x L x sqrt(P): the tokens a constant-liquidity position holds between -1% and +1%).
             Emissions on both venues are paid pro rata to in-range liquidity, so this is the basis that decides what a
             position receives.
  balances   the pool's token balances at the end block (the only block where they were read).
Emissions are the tokens notified for each weekly epoch, valued at the token's volume-weighted price in that week ("if
sold", no sale cost: a ceiling for all LPs together). Loss is each swap's inventory change valued one hour later (Q1).
Nothing is annualised.
"""
import datetime as dt
import json

import numpy as np

import engine_ext as X
from lib_cl import DATA, OUT, VENUES, dump, load_bars, load_swaps
from rewards import WEEK, emission_rate, load_rewards

PAR = json.load(open("params.json"))["rewards"]
BAND = 0.009976
END_FACTS = {"pharaoh_wavax": {"balances_usd": 3140693.0, "within_1pct_usd_by_quote": 318942.0},
             "blackhole_wavax": {"balances_usd": 473984.0, "within_1pct_usd_by_quote": 26847.0}}   # data/blackhole/FACTS.md section E


def weekly(venue: str) -> dict:
    cfg = PAR[venue]
    q1 = json.loads((OUT / f"q1_{venue}.json").read_text())
    rate = emission_rate(cfg)
    ps = load_swaps(cfg["price_venue"])
    px = ps.p * (load_bars(cfg["quote_usd_bars"]).at(ps.ts) if cfg.get("quote_usd_bars") else 1.0)
    pvol = np.abs(ps.base_in)
    s = load_swaps(venue)
    lsp = s.L * np.sqrt(s.p)                      # in force from each swap until the next
    dts = np.diff(s.ts, append=s.ts[-1])
    per = {}
    for period, ts, amt in rate.notes:
        per[period] = per.get(period, 0.0) + amt
    rows = []
    for period in sorted(per):
        a, b = period * WEEK, (period + 1) * WEEK
        dsel = [v for k, v in q1["by_day"].items() if a <= dt.datetime.fromisoformat(k).replace(tzinfo=dt.timezone.utc).timestamp() < b]
        m = (ps.ts >= a) & (ps.ts < b)
        w = (s.ts >= a) & (s.ts < b)
        if len(dsel) != 7 or not m.any() or not w.any():
            continue
        price = float(np.sum(px[m] * pvol[m]) / np.sum(pvol[m]))
        inr = float(BAND * np.sum(lsp[w] * dts[w]) / np.sum(dts[w]))
        rew = per[period] * price
        loss = sum(v["loss_3600"] for v in dsel)
        rows.append({"week_start": dt.datetime.fromtimestamp(a, tz=dt.timezone.utc).strftime("%Y-%m-%d"), "tokens": per[period], "token_usd": price,
                     "reward_usd": rew, "swap_fee_usd": sum(v["fee_usd_scored"] for v in dsel), "lp_fee_usd": sum(v["lp_fee_usd_scored"] for v in dsel),
                     "loss_3600_usd": loss, "loss_60_usd": sum(v["loss_60"] for v in dsel), "volume_usd": sum(v["volume_usd"] for v in dsel),
                     "in_range_1pct_usd": inr, "reward_per_1000_in_range": 1000 * rew / inr, "loss_per_1000_in_range": 1000 * loss / inr,
                     "net_per_1000_in_range": 1000 * (rew - loss) / inr})
    tot = {k: sum(r[k] for r in rows) for k in ("tokens", "reward_usd", "swap_fee_usd", "lp_fee_usd", "loss_3600_usd", "loss_60_usd", "volume_usd")}
    tot["weeks"] = len(rows)
    tot["in_range_1pct_usd_mean"] = float(np.mean([r["in_range_1pct_usd"] for r in rows]))
    tot["reward_per_1000_in_range_per_week"] = 1000 * tot["reward_usd"] / tot["weeks"] / tot["in_range_1pct_usd_mean"]
    tot["loss_per_1000_in_range_per_week"] = 1000 * tot["loss_3600_usd"] / tot["weeks"] / tot["in_range_1pct_usd_mean"]
    tot["reward_over_loss"] = tot["reward_usd"] / tot["loss_3600_usd"]
    tot["swap_fee_over_loss"] = tot["swap_fee_usd"] / tot["loss_3600_usd"]
    tot["weeks_reward_above_loss"] = sum(1 for r in rows if r["reward_usd"] > r["loss_3600_usd"])
    last = rows[-1]
    end = dict(END_FACTS[venue])
    end.update({"last_full_week": last["week_start"], "last_week_reward_usd": last["reward_usd"],
                "last_week_reward_per_1000_of_balances": 1000 * last["reward_usd"] / end["balances_usd"],
                "last_week_reward_per_1000_within_1pct_by_quote": 1000 * last["reward_usd"] / end["within_1pct_usd_by_quote"],
                "last_week_loss_per_1000_of_balances": 1000 * last["loss_3600_usd"] / end["balances_usd"]})
    return {"label": VENUES[venue].label, "token": cfg["token_symbol"], "weeks": rows, "total": tot, "at_end": end}


def position(venue: str) -> dict:
    """The Q2 live-policy position ($1,000, +/-2,400 / 600) on each pool over the common window, staked, rewards if sold."""
    rate, price, cfg = load_rewards(venue)
    v = VENUES[venue]
    ds = X.load_dataset(venue, ram_rate=rate)
    out = {}
    for half, trig in ((2400, 600), (4800, 1200)):
        c = X.single_range_config(half, trig, 1000.0, v.spacing, block_sessions=(), sample_s=3600.0)
        r = X.run_window(ds, c, None, None, equity_sessions=False, reward_price=price, reward_sale_cost=cfg.get("sale_cost", 0.0))
        out[f"{half}_{trig}"] = {k: r[k] for k in ("days", "excess_mark_pct", "hold_ret_pct", "fees_usd", "lvr_usd", "recenters_core", "deployed_frac_avg",
                                                    "reward_tokens", "reward_usd_if_sold", "reward_pct_if_sold")}
    return out


if __name__ == "__main__":
    res = {}
    for venue in ("pharaoh_wavax", "blackhole_wavax"):
        res[venue] = weekly(venue)
        t = res[venue]["total"]
        print(f"\n{res[venue]['label']}: {res[venue]['token']}, {t['weeks']} full weeks")
        print(f"{'week':<12}{'tokens':>12}{'price $':>9}{'reward $':>10}{'swap fee $':>11}{'loss 1h $':>11}{'in-range $':>12}{'rew/$1k':>9}{'loss/$1k':>9}{'net/$1k':>9}")
        for r in res[venue]["weeks"]:
            print(f"{r['week_start']:<12}{r['tokens']:>12,.0f}{r['token_usd']:>9.5f}{r['reward_usd']:>10,.0f}{r['swap_fee_usd']:>11,.0f}{r['loss_3600_usd']:>11,.0f}"
                  f"{r['in_range_1pct_usd']:>12,.0f}{r['reward_per_1000_in_range']:>9.1f}{r['loss_per_1000_in_range']:>9.1f}{r['net_per_1000_in_range']:>9.1f}")
        print(f"{'TOTAL':<12}{t['tokens']:>12,.0f}{'':>9}{t['reward_usd']:>10,.0f}{t['swap_fee_usd']:>11,.0f}{t['loss_3600_usd']:>11,.0f}{t['in_range_1pct_usd_mean']:>12,.0f}"
              f"{t['reward_per_1000_in_range_per_week']:>9.1f}{t['loss_per_1000_in_range_per_week']:>9.1f}   reward/loss {t['reward_over_loss']:.2f}, "
              f"swap fee/loss {t['swap_fee_over_loss']:.2f}, reward above loss in {t['weeks_reward_above_loss']} of {t['weeks']} weeks")
        print("at END:", {k: (round(x, 1) if isinstance(x, float) else x) for k, x in res[venue]["at_end"].items()})
    for venue in ("pharaoh_wavax", "blackhole_wavax"):
        res[venue]["position_1000"] = position(venue)
        print(venue, {k: {a: (round(float(b), 2) if isinstance(b, (int, float)) else b) for a, b in x.items()} for k, x in res[venue]["position_1000"].items()})
    dump(res, "q7_blackhole_vs_pharaoh.json")
