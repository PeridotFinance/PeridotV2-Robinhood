"""README tables for section 17 (strategy comparison, reserve sizing, retention). Generated from evidence/*.json, never typed."""
import json

from common import HERE
import strategy_comparison as SC

D = json.load(open(HERE / "evidence" / "reserve-sizing.json"))


def strategy_caps():
    return SC.table("caps")


def strategy_counterfactual():
    return SC.table("research")


def _pick(config_prefix, scenario):
    return [r for r in D["rows"] if r["config"].startswith(config_prefix) and r["scenario"] == scenario]


def sizing():
    rows = ["| configuration (deployed value) | modeled stress | price falls: USDG seed, loss cover 100% / +25% / +50% | price rises: stock seed (USD), 100% / +25% / +50% | no-swap cash instead (USDG / stock) | shortfall S down / up |", "| --- | --- | --- | --- | --- | --- |"]
    for pref in ("A.", "B.", "C.", "D.", "E.", "F."):
        for sc in ("NVDA historical worst within 28d", "SPY historical worst within 28d", "NVDA synthetic gap 30%", "NVDA synthetic gap 50%"):
            pr = _pick(pref, sc)
            if not pr:
                continue
            dn = next(r for r in pr if r["direction"] == "down")
            up = next(r for r in pr if r["direction"] == "up")
            c = lambda r: f"${r['withContingency']['0%']:.2f} / ${r['withContingency']['25%']:.2f} / ${r['withContingency']['50%']:.2f}"
            rows.append(f"| {dn['config'].split(':')[0]}: {dn['config'].split(': ', 1)[1]} | {sc} (x{dn['ratio']:.3f} / x{up['ratio']:.3f}) | {c(dn)} | {c(up)} | "
                        f"${dn['noSwapCashSeedUsd']:.2f} / ${up['noSwapCashSeedUsd']:.2f} | ${dn['shortfallUsd']:.2f} / ${up['shortfallUsd']:.2f} |")
    return "\n".join(rows)


def retention():
    inc = D["feeIncomeAtCurrentCaps"]
    rows = ["| strategy at current caps | LP fees before the reserve cut, per year | retained at 50% (contract maximum 5,000 bp), per year | at the live 20% | protocol-paid operating cost per year (assumed / observed-average basis) | years of 50% retention to build the NVDA 28-day-worst seed (full range, 100%) |", "| --- | --- | --- | --- | --- | --- |"]
    seed = next(r for r in D["rows"] if r["config"].startswith("C.") and r["scenario"] == "NVDA historical worst within 28d" and r["direction"] == "up")["lossCoverageSeedUsd"]
    for k, v in inc.items():
        yrs = seed / (0.5 * v["perYearUsd"]) if v["perYearUsd"] else float("inf")
        rows.append(f"| {k} | ${v['perYearUsd']:.2f} | ${0.5 * v['perYearUsd']:.2f} | ${0.2 * v['perYearUsd']:.2f} | ${v['operatingCostPerYearUsd']:.0f} / ${v['operatingCostPerYearUsd'] * 0.1428 / 0.5737:.0f} | {yrs:.1f} |")
    return "\n".join(rows)


if __name__ == "__main__":
    for fn in (strategy_caps, strategy_counterfactual, sizing, retention):
        print(fn.__name__.upper()); print(fn()); print()
