"""Q5. Reward timing: RAM / PHAR lost to the gauge's timing rules under three keeper policies.

There is no realised figure to report: no Peridot position has earned RAM on mainnet (data/facts/MECHANICS.md section 3)
and the Pharaoh vaults' PHAR sits unharvested. So this is the gauge's rule, as fork-measured, applied to the position
the engine simulates over the window, with the one input nobody can read from the chain (how often users make the
vault act) swept explicitly.

Gauge rule (per position NFT; threshold T = voter.timeThresholdForGauge: 99 s Ramses, 111 s Pharaoh):
  * every liquidity change claims what has accrued since the previous change;
  * a claim made T seconds or less after the position's last change pays nothing, and what had accrued since that
    change is gone (Ramses pays it to its treasury on the next claim; Pharaoh sends it to r33);
  * a claim made later pays in full.
So the only reward that can be lost is what accrues during the first T seconds after a liquidity change, and only if
something claims inside those T seconds. The simulator applies exactly that to a timeline of events:

  keeper liquidity changes   the engine's recentres (real swap path) and, optionally, an hourly rebalance
  user-driven changes        withdrawals that unwind liquidity: Poisson, UNWINDS per day (not spaced by any keeper rule)
  settlements                claims with no liquidity change: Poisson, ADMITS per day (policy "settle before admit" only)

Policies:
  current           keeper never acts within 120 s of the last change (ramses_keeper.py ANTI_SYBIL_WAIT); harvest every 6 h
  claim_at_recentre no separate harvest: rewards are claimed only by the recentre's own liquidity change
  settle_before_admit   current, plus a claim before every deposit / withdrawal is admitted
  unspaced          for scale: the keeper rule switched off and an hourly rebalance landing 5 s after each recentre

Usage: q5_reward_timing.py VENUE [--half 2400 --trigger 600]
"""
from __future__ import annotations

import argparse

import numpy as np

import engine_ext as X
from lib_cl import VENUES, dump, iso
from rewards import load_rewards, usd_if_sold

RNG = np.random.default_rng(20261110)


def lost_fraction(changes: np.ndarray, claims: np.ndarray, t0: float, t1: float, thr: float) -> float:
    """Share of [t0, t1] whose accrual is lost, for sorted liquidity-change times and claim-only times (uniform accrual
    within the window; the position is assumed in range throughout, which is the case that maximises the loss)."""
    ev = np.concatenate([np.column_stack([changes, np.ones(len(changes))]), np.column_stack([claims, np.zeros(len(claims))])])
    ev = ev[np.argsort(ev[:, 0], kind="stable")]
    lost = 0.0
    last_change = t0
    paid_upto = t0                       # accrual before this time has been settled (paid or lost)
    for t, is_change in ev:
        if t - last_change <= thr:       # inside the withholding window: everything since the last settle point is gone
            lost += t - paid_upto
        paid_upto = t
        if is_change:
            last_change = t
    return lost / (t1 - t0)


def space(times: np.ndarray, others: np.ndarray, wait: float) -> np.ndarray:
    """Delay each keeper action until ``wait`` seconds after the latest earlier liquidity change (its own or a user's)."""
    out = []
    allc = np.sort(others)
    last_own = -1e18
    for t in np.sort(times):
        while True:
            j = np.searchsorted(allc, t, side="right") - 1
            prev = max(last_own, allc[j] if j >= 0 else -1e18)
            if t - prev >= wait:
                break
            t = prev + wait
        out.append(t)
        last_own = t
    return np.array(out)


def poisson_times(rate_per_day: float, t0: float, t1: float) -> np.ndarray:
    n = RNG.poisson(rate_per_day * (t1 - t0) / 86400.0)
    return np.sort(RNG.uniform(t0, t1, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venue", choices=list(VENUES))
    ap.add_argument("--half", type=int, default=2400)
    ap.add_argument("--trigger", type=int, default=600)
    ap.add_argument("--from-ts", type=float)
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    v = VENUES[a.venue]
    rate, price, cfg = load_rewards(a.venue)
    thr = float(cfg["forfeit_window_s"])
    ds = X.load_dataset(a.venue, ram_rate=rate)
    # start where the gauge actually pays
    first_paid = min(s[0] for s in rate.spans)
    t_from = max(a.from_ts or 0.0, first_paid, ds.ts[0], ds.oracle_t[0])
    out = {"venue": a.venue, "label": v.label, "token": cfg["token_symbol"], "threshold_s": thr, "policy_range": [a.half, a.trigger], "sizes": {}, "loss_share": {}}
    recs = None
    for size in (1000.0, 10000.0):
        conf = X.single_range_config(a.half, a.trigger, size, v.spacing, block_sessions=("weekend",) if v.equity else ())
        r = X.run_window(ds, conf, t_from, None, equity_sessions=v.equity, keep_series=True, reward_price=price, reward_sale_cost=cfg.get("sale_cost", 0.0))
        t0, t1 = r["start_ts"], r["end_ts"]
        recs = np.array([x[0] for x in r["recenter_log"]])
        out["sizes"][str(int(size))] = {"days": r["days"], "reward_tokens": r["reward_tokens"], "reward_usd_if_sold": r["reward_usd_if_sold"],
                                        "reward_pct_of_start_if_sold": r["reward_pct_if_sold"], "recentres": int(len(recs)), "in_range": r["in_range_core"],
                                        "lp_fees_usd": r["fees_usd"], "lvr_usd": r["lvr_usd"]}
    out["window"] = [iso(t0), iso(t1)]
    hourly = np.arange(t0 + 1800, t1, 3600.0)
    print(f"{v.label}: {cfg['token_symbol']} timing loss, threshold {thr:.0f} s, range +/-{a.half}/{a.trigger}, {iso(t0)} .. {iso(t1)}, {len(recs)} recentres")
    for k, s in out["sizes"].items():
        print(f"  ${int(k):>6,}: {s['reward_tokens']:.2f} {cfg['token_symbol']} accrued = ${s['reward_usd_if_sold']:.2f} if sold ({s['reward_pct_of_start_if_sold']:.2f}% of start); "
              f"LP fees ${s['lp_fees_usd']:.2f}, LVR ${s['lvr_usd']:.2f}")
    print(f"{'policy':<22}{'hourly rebal':>13}{'unwinds/d':>10}{'admits/d':>10}{'lost % (mean)':>15}{'max of trials':>15}")
    scen = []
    for hourly_on in (False, True):
        for unw in (0.0, 1.0, 10.0, 100.0):
            for adm in (0.0, 10.0, 100.0, 1000.0, 10000.0):
                scen.append((hourly_on, unw, adm))
    for policy in ("current", "claim_at_recentre", "settle_before_admit", "unspaced"):
        for hourly_on, unw, adm in scen:
            if policy != "settle_before_admit" and adm > 0:
                continue
            if policy == "settle_before_admit" and adm == 0:
                continue
            if policy == "unspaced" and (unw > 0 or not hourly_on):
                continue
            vals = []
            for _ in range(a.trials):
                users = poisson_times(unw, t0, t1)
                keeper = np.concatenate([recs, hourly]) if hourly_on else recs.copy()
                if policy == "unspaced":
                    keeper = np.concatenate([recs, recs + 5.0, hourly])
                    changes = np.sort(keeper)
                    claims = np.array([])
                else:
                    keeper = space(keeper, users, 120.0)
                    changes = np.sort(np.concatenate([keeper, users]))
                    if policy == "claim_at_recentre":
                        claims = np.array([])
                    else:
                        claims = space(np.arange(t0 + 6 * 3600, t1, 6 * 3600.0), changes, 120.0)
                    if policy == "settle_before_admit":
                        claims = np.sort(np.concatenate([claims, poisson_times(adm, t0, t1)]))
                vals.append(100 * lost_fraction(changes, claims, t0, t1, thr))
                if unw == 0 and adm == 0:
                    break  # deterministic
            row = {"policy": policy, "hourly_rebalance": hourly_on, "unwinds_per_day": unw, "admits_per_day": adm, "lost_pct_mean": float(np.mean(vals)),
                   "lost_pct_max": float(np.max(vals))}
            out["loss_share"].setdefault(policy, []).append(row)
            print(f"{policy:<22}{str(hourly_on):>13}{unw:>10.0f}{adm:>10.0f}{row['lost_pct_mean']:>15.4f}{row['lost_pct_max']:>15.4f}")
    p = dump(out, f"q5_{a.venue}{a.tag}.json")
    print("wrote", p)


if __name__ == "__main__":
    main()
