"""Markdown tables for the pool-selection and fee-reconciliation evidence. Read-only formatting of evidence/pools and evidence/fees.

  python3 render_pools.py
"""
import json

from common import HERE

E = HERE / "evidence"


def fmt(x, nd=1):
    return "n/a (window)" if x is None else f"{x:.{nd}f}"


def main():
    q = json.loads((E / "pools" / "pool-quotes.json").read_text())
    gw = {p["id"].lower(): p for p in json.loads((E / "pools" / "gateway-pools.json").read_text())["pools"]}
    fr = json.loads((E / "fees" / "fee-reconciliation.json").read_text())
    print("**Pinned pool facts** (each pool read at its own block; gateway values are unpinned)\n")
    print("| pool | address | pin (L2 block) | spacing | fee() at pin | listed fee | 30-day mean fee | active liquidity | 24 h volume (logs / gateway) | gateway TVL |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, p in q["pools"].items():
        g = gw[p["pool"].lower()]
        t = fr["pools"].get(name, {}).get("timeWeightedFee", {"30d_meanPpm": float("nan")})
        print(f"| {name} | `{p['pool']}` | {p['pinL2Block']:,} | {p['tickSpacing']} | {p['fee_ppm']} ppm | {g['feeTier']} ppm | {('n/a' if t['30d_meanPpm'] != t['30d_meanPpm'] else format(t['30d_meanPpm'], '.0f') + ' ppm')} | {p['liquidity']:.3e} | "
              f"${p['volume24h']['usdgVolume']:,.0f} / ${g['last24hVolume'] or 0:,.0f} | ${g['tvlUsd']:,.0f} |")
    sizes = q["sizesUsd"]
    ab = {"filled": "", "price_limit_rejection": "limit", "capacity_exhausted": "no capacity", "scan_window_exhausted": "beyond scan"}
    def cell(x):
        return f"{x['costBps']:.1f}" if x["costBps"] is not None else ab.get(x["status"], x["status"])
    print("\n**One-way execution cost in bp of trade size** (fee + price impact versus the pre-trade mid; independent quotes at the pin; buy / sell)\n")
    print("| pool | " + " | ".join(f"${s:,}" for s in sizes) + " |")
    print("| --- | " + " | ".join("---" for _ in sizes) + " |")
    for name, p in q["pools"].items():
        print(f"| {name} | " + " | ".join(f"{cell(r_['buy'])} / {cell(r_['sell'])}" for r_ in p["quotes"]) + " |")
    print("\n**Sequential round trip, bp** (buy, then sell what was bought, in the same evolving pool state: this is the cost of *reversing at once* and returns the price impact, so it is NOT the cost of exiting a position; it replaces the old `roundTripBps`, which added two independent quotes)\n")
    print("| pool | " + " | ".join(f"${s:,}" for s in sizes) + " |")
    print("| --- | " + " | ".join("---" for _ in sizes) + " |")
    for name, p in q["pools"].items():
        print(f"| {name} | " + " | ".join(cell(r_["sequentialRoundTrip"]) for r_ in p["quotes"]) + " |")
    print("\n**Executable capacity** (the pool keeps filling at collapsing prices, so a trade is rarely refused; this is the most it can PAY for an unbounded input)\n")
    print("| pool | scan | most USDG obtainable by selling the stock | most stock (USD at mid) obtainable by spending USDG | statuses |")
    print("| --- | --- | --- | --- | --- |")
    for name, p in q["pools"].items():
        c = p["executableCapacity"]
        print(f"| {name} | {p['scan']['mode']} | ${c['maxUsdgFromSelling']:,.0f} | ${c['maxStockUsdFromBuyingAtMid']:,.0f} | sell {c['sellStatus']}, buy {c['buyStatus']} |")
    print("\n**Depth: USDG-equivalent input that moves the price** (+1% / +5% buying the stock, -1% / -5% selling it)\n")
    print("| pool | +1% | +5% | -1% | -5% |")
    print("| --- | --- | --- | --- | --- |")
    for name, p in q["pools"].items():
        d = {(x["direction"], x["movePct"]): x for x in p["depth"]}
        f = lambda k: ab.get(d[k]["status"], d[k]["status"]) if d[k]["inputUsd"] is None else f"${d[k]['inputUsd']:,.0f}"
        print(f"| {name} | {f(('price_up_buy_stock', 1.0))} | {f(('price_up_buy_stock', 5.0))} | {f(('price_down_sell_stock', 1.0))} | {f(('price_down_sell_stock', 5.0))} |")
    print("\n**Fee reconciliation**\n")
    print("| pool | listed | FeeAdjustment events | fee range seen (ppm) | fee() vs events at matched blocks | implied fee from swaps vs event fee | protocol delta / predicted | feeGrowth delta / predicted | share of last 30 days at 150 ppm |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, p in fr["pools"].items():
        m = p["matchedBlocks"]
        sa = p.get("swapAccounting")
        sw = "n/a" if not sa else "median ratio " + ", ".join(f"{r['medianRatio']:.4f}" for r in sa["byEventFee"][:3]) + f" ({sa['singleBandSwapsUsed']:,} swaps)"
        w = p["windowSplit"]["perToken"]
        pr = [t["protocolRatioActualOverPredicted"] for t in w if t["protocolRatioActualOverPredicted"] is not None]
        fg = [t["feeGrowthActualOverPredictedAll"] for t in w if t["feeGrowthActualOverPredictedAll"] is not None]
        t30 = p["timeWeightedFee"]["30d"].get("150", 0.0)
        print(f"| {name} | {p['listedFeePpm']} | {p['feeAdjustmentEvents']} | {p['feeMinPpm']}-{p['feeMaxPpm']} | {m['samples'] - m['mismatches']}/{m['samples']} match | {sw} | "
              f"{', '.join(f'{x:.4f}' for x in pr) or 'no swaps'} | {', '.join(f'{x:.4f}' for x in fg) or 'no swaps'} | {100 * t30:.1f}% |")
    print("\n**Fee-tier states for SPY over the last 30 days (share of time)**: " + ", ".join(f"{k} ppm {100 * v:.1f}%" for k, v in fr["pools"]["SPY/USDG CL1"]["timeWeightedFee"]["30d"].items()) +
          f"; last at 150 ppm {fr['pools']['SPY/USDG CL1']['timeWeightedFee']['lastTimeInForce']['150']}.\n")
    print("**Gross swap fee -> protocol share -> holder entitlement** (fork tests at pin 104,116,096; raw token units; reserve cut 20% is the README fixture value)\n")
    print("| pool | fee in force | protocol share of gross | our share of active liquidity | LP collect (token0 / token1) | collect vs (gross - protocol) x share | holders after reserve (token0 / token1) |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for name, t in fr["forkTests"].items():
        ps = [x for x in t["protocolShareOfGross"] if x is not None]
        print(f"| {name} | {t['feePpmInForce']} ppm | {', '.join(f'{x:.4f}' for x in ps)} | {t['ourShareOfActive']:.4f} | {t['actualCollectRaw'][0]:,} / {t['actualCollectRaw'][1]:,} | "
              f"diff {t['collectVsExpectedRawDiff'][0]} / {t['collectVsExpectedRawDiff'][1]} raw | {t['holderAfterReserveRaw'][0]:,.0f} / {t['holderAfterReserveRaw'][1]:,.0f} |")


if __name__ == "__main__":
    main()
