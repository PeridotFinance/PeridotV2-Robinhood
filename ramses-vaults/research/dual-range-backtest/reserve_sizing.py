"""Stress sizing of the dedicated loss-reserve seed at the CURRENT CAPS (and, labelled COUNTERFACTUAL, at higher ranged caps). Offline; changes nothing.

Definitions (all USD, per the vault's own accounting: benchmark = principals valued at the current oracle price, gross = what the pair holds, shortfall =
benchmark - gross, recognized pro rata across both sides' principals):
  deployed position   matched value V0 deployed at price p0 into a Uniswap-v3-style range [p0*1.0001^-h, p0*1.0001^h] (h ticks) or full range
  shortfall S(p)      hold(p) - LP(p) for a GAP move to price p (no recentre, no fee income: 'assume no future income for day-one backing')
  composition deficit the scarce token's native claim (after the pro-rata write-down f = LP/hold) minus what the position holds of it:
                        price falls  -> USDG deficit  D_u = max(0, y0 f - y(p));  price rises -> stock deficit D_s = max(0, x0 f - x(p)) (valued at p)
                      This is the CASH the reserve must hold in the scarce token for all suppliers of that side to exit concurrently WITHOUT a settlement swap
                      (swaps are paused in the configuration). It is a liquidity need, not an additional economic loss; the stock-side surplus stays in the vault.
  swap path           if the bounded settlement swap were enabled, the surplus token is converted at <= maxSwapSlippageBps (+ pool fee): cost = converted value x
                      (slippage + fee). Reported as an alternative to funding the whole composition deficit.
  recovery gas        a handful of keeper/withdrawal transactions, counted ONCE.
Seed requirement (100% coverage of the MODELED aggregate strategy shortfall): S + swap-path cost + recovery gas, with 25% and 50% contingency on top;
and, separately, the no-swap cash requirement D. The two are never added (they overlap). Historical moves are a baseline, not proof of sufficiency.
Retention: earnings retained at reserveFeeBps (<= 5,000 contract maximum) accumulate from LP fees measured in baseline_*_caps.json; nothing is counted on day one.

  python3 reserve_sizing.py     writes evidence/reserve-sizing.json
"""
import json
import math

from common import HERE

TICK = 1.0001
CAPS = {"pairCapUsd": 1000.0, "rangedCapUsd": 50.0}
SWAP_SLIPPAGE_BPS = 100          # maxSwapSlippageBps read back from the live Uniswap vault; the Ramses value is a configuration choice
POOL_FEE_BPS = {"SPY": 1.5, "NVDA": 1.5}   # 100-150 ppm: pool fee actually in force, order of magnitude only
RECOVERY_TXS = 5
RECOVERY_GAS_USD_EACH = 0.0663   # the assumed per-recenter gas used by the baselines (a proxy, not a Ramses measurement)
CONTINGENCY = (0.0, 0.25, 0.50)


def amounts(L, p, pa, pb):
    sp, sa, sb = math.sqrt(p), math.sqrt(pa), math.sqrt(pb)
    if p <= pa:
        return L * (1 / sa - 1 / sb), 0.0
    if p >= pb:
        return 0.0, L * (sb - sa)
    return L * (1 / sp - 1 / sb), L * (sp - sa)


def position(p0, half_ticks):
    """Return (pa, pb) for a symmetric range, or the full range."""
    if half_ticks is None:
        return 1e-12 * p0, 1e12 * p0
    return p0 * TICK ** -half_ticks, p0 * TICK ** half_ticks


def stress(v0, half_ticks, ratio, pair, p0=1.0):
    """Gap to price p0*ratio. v0 = deployed value in USD. Returns dict in USD."""
    pa, pb = position(p0, half_ticks)
    L = 1.0
    x0, y0 = amounts(L, p0, pa, pb)
    scale = v0 / (x0 * p0 + y0)
    x0, y0 = x0 * scale, y0 * scale
    p = p0 * ratio
    Lp = L * scale
    x, y = amounts(Lp, p, pa, pb)
    hold = x0 * p + y0
    lp = x * p + y
    s = max(0.0, hold - lp)
    f = lp / hold if hold else 1.0
    if ratio < 1:      # stock falls: the position drifts into the stock; USDG is scarce
        deficit, token = max(0.0, y0 * f - y), "USDG"
        swap_cost = deficit * (SWAP_SLIPPAGE_BPS + POOL_FEE_BPS[pair]) / 1e4
    else:
        deficit, token = max(0.0, (x0 * f - x) * p), "stock"
        swap_cost = deficit * (SWAP_SLIPPAGE_BPS + POOL_FEE_BPS[pair]) / 1e4
    gas = RECOVERY_TXS * RECOVERY_GAS_USD_EACH
    return {"ratio": ratio, "shortfallUsd": s, "scarceToken": token, "compositionDeficitUsd": deficit, "swapPathCostUsd": swap_cost, "recoveryGasUsd": gas,
            "lossCoverageSeedUsd": s + swap_cost + gas, "noSwapCashSeedUsd": deficit + gas}


def excursions(series, horizons):
    """Worst adverse (down / up) price ratio reached within each horizon after ANY feed answer, from the feed series."""
    pts = [(x["updatedAt"], x["answer"] / 1e8) for x in series if x["answer"] < 1e12]
    out = {}
    for name, h in horizons.items():
        dn, up = 1.0, 1.0
        for i, (t0, p0) in enumerate(pts):
            lo = hi = 1.0
            for t, p in pts[i + 1:]:
                if t - t0 > h:
                    break
                lo, hi = min(lo, p / p0), max(hi, p / p0)
            dn, up = min(dn, lo), max(up, hi)
        out[name] = {"worstDown": dn, "worstUp": up}
    return out


def main():
    answers = json.loads((HERE / "evidence" / "bundle" / "inputs" / "answer_updated.json").read_text())["series"]
    horizons = {"24h": 86400, "7d": 7 * 86400, "28d": 28 * 86400, "whole window": 10 ** 9}
    ex = {p: excursions(answers[p], horizons) for p in ("SPY", "NVDA")}
    scenarios = {}
    for p in ("SPY", "NVDA"):
        for name, v in ex[p].items():
            scenarios[f"{p} historical worst within {name}"] = {"pair": p, "down": v["worstDown"], "up": v["worstUp"]}
    for g in (0.7, 0.5):
        for p in ("SPY", "NVDA"):
            scenarios[f"{p} synthetic gap {int(round((1 - g) * 100))}%"] = {"pair": p, "down": g, "up": 1 / g}
    configs = {
        "A. current caps: ±600 ticks, $50 ranged (cap-compatible)": (50.0, 600),
        "B. current caps: live policy ±2400 ticks, $50 ranged (cap-compatible)": (50.0, 2400),
        "C. current caps: full range, whole $1,000 pair (cap-compatible; the ranged cap applies to ranged policies)": (1000.0, None),
        "D. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $750 ranged (75%)": (750.0, 2400),
        "E. COUNTERFACTUAL, needs a higher ranged cap: ±2400 ticks, $900 ranged (90%)": (900.0, 2400),
        "F. COUNTERFACTUAL, needs a higher ranged cap: ±600 ticks, $750 ranged (75%)": (750.0, 600),
    }
    rows = []
    for cname, (v0, h) in configs.items():
        for sname, sc in scenarios.items():
            for direction, ratio in (("down", sc["down"]), ("up", sc["up"])):
                r = stress(v0, h, ratio, sc["pair"])
                rows.append({"config": cname, "deployedUsd": v0, "halfWidthTicks": h, "scenario": sname, "direction": direction, **r,
                             "withContingency": {f"{int(c * 100)}%": r["lossCoverageSeedUsd"] * (1 + c) for c in CONTINGENCY}})
    base = {}
    for pair in ("SPY", "NVDA"):
        d = json.load(open(HERE / "evidence" / f"baseline_{pair}_caps.json"))
        for run in ("full_range", "single600_trig300", "live2400_trig600_perday4"):
            x = next(r["result"] for r in d["results"] if r["run"] == run and r["window"] == "full" and r["capital"] == d["capitalUsd"])
            base[f"{pair}/{run}"] = {"days": x["days"], "feesBeforeReserveUsd": x["fees_before_reserve_usd"], "perYearUsd": x["fees_before_reserve_usd"] * 365 / x["days"],
                                     "maintenanceUsd": x["maintenance_gas_usd"], "operatingCostPerYearUsd": x["protocol_operating_cost_usd"] * 365 / x["days"]}
    res = {"schema": "ramses-reserve-sizing-1", "doc": __doc__, "caps": CAPS, "assumptions": {"swapSlippageBps": SWAP_SLIPPAGE_BPS, "poolFeeBps": POOL_FEE_BPS,
           "recoveryTxs": RECOVERY_TXS, "recoveryGasUsdEach": RECOVERY_GAS_USD_EACH, "contingency": CONTINGENCY, "retentionMaxBps": 5000},
           "historicalExcursions": ex, "scenarios": scenarios, "rows": rows, "feeIncomeAtCurrentCaps": base}
    p = HERE / "evidence" / "reserve-sizing.json"
    p.write_text(json.dumps(res, indent=1))
    print("wrote", p, len(rows), "rows")
    for pair in ("SPY", "NVDA"):
        print(pair, {k: (round(v["worstDown"], 3), round(v["worstUp"], 3)) for k, v in ex[pair].items()})


if __name__ == "__main__":
    main()
