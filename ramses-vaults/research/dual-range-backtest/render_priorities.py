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


# ---------------------------------------------------------------- section 18
def _b():
    return json.load(open(HERE / "evidence" / "breakeven.json"))


def reserve_units():
    rows = ["| pair | deployed (current caps) | stress | stock valued at | LOSS COVER: USDG units (price falls) | LOSS COVER: stock units (price rises) | both legs funded today | WITHDRAWAL LIQUIDITY without a swap: USDG units / stock units | both legs today |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in _b()["reserveUnits"]:
        rows.append(f"| {r['pair']} | {r['label']}, ${r['deployedUsd']:.0f} | {r['scenario']} (x{r['ratioDown']:.3f} / x{r['ratioUp']:.3f}) | ${r['stockValuationPrice']:.2f} | {r['usdgUnitsLossCover']:.2f} USDG | {r['stockUnitsLossCover']:.5f} {r['pair']} | ${r['bothLegsLossCoverTodayUsd']:.2f} | "
                    f"{r['usdgUnitsWithdrawalNoSwap']:.2f} USDG / {r['stockUnitsWithdrawalNoSwap']:.5f} {r['pair']} | ${r['bothLegsWithdrawalTodayUsd']:.2f} |")
    return "\n".join(rows)


def strategy_costs():
    d = _b()
    cad = list(d["cadenceCostPerYear"])
    rows = ["| pair | configuration | supplier excess vs hold (window) | gross fees / LVR | gross fees per year at caps | retained at 20% / 50% | recentres per year | protocol cost per year: " + " / ".join(c.split(" (")[0] for c in cad) + " |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in d["rows"]:
        rows.append(f"| {r['pair']} | {r['label']} | {r['supplierExcessVsHoldPctWindow']:+.2f}% | {r['feesToLvrRatio']:.2f} | ${r['feesPerYearAtCaps']:.2f} | ${r['retained20PerYear']:.2f} / ${r['retained50PerYear']:.2f} | {r['recentresPerYear']:.0f} | " + " / ".join(f"${r['protocolCostPerYearByCadence'][c]:.2f}" for c in cad) + " |")
    return "\n".join(rows)


def break_even():
    d = _b()
    rows = ["| pair | configuration | current cap | size at half the fee flow (fixed volume) | fee model check: predicted / observed at the $10,000 baseline, per year | depth limit (30% gap's scarce token within 2% depth) | seed per $ deployed (30% gap, both legs) | break-even at 50% retention, 5% seed carry: weekly / daily / hourly |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in d["rows"]:
        be = {}
        for x in d["breakEven"]:
            if x["pair"] == r["pair"] and x["run"] == r["run"] and x["retention"] == 0.5 and x["seedCarry"] == 0.05:
                be[x["cadence"].split(",")[0].split(" (")[0]] = x["breakEvenUsd"]
        fmt = lambda v: "none" if v is None else f"${v:,.0f}"
        cap = "$1,000 pair" if r["run"] == "full_range" else "$50 ranged"
        rows.append(f"| {r['pair']} | {r['label']} | {cap} | ${r['halfSaturationUsd']:,.0f} | ${r['predicted10kFeesPerYear']:.0f} / ${r['observed10kFeesPerYear']:.0f} | ${r['depthLimitedSizeUsd']:,.0f} | {100 * r['seedFracPer1UsdDeployed30pctGap']:.1f}% | {fmt(be.get('weekly'))} / {fmt(be.get('daily'))} / {fmt(be.get('hourly'))} |")
    return "\n".join(rows)
