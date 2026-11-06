"""Read-only fee reconciliation for the candidate Ramses pools (SPY, GLD, NVDA from the datasets; META and r33 CL200 from logs).

Four independent checks, none of which waives the fee gate:

 A. FEE TIMELINE. Fee-change (FeeAdjustment) and protocol-share (SetFeeProtocol) events versus the gateway's listed fee, versus
    fee() read by eth_call at the SAME blocks (the public RPC keeps ~9 minutes of state, so "matching blocks" are sampled inside
    that window, including the blocks around every fee change in it) and versus the fee printed by the fork tests at their pin.
 B. SWAP ACCOUNTING (history). For swaps that stay inside one liquidity band the pool charges fee on the gross input, so
    net = L * (change in 1/sqrtP or sqrtP) and implied fee = 1 - net / gross. Compared with the event-derived fee in force.
 C. PROTOCOL / LP SPLIT (pinned window). Over a window inside the state horizon: sum of gross swap fees (input x fee in force),
    the protocol share, and what the pool actually booked, i.e. the change in feeGrowthGlobal0/1X128 (LP entitlement per unit of
    liquidity) and in protocolFees().
 D. HOLDER ENTITLEMENT. The fork tests' measured gross fee -> protocol delta -> collect for the exact two-layer position, then the
    vault's reserve cut (20% fixture value) -> holders.

  python3 fee_reconcile.py            writes evidence/fees/fee-reconciliation.json
"""
from __future__ import annotations

import bisect
import datetime as _dt
import json
import pickle
import re
import statistics
import sys

from common import *  # noqa: F401,F403  (rpc, T, words, sN, HERE, RESULTS)
from pool_quote import POOLS, eth_call, enc_int
from strategy_data import StepFunction

Q128 = 2 ** 128
SIG_FEE = "FeeAdjustment(uint24,uint24)"
SIG_PROTO = "SetFeeProtocol(uint24,uint24)"
SIG_SWAP = "Swap(address,address,int256,int256,uint160,uint128,int24)"
SIG_CPROTO = "CollectProtocol(address,address,uint128,uint128)"
GATEWAY = HERE / "evidence" / "pools" / "gateway-pools.json"
FORK_LOG = HERE / "evidence" / "logs" / "fork-final.log"
FORK_PIN = 104116096
RESERVE_BPS = 2000
DATASET_PAIRS = {"SPY": "SPY/USDG CL1", "GLD": "GLD/USDG CL1", "NVDA": "NVDA/USDG CL10"}


def step_from(events, which):
    evs = sorted(events, key=lambda l: (l["blockNumber"], l["logIndex"]))
    if not evs:
        return StepFunction(None, []), []
    first_old = words(evs[0]["data"])[0]
    ch = [(l["blockNumber"], l["logIndex"], words(l["data"])[1]) for l in evs]
    return StepFunction(first_old, ch), ch


def timeline(r, name, addr, head, listed_ppm):
    fee_ev = r.logs(addr, [T(SIG_FEE)], 0, head, head - 200)
    pro_ev = r.logs(addr, [T(SIG_PROTO)], 0, head, head - 200)
    fee_f, fee_ch = step_from(fee_ev, "fee")
    pro_f, pro_ch = step_from(pro_ev, "proto")
    static = not fee_ev
    if static:        # no FeeAdjustment ever emitted: the fee is static; its value must equal the listed fee() (checked at matched blocks)
        fee_f = StepFunction(listed_ppm, [])
    vals = [c[2] for c in fee_ch]
    out = {"pool": addr, "listedFeePpm": listed_ppm, "staticFeeNoAdjustmentEvents": static, "feeAdjustmentEvents": len(fee_ev), "setFeeProtocolEvents": len(pro_ev),
           "feeInitialBeforeFirstEvent": fee_f.initial, "feeMinPpm": min(vals) if vals else fee_f.initial, "feeMaxPpm": max(vals) if vals else fee_f.initial,
           "feeLastEventPpm": vals[-1] if vals else None,
           "feeDistinctValues": sorted(set(vals + ([fee_f.initial] if fee_f.initial is not None else []))),
           "everEqualsListed": (listed_ppm in set(vals + [fee_f.initial])) if not static else True,
           "protocolShareChanges": [(b, v / 1e6) for b, _, v in pro_ch], "protocolShareInitial": (pro_f.initial or 0) / 1e6 if pro_f.initial is not None else None}
    return out, fee_f, pro_f


def time_weighted(ti, fee_f, end_block):
    """Share of wall-clock time each fee value was in force over the last 24 h / 7 d / 30 d (up to the last header anchor), and the last time
    each value was in force."""
    end_t = ti.ts(end_block)
    marks = [(0, fee_f.initial)] + [(b, v) for (b, _i, v) in fee_f.changes]
    segs = []
    for k, (b, v) in enumerate(marks):
        b_next = marks[k + 1][0] if k + 1 < len(marks) else end_block
        b0, b1 = min(b, end_block), min(b_next, end_block)
        if b1 <= b0:
            continue
        t0 = ti.ts(b0) if b0 > 0 else None
        t1 = ti.ts(b1)
        segs.append((t0, t1, v))
    out = {}
    for label, days in (("24h", 1), ("7d", 7), ("30d", 30)):
        lo = end_t - days * 86400
        acc = {}
        for t0, t1, v in segs:
            if t0 is None:
                t0 = t1 - 1
            a, b = max(t0, lo), min(t1, end_t)
            if b > a:
                acc[v] = acc.get(v, 0.0) + (b - a)
        tot = sum(acc.values()) or 1.0
        out[label] = {str(k): round(v / tot, 4) for k, v in sorted(acc.items())}
        out[label + "_meanPpm"] = round(sum(k * v for k, v in acc.items()) / tot, 2)
    last = {}
    for t0, t1, v in segs:
        last[v] = t1
    out["lastTimeInForce"] = {str(v): _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ") for v, t in sorted(last.items())}
    out["endBlock"] = end_block
    out["endIso"] = _dt.datetime.fromtimestamp(end_t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def matched_blocks(r, addr, fee_f, head):
    """fee() by eth_call at sampled blocks inside the state horizon versus the event-derived fee at the same block."""
    hi = head - 5
    lo = hi - 3300
    pts = set(range(lo, hi + 1, 220))
    for (b, _i, _v) in fee_f.changes:
        if lo <= b - 1 and b <= hi:
            pts.update({b - 1, b})
    rows, bad = [], 0
    for b in sorted(pts):
        onchain = words(eth_call(r, addr, "fee()", block=b))[0]
        ev = fee_f(b, 10 ** 9)
        rows.append({"block": b, "feeCall": onchain, "feeFromEvents": ev, "match": onchain == ev})
        bad += 0 if onchain == ev else 1
    return {"samples": len(rows), "mismatches": bad, "rows": rows, "feeChangesInWindow": sum(1 for (b, _i, _v) in fee_f.changes if lo <= b <= hi)}


def swap_accounting(pair):
    cols, meta = pickle.load(open(RESULTS / f"dataset_{pair}.pkl", "rb"))
    n = len(cols["ts"])
    by_fee = {}
    used = 0
    for i in range(1, n):
        if cols["L_after"][i] != cols["L_after"][i - 1]:
            continue                       # liquidity changed (band crossing or mint/burn): not a single-band swap
        sa, sb, L, g = cols["s_after"][i - 1], cols["s_after"][i], cols["L_after"][i], cols["in_amt"][i]
        if g <= 0 or sa == sb:
            continue
        tok = cols["in_tok"][i]
        net = L * (1.0 / sb - 1.0 / sa) if tok == 0 else L * (sb - sa)
        if net <= 0:
            continue
        if abs(sb - sa) / sa < 1e-7:        # price move too small for double precision to resolve a 1e-4 fee
            continue
        imp = (1.0 - net / g) * 1e6
        by_fee.setdefault(cols["fee_ppm"][i], []).append(imp)
        used += 1
    rows = []
    for fee, xs in sorted(by_fee.items()):
        xs.sort()
        med = statistics.median(xs)
        rows.append({"eventFeePpm": fee, "swaps": len(xs), "impliedMedianPpm": med, "impliedP10": xs[len(xs) // 10], "impliedP90": xs[9 * len(xs) // 10],
                     "medianRatio": med / fee if fee else None, "within5pct": sum(1 for x in xs if abs(x - fee) <= 0.05 * fee) / len(xs)})
    rows.sort(key=lambda r_: -r_["swaps"])
    return {"swapsTotal": n, "singleBandSwapsUsed": used, "byEventFee": rows[:8]}


def window_split(r, name, addr, fee_f, pro_f, head):
    B = head - 3
    A = B - 3300
    def rd(sig, blk):
        return words(eth_call(r, addr, sig, block=blk))
    fg0A, fg1A = rd("feeGrowthGlobal0X128()", A)[0], rd("feeGrowthGlobal1X128()", A)[0]
    fg0B, fg1B = rd("feeGrowthGlobal0X128()", B)[0], rd("feeGrowthGlobal1X128()", B)[0]
    pA, pB = rd("protocolFees()", A), rd("protocolFees()", B)
    slot0 = rd("slot0()", B)
    swaps = r.logs(addr, [T(SIG_SWAP)], A + 1, B, B)
    cprot = r.logs(addr, [T(SIG_CPROTO)], A + 1, B, B)
    gross = [0.0, 0.0]
    proto = [0.0, 0.0]
    lp = [0.0, 0.0]
    pred_fg = [0.0, 0.0]
    pred_fg_clean = [0.0, 0.0]
    prevL = None
    clean_vol = unclean_vol = 0.0
    for l in sorted(swaps, key=lambda x: (x["blockNumber"], x["logIndex"])):
        w = words(l["data"])
        a0, a1, L = sN(w[0], 256), sN(w[1], 256), float(w[3])
        tok = 0 if a0 > 0 else 1
        g_in = float(a0 if tok == 0 else a1)
        fee = fee_f(l["blockNumber"], l["logIndex"]) / 1e6
        share = (pro_f(l["blockNumber"], l["logIndex"]) if pro_f.initial is not None else slot0[5]) / 1e6
        gf = g_in * fee
        pc = gf * share
        gross[tok] += gf
        proto[tok] += pc
        lp[tok] += gf - pc
        if L > 0:
            pred_fg[tok] += (gf - pc) * Q128 / L
            if prevL == L:
                pred_fg_clean[tok] += (gf - pc) * Q128 / L
                clean_vol += g_in if tok == 1 else 0
            else:
                unclean_vol += g_in if tok == 1 else 0
        prevL = L
    act_fg = [fg0B - fg0A, fg1B - fg1A]
    act_pr = [pB[0] - pA[0], pB[1] - pA[1]]
    rows = []
    for k in (0, 1):
        rows.append({"token": k, "grossSwapFeeRaw": gross[k], "protocolShareRaw": proto[k], "lpFeeRaw": lp[k],
                     "protocolFeesDeltaRaw": act_pr[k], "protocolRatioActualOverPredicted": (act_pr[k] / proto[k]) if proto[k] else None,
                     "feeGrowthDeltaActual": act_fg[k], "feeGrowthDeltaPredictedAll": pred_fg[k], "feeGrowthDeltaPredictedSingleBand": pred_fg_clean[k],
                     "feeGrowthActualOverPredictedAll": (act_fg[k] / pred_fg[k]) if pred_fg[k] else None})
    return {"fromBlock": A, "toBlock": B, "swapsInWindow": len(swaps), "collectProtocolEventsInWindow": len(cprot), "slot0FeeProtocolRaw": slot0[5],
            "feeAtEnd": rd("fee()", B)[0], "perToken": rows,
            "note": "predicted feeGrowth uses the liquidity reported by each Swap event (after the swap); band-crossing swaps make it approximate. "
                    "protocolFees() is only comparable when no CollectProtocol event fell in the window."}


def parse_fork_log():
    txt = FORK_LOG.read_text()
    blocks = re.split(r"Ran 3 tests for ramses-vaults/test/fork/GroupFeeEntitlement.t.sol:GroupFeeEntitlement", txt)[1:]
    out = {}
    for b in blocks:
        name = b.split(":")[0].split("\n")[0].strip()
        g = lambda key: int(re.search(re.escape(key) + r":\s*(\d+)", b).group(1))
        gross0, gross1 = g("gross fee token0 raw"), g("gross fee token1 raw")
        pr0, pr1 = g("protocol delta token0 raw"), g("protocol delta token1 raw")
        ours, active = g("our liquidity"), g("active liquidity")
        c0, c1 = g("collect token0 (both layers) raw"), g("collect token1 (both layers) raw")
        e0, e1 = g("expected LP collect token0"), g("expected LP collect token1")
        res = {"feePpmInForce": g("fee ppm in force"), "slot0FeeProtocolPpm": g("slot0.feeProtocol ppm"),
               "grossFeeRaw": [gross0, gross1], "protocolDeltaRaw": [pr0, pr1], "protocolShareOfGross": [pr0 / gross0 if gross0 else None, pr1 / gross1 if gross1 else None],
               "ourLiquidity": ours, "activeLiquidity": active, "ourShareOfActive": ours / active,
               "lpFeeAfterProtocolRaw": [gross0 - pr0, gross1 - pr1], "expectedCollectRaw": [e0, e1], "actualCollectRaw": [c0, c1],
               "holderAfterReserveRaw": [c0 * (10000 - RESERVE_BPS) / 10000, c1 * (10000 - RESERVE_BPS) / 10000],
               "reserveCutRaw": [c0 * RESERVE_BPS / 10000, c1 * RESERVE_BPS / 10000],
               "collectVsExpectedRawDiff": [c0 - e0, c1 - e1]}
        out[name] = res
    return out


def main():
    r = rpc()
    head = r.head()
    gw = {p["id"].lower(): p for p in json.loads(GATEWAY.read_text())["pools"]}
    ti = TimeIndex(r, path=CACHE / "headers.json")
    end_anchor = (head // ti.step) * ti.step
    ti.ensure(end_anchor - 40 * 86400 * 8 - ti.step, end_anchor - 1)   # >= 30 days of headers behind the last anchor (about 8 blocks per second)
    out = {"schema": "ramses-fee-reconciliation-1", "readOnly": True, "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "command": "python3 " + " ".join(sys.argv), "head": head,
           "gate": "NOT waived. This file reconciles numbers; whether a fee difference is acceptable, and any GLD activation, remain operator decisions.",
           "pools": {}}
    for name, (addr, *_rest) in POOLS.items():
        listed = gw[addr.lower()]["feeTier"]
        tl, fee_f, pro_f = timeline(r, name, addr, head, listed)
        tl["gatewayFeeProtocol"] = gw[addr.lower()]["feeProtocol"]
        tl["gatewayHasDynamicFees"] = gw[addr.lower()]["hasDynamicFees"]
        tl["feeInForceAtForkPin_%d" % FORK_PIN] = fee_f(FORK_PIN, 10 ** 9)
        tl["matchedBlocks"] = matched_blocks(r, addr, fee_f, head)
        tl["timeWeightedFee"] = time_weighted(ti, fee_f, end_anchor - ti.step)
        tl["windowSplit"] = window_split(r, name, addr, fee_f, pro_f, head)
        pair = next((p for p, n in DATASET_PAIRS.items() if n == name), None)
        if pair:
            tl["swapAccounting"] = swap_accounting(pair)
            ds = json.loads((HERE / "evidence" / "dataset_checks.json").read_text()).get(pair)
            if ds:
                tl["volumeWeightedFeePpmOverDataset"] = ds["volume_weighted_fee_ppm"]
        out["pools"][name] = tl
        print(name, "listed", listed, "events", tl["feeAdjustmentEvents"], "distinct", tl["feeDistinctValues"][:8], "everEqualsListed", tl["everEqualsListed"],
              "| matched", tl["matchedBlocks"]["samples"], "mismatches", tl["matchedBlocks"]["mismatches"], flush=True)
    out["forkTests"] = parse_fork_log()
    out["forkPin"] = FORK_PIN
    p = HERE / "evidence" / "fees" / "fee-reconciliation.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", p)


if __name__ == "__main__":
    main()
