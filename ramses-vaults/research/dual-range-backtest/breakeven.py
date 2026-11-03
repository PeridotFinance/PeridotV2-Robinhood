"""Reserve table in token units, and a conditional break-even, offline from saved evidence. Nothing here increases a cap or recommends a size above the current caps.

1. Reserve units: the USDG leg (price fall) and the stock leg (price rise), in actual token units at a stated valuation price. Direction is unknown in advance, so BOTH legs must be funded at the
   same time; one gap draws one leg. Loss coverage and withdrawal liquidity are different quantities and overlap, so they are never added. Retained income is replenishment, not seed.
2. Fee income is NOT assumed linear in size. The position's fee share is V*l / (L_pool + V*l), where l is the pool liquidity units per USD of position for that range at the current price and L_pool
   the pool's active liquidity at the saved pin; volume held fixed (no growth, and no volume response to tighter spreads). F_pool is fitted to the at-caps baseline and cross-checked against the
   $10,000 baseline. Costs use measured keeper gas (evidence/live-baseline/costs.json).
3. Break-even is CONDITIONAL: the size at which retained income covers protocol cost plus an assumed carry on the seed. It says nothing about supplier excess: LVR falls on suppliers, shown separately.
"""
import json
import math

from common import HERE
from reserve_sizing import stress, amounts, position, excursions

PRICE = {"SPY": 752.23, "NVDA": 204.52}   # feed answers read 2026-11-03 (evidence/oracle-research/chainlink-feed-reads.json, live-baseline/state-table.json)
RUNS = {"full_range": None, "live2400_trig600_perday4": 2400, "single1200_trig600": 1200, "single600_trig300": 600}
LABEL = {"full_range": "full range", "live2400_trig600_perday4": "±2400", "single1200_trig600": "±1200", "single600_trig300": "±600"}
CAP = {"full_range": 1000.0, "live2400_trig600_perday4": 50.0, "single1200_trig600": 50.0, "single600_trig300": 50.0}
HOURS_OPEN = 24 * 5 * 52


def units_table():
    answers = json.loads((HERE / "evidence" / "bundle" / "inputs" / "answer_updated.json").read_text())["series"]
    out = []
    for pair in ("SPY", "NVDA"):
        ex = excursions(answers[pair], {"28d": 28 * 86400})["28d"]
        for run, h in RUNS.items():
            v0 = CAP[run]
            for name, dn, up in ((f"{pair} worst 28 days", ex["worstDown"], ex["worstUp"]), ("30% gap", 0.7, 1 / 0.7), ("50% gap", 0.5, 2.0)):
                a, b = stress(v0, h, dn, pair), stress(v0, h, up, pair)
                pu = PRICE[pair] * up
                loss_u, loss_s = a["lossCoverageSeedUsd"], b["lossCoverageSeedUsd"]
                out.append({"pair": pair, "run": run, "label": LABEL[run], "deployedUsd": v0, "scenario": name, "ratioDown": dn, "ratioUp": up, "stockPriceNow": PRICE[pair],
                            "stockValuationPrice": pu,
                            "usdgUnitsLossCover": loss_u, "stockUnitsLossCover": loss_s / pu, "stockLossCoverCostTodayUsd": loss_s / pu * PRICE[pair],
                            "bothLegsLossCoverTodayUsd": loss_u + loss_s / pu * PRICE[pair],
                            "usdgUnitsWithdrawalNoSwap": a["compositionDeficitUsd"], "stockUnitsWithdrawalNoSwap": b["compositionDeficitUsd"] / pu,
                            "bothLegsWithdrawalTodayUsd": a["compositionDeficitUsd"] + b["compositionDeficitUsd"] / pu * PRICE[pair],
                            "shortfallDownUsd": a["shortfallUsd"], "shortfallUpUsd": b["shortfallUsd"]})
    return out


def l_per_usd(price, h):
    """Pool liquidity units per USD of position (raw L convention: token raw units), USDG per stock orientation; L is orientation-invariant."""
    p = price * 1e-12
    pa, pb = position(p, h)
    x, y = amounts(1.0, p, pa, pb)     # raw stock, raw USDG for L = 1
    return 1.0 / (x / 1e18 * price + y / 1e6)


def main():
    quotes = json.load(open(HERE / "evidence/pools/pool-quotes.json"))["pools"]
    pool_name = {"SPY": "SPY/USDG CL1", "NVDA": "NVDA/USDG CL10"}
    costs = json.load(open(HERE / "evidence/live-baseline/costs.json"))
    k = costs["byClass"]
    per = {}
    for key, v in k.items():
        if key.startswith("keeper:"):
            per[key.split(":")[1].split("(")[0]] = v["avgUSD_per_tx"]
    chk, reb, rec = per["checkpoint"], per["rebalance"], per["recenter"]
    cadence = {"hourly, 24/5 (maxCheckpointAge)": HOURS_OPEN * chk, "every 4 h, 24/5": HOURS_OPEN / 4 * chk, "daily": 365 * chk, "weekly": 52 * chk}
    rows, be = [], []
    for pair in ("SPY", "NVDA"):
        cap = json.load(open(HERE / f"evidence/baseline_{pair}_caps.json"))
        res = json.load(open(HERE / f"evidence/baseline_{pair}.json"))
        L = float(quotes[pool_name[pair]]["liquidity"])
        dep = quotes[pool_name[pair]]["depth"]
        d2u = next(x["inputUsd"] for x in dep if x["direction"] == "price_up_buy_stock" and x["movePct"] == 2.0)
        d2d = next(x["inputUsd"] for x in dep if x["direction"] == "price_down_sell_stock" and x["movePct"] == 2.0)
        for run, h in RUNS.items():
            xc = next(r["result"] for r in cap["results"] if r["run"] == run and r["window"] == "full" and r["capital"] == cap["capitalUsd"])
            xr = next(r["result"] for r in res["results"] if r["run"] == run and r["window"] == "full" and r["capital"] == res["capitalUsd"])
            yr = 365 / xc["days"]
            v1 = 1000 * xc["deployed_frac_avg"]
            f1 = xc["fees_before_reserve_usd"] * yr
            l = l_per_usd(PRICE[pair], h)
            half = L / l     # position size at which it would own half the fee flow, at fixed volume
            fpool = f1 * (v1 * l + L) / (v1 * l)
            v2 = 10000 * xr["deployed_frac_avg"]
            pred2 = fpool * v2 * l / (v2 * l + L)
            obs2 = xr["fees_before_reserve_usd"] * yr
            recents = xc["recenters_core"] * yr
            s30 = stress(1000.0, h, 0.7, pair)
            s30u = stress(1000.0, h, 1 / 0.7, pair)
            seed_frac = (s30["lossCoverageSeedUsd"] - s30["recoveryGasUsd"] + (s30u["lossCoverageSeedUsd"] - s30u["recoveryGasUsd"]) / (1 / 0.7)) / 1000   # both legs at today's prices, per $ deployed
            liq_frac_d, liq_frac_u = s30["compositionDeficitUsd"] / 1000, s30u["compositionDeficitUsd"] / 1000
            depth_cap = min(d2d / liq_frac_d, d2u / liq_frac_u)
            rows.append({"pair": pair, "run": run, "label": LABEL[run], "days": xc["days"], "feesPerYearAtCaps": f1, "deployedAtCapsUsd": v1, "halfSaturationUsd": half, "fittedPoolFeesPerYear": fpool,
                         "predicted10kFeesPerYear": pred2, "observed10kFeesPerYear": obs2, "supplierExcessVsHoldPctWindow": xc["supplier_excess_vs_hold_pct"], "lvrUsdWindow": xc["lvr_usd"],
                         "feesToLvrRatio": xc["fees_before_reserve_usd"] / xc["lvr_usd"] if xc["lvr_usd"] else None, "recentresPerYear": recents, "recentreCostPerYear": recents * (rec + reb),
                         "retained20PerYear": 0.2 * f1, "retained50PerYear": 0.5 * f1, "seedFracPer1UsdDeployed30pctGap": seed_frac, "depthLimitedSizeUsd": depth_cap,
                         "protocolCostPerYearByCadence": {c: v + recents * (rec + reb) for c, v in cadence.items()}})
            for rho in (0.2, 0.5):
                for cname, cv in cadence.items():
                    for carry in (0.0, 0.05):
                        C = cv + recents * (rec + reb)
                        def net(V):
                            return rho * fpool * V * l / (V * l + L) - carry * seed_frac * V - C
                        grid = [10 ** (i / 40) for i in range(40, 321)]    # $10 .. $100M
                        hit = next((V for V in grid if net(V) >= 0), None)
                        be.append({"pair": pair, "run": run, "label": LABEL[run], "retention": rho, "cadence": cname, "seedCarry": carry, "protocolCostPerYear": C, "breakEvenUsd": hit,
                                   "exceedsCurrentCap": (hit is not None and hit > CAP[run]), "exceedsDepthLimit": (hit is not None and hit > depth_cap)})
    res = {"schema": "ramses-breakeven-1", "doc": __doc__, "measuredKeeperGasUsd": {"checkpoint": chk, "rebalance": reb, "recentre": rec, "ethUsd": costs["ethUsd_at_pin"]}, "cadenceCostPerYear": cadence, "rows": rows, "breakEven": be, "reserveUnits": units_table(), "prices": PRICE}
    (HERE / "evidence" / "breakeven.json").write_text(json.dumps(res, indent=1))
    print("gas", chk, reb, rec, {k: round(v, 1) for k, v in cadence.items()})
    for r in rows:
        print(r["pair"], r["label"], "fees/yr %.2f half-sat $%.0f pred10k %.1f obs10k %.1f fee/LVR %.2f seedfrac %.3f depthcap $%.0f" % (r["feesPerYearAtCaps"], r["halfSaturationUsd"], r["predicted10kFeesPerYear"], r["observed10kFeesPerYear"], r["feesToLvrRatio"], r["seedFracPer1UsdDeployed30pctGap"], r["depthLimitedSizeUsd"]))
    for b in be:
        if b["cadence"].startswith(("daily", "hourly")) and b["seedCarry"] == 0.05:
            print(b["pair"], b["label"], b["retention"], b["cadence"][:6], "C %.1f BE %s" % (b["protocolCostPerYear"], None if b["breakEvenUsd"] is None else round(b["breakEvenUsd"])))


if __name__ == "__main__":
    main()
