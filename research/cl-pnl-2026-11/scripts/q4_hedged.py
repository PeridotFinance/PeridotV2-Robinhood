"""Q4. Hedged mode at scale: the range position with its stock delta shorted in the margin market.

One portfolio ledger in USD, no outside flows after the start:

  LP leg     the engine's range position on real swaps (Q2 rules; fees net of protocol share and the 20% reserve cut)
  hedge leg  borrow the stock and sell it; borrowed amount = the pair's ACTUAL stock holding at that moment (ranged
             position + idle + accrued fees, i.e. dNAV/dP of the share), re-set whenever the drift exceeds THRESHOLD x LP
             value at a 300 s sample, opened at the start and closed at the end
  cash       margin posted (MARGIN x opening short notional) plus the short's proceeds; earns the quote supply rate
  costs      every adjustment pays the hedge venue's swap fee in force and price impact against that venue's active
             liquidity at that time (N / (L sqrt(P)) of notional N, i.e. half the price move), plus gas; borrowed stock
             grows at the borrow rate; none of the LP's own costs are counted twice
  rewards    reported on a separate line, in tokens and in USD "if sold" (price series x (1 - sale cost)); never inside
             the hedged return unless the row says "+ rewards"

Benchmarks on the same starting equity: hold the deposited tokens + the same cash; the unhedged LP + the same cash.

Usage: q4_hedged.py LP_VENUE [--hedge-venue V] [--half 2400 --trigger 600] ...   (parameters in params.json)
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path

import numpy as np

import engine_ext as X
from lib_cl import VENUES, YEAR, dump, iso, load_swaps
from q2_geometry import SPACING, STEP_S, WINDOW_S

PARAMS = json.loads((Path(__file__).parent / "params.json").read_text())


def hedge_ledger(run: dict, ds, hv, thr: float, par: dict, self_share: float = 0.0) -> dict:
    """Walk the engine's 300 s samples and run the short. ``hv`` = Swaps of the venue the hedge trades on."""
    series, q = run["series"], run["q_series"]
    m = ds.market
    t = np.array([x[0] for x in series])
    lp = np.array([x[1] for x in series])            # LP equity at the pool price (fees in, recentre gas out)
    hold = np.array([x[2] for x in series])
    # LP-venue pool price in force at each sample (state before the first swap at or after the sample, as the engine samples)
    idx = np.searchsorted(np.asarray(ds.ts), t, side="left") - 1
    idx = np.clip(idx, 0, None)
    p = np.array([m.usd_per_stock(ds.s_after[i]) for i in idx])
    j = np.clip(np.searchsorted(hv.ts, t, side="right") - 1, 0, None)
    ph, Lh, fh = hv.p[j], hv.L[j], hv.fee_ppm[j] / 1e6
    # the margin stack refuses to trade when the feed is older than 12 h or the pool is more than 1% from it [MECHANICS 6A]
    ot, op = np.asarray(ds.oracle_t), np.asarray(ds.oracle_p)
    oi = np.clip(np.searchsorted(ot, t, side="right") - 1, 0, None)
    can_trade = ((t - ot[oi]) <= par.get("max_oracle_age_s", 43200)) & (np.abs(ph / op[oi] - 1) <= par.get("oracle_gate", 0.01))
    led_blocked = 0
    r_b, r_s = par["borrow_apr"], par["quote_supply_apr"]
    M = par["margin_fraction"] * q[0] * p[0]
    cash, debt = M, 0.0
    led = {"swap_fee": 0.0, "impact": 0.0, "gas": 0.0, "borrow_interest": 0.0, "supply_interest": 0.0, "adjustments": 0, "traded_usd": 0.0}
    worst_ratio = float("inf")
    eq = []

    def adjust(target, k):
        nonlocal cash, debt
        dq = target - debt
        n = abs(dq) * ph[k]
        if n <= 0:
            return
        n_eff = n / (1.0 - self_share)                # trading in the pool our own liquidity sits in hands part of it back
        fee = n_eff * (fh[k] + par.get("flash_fee", 0.0))
        imp = n_eff * min(n_eff / (Lh[k] * math.sqrt(ph[k])), 0.5)
        cash += dq * ph[k] - fee - imp - par["gas_usd_per_adjustment"]
        led["swap_fee"] += fee; led["impact"] += imp; led["gas"] += par["gas_usd_per_adjustment"]
        led["adjustments"] += 1; led["traded_usd"] += n_eff
        debt = target

    adjust(q[0], 0)
    eq.append(lp[0] + cash - debt * p[0])
    for k in range(1, len(t)):
        dt_ = t[k] - t[k - 1]
        grow = debt * r_b * dt_ / YEAR
        led["borrow_interest"] += grow * p[k]
        debt += grow
        earn = cash * r_s * dt_ / YEAR
        led["supply_interest"] += earn
        cash += earn
        last = k == len(t) - 1
        if last:
            adjust(0.0, k)
        elif abs(q[k] - debt) * p[k] > thr * lp[k]:
            if can_trade[k]:
                adjust(q[k], k)
            else:
                led_blocked += 1
        e = lp[k] + cash - debt * p[k]
        eq.append(e)
        if debt > 0:
            worst_ratio = min(worst_ratio, (cash - debt * p[k]) / (debt * p[k]))
    start = lp[0] + M
    # end marks: the LP at the engine's end mark (oracle when fresh), the hedge is closed
    lp_end = run["equity_end_mark"]
    hedged_end = lp_end + cash
    hold_end = run["hold_end_mark"] + M
    unhedged_end = lp_end + M
    costs = led["swap_fee"] + led["impact"] + led["gas"] + led["borrow_interest"] - led["supply_interest"]
    hedge_pnl = cash - M
    eqa = np.array(eq)
    return {"threshold": thr, "start_equity": start, "margin_posted": M, "days": (t[-1] - t[0]) / 86400,
            "end": {"hedged": hedged_end, "unhedged_lp_plus_cash": unhedged_end, "hold_plus_cash": hold_end},
            "return_pct": {"hedged": 100 * (hedged_end / start - 1), "unhedged": 100 * (unhedged_end / start - 1), "hold": 100 * (hold_end / start - 1)},
            "hedged_vs_hold_pct": 100 * (hedged_end - hold_end) / start, "hedged_vs_cash_pct": 100 * (hedged_end / start - 1),
            "hedge_leg": {"total": hedge_pnl, "price_and_rebalance": hedge_pnl + costs, **{k: v for k, v in led.items()}},
            "costs_pct_of_start": 100 * costs / start,
            "hedged_before_hedge_costs_pct": 100 * (hedged_end + costs - start) / start,
            "lp": {"fees_usd": run["fees_usd"], "lvr_usd": run["lvr_usd"], "fee_over_lvr": run["fee_over_lvr"], "recenters": run["recenters_core"],
                   "deployed_frac_avg": run["deployed_frac_avg"], "gas_usd": run["gas_usd"]},
            "samples_wanting_adjust_but_blocked": led_blocked, "worst_margin_ratio": worst_ratio, "max_short_usd": float(np.max(np.array(q) * p)),
            "worst_drawdown_pct": float(100 * np.min(eqa / np.maximum.accumulate(eqa) - 1)),
            "reward_tokens": run.get("ram_tokens", 0.0)}


def reward_value(run: dict, price_series, sale_cost: float, forfeit: float = 0.0) -> dict:
    """Reward tokens accrued (engine: emission x our share of in-range liquidity), sold at each week's end at the price
    then in force. ``forfeit`` = share lost to the gauge's timing rules (Q5)."""
    if price_series is None or not run.get("ram_series"):
        return {"tokens": run.get("ram_tokens", 0.0), "usd_if_sold": None}
    t = np.array([x[0] for x in run["series"]])
    ram = np.array(run["ram_series"])
    week = (t // 604800).astype(int)
    usd = 0.0
    for w in np.unique(week):
        k = np.flatnonzero(week == w)
        got = ram[k[-1]] - (ram[k[0] - 1] if k[0] > 0 else 0.0)
        px = float(price_series.at(np.array([t[k[-1]]]))[0])
        if px == px:
            usd += got * px
    return {"tokens": float(ram[-1]), "usd_if_sold": usd * (1 - sale_cost) * (1 - forfeit), "sale_cost": sale_cost, "forfeit_share": forfeit}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--hedge-venue")
    ap.add_argument("--half", type=int, default=2400)
    ap.add_argument("--trigger", type=int, default=600)
    ap.add_argument("--max-per-day", type=int, default=4)
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--to-ts", type=float)
    ap.add_argument("--start-stock-frac", type=float, default=0.5)
    ap.add_argument("--no-windows", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    v = VENUES[a.venue]
    par = PARAMS["hedge"][a.venue]
    hv_key = a.hedge_venue or par.get("hedge_venue", a.venue)
    from rewards import load_rewards
    ram_rate, price, rewards = load_rewards(a.venue)
    ds = X.load_dataset(a.venue, ram_rate=ram_rate)
    hv = load_swaps(hv_key)
    t0 = max(ds.ts[0], a.from_ts or 0.0, ds.oracle_t[0], hv.ts[0])
    t1 = min(ds.ts[-1], a.to_ts or 1e18, hv.ts[-1])
    windows = [(t0, t1, "full")]
    if not a.no_windows:
        s = t0
        while s + WINDOW_S <= t1 + 1:
            windows.append((s, s + WINDOW_S, "30d@" + iso(s)[:10]))
            s += STEP_S
    out = {"venue": a.venue, "label": v.label, "hedge_venue": hv_key, "policy": [a.half, a.trigger, a.max_per_day], "params": par,
           "data_window": [iso(t0), iso(t1)], "rows": []}
    print(f"{v.label}; hedge on {VENUES[hv_key].label}; +/-{a.half}/{a.trigger}; {iso(t0)} .. {iso(t1)}")
    print(f"{'window':<16}{'size':>8}{'thr':>5}{'hedged%':>9}{'unhedg%':>9}{'hold%':>8}{'vsHold':>8}{'preCost%':>9}{'cost%':>7}{'fee$':>8}{'imp$':>8}{'int$':>8}{'gas$':>7}{'adj':>5}{'rew$':>8}{'+rew%':>8}{'minMR':>7}")
    for (w0, w1, lab) in windows:
        for size in PARAMS["sizes_usd"]:
            cfg = X.single_range_config(a.half, a.trigger, size, SPACING[a.venue], max_per_day=a.max_per_day,
                                        block_sessions=("weekend",) if v.equity else ())
            try:
                run = X.run_window(ds, cfg, w0, w1, equity_sessions=v.equity, start_stock_frac=a.start_stock_frac, keep_series=True,
                                   reward_price=price, reward_sale_cost=(rewards or {}).get("sale_cost", 0.0))
            except Exception as e:
                out["rows"].append({"window": lab, "size": size, "error": repr(e)}); continue
            rw = {"tokens": run.get("reward_tokens", 0.0), "usd_if_sold": run.get("reward_usd_if_sold")}
            for thr in PARAMS["thresholds"]:
                share = 0.0   # our own liquidity's share of the hedge venue is negligible at these sizes (stated in the report)
                h = hedge_ledger(run, ds, hv, thr, par, share)
                h.update({"window": lab, "size": size, "rewards": rw})
                h["hedged_plus_rewards_pct"] = None if rw["usd_if_sold"] is None else h["return_pct"]["hedged"] + 100 * rw["usd_if_sold"] / h["start_equity"]
                out["rows"].append(h)
                hl = h["hedge_leg"]
                print(f"{lab:<16}{size:>8,.0f}{thr * 100:>4.0f}%{h['return_pct']['hedged']:>9.2f}{h['return_pct']['unhedged']:>9.2f}{h['return_pct']['hold']:>8.2f}"
                      f"{h['hedged_vs_hold_pct']:>8.2f}{h['hedged_before_hedge_costs_pct']:>9.2f}{h['costs_pct_of_start']:>7.2f}{hl['swap_fee']:>8.2f}{hl['impact']:>8.2f}"
                      f"{hl['borrow_interest'] - hl['supply_interest']:>8.2f}{hl['gas']:>7.2f}{hl['adjustments']:>5}"
                      f"{(rw['usd_if_sold'] if rw['usd_if_sold'] is not None else float('nan')):>8.2f}"
                      f"{(h['hedged_plus_rewards_pct'] if h['hedged_plus_rewards_pct'] is not None else float('nan')):>8.2f}{h['worst_margin_ratio']:>7.2f}")
    p = dump(out, f"q4_{a.venue}_h{a.half}_t{a.trigger}{a.tag}.json")
    print("wrote", p)


if __name__ == "__main__":
    main()
