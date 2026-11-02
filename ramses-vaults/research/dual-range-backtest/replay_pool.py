"""Pin a pool, save its full inputs, and validate the offline quote walk against the DEPLOYED pool on a fork at the SAME block.

  python3 replay_pool.py "<pool name>" [--repo-root PATH]

Steps: read the pinned inputs (pool_quote.read_inputs), compute the offline expectations (quote_math.swap) for exact integer trade
amounts, run test/fork/PoolQuoteReplay.t.sol at that block, and compare consumed input, output and final sqrtPrice. Fails (non-zero)
on any mismatch beyond rounding. Writes evidence/pools/fork-replay/<slug>.json and the raw forge log. Read-only: the fork is local.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from common import HERE
import pool_quote as PQ
import quote_math as QM

REPO = HERE.parent.parent.parent           # .../PeridotV2-Robinhood checkout root (research dir is ramses-vaults/research/dual-range-backtest)
OUTDIR = HERE / "evidence" / "pools" / "fork-replay"
LIMIT_PCT = 10.0
TOL_REL = 1e-6


def expected(inputs, plan, buy_limit, sell_limit):
    usdg0 = plan["usdg0"]
    st0 = QM.make_state(inputs)
    exp = {}
    for i, (b, s) in enumerate(zip(plan["buy"], plan["sell"])):
        for kind, amt, zfo, lim in (("buy", b, usdg0, None), ("sell", s, not usdg0, None), ("buyLimited", b, usdg0, buy_limit), ("sellLimited", s, not usdg0, sell_limit)):
            st = copy.deepcopy(st0)
            r = QM.swap(st, amt, zfo, lim)
            exp[(kind, i)] = (amt, r["consumed"], r["out"], r["sqrtAfter"], r["status"])
        st = copy.deepcopy(st0)
        buy = QM.swap(st, b, usdg0)
        sold = QM.swap(st, buy["out"], not usdg0) if buy["status"] == "filled" else None
        exp[("sequential", i)] = (b, buy["out"], sold["out"] if sold else None, 0.0, buy["status"] + ("" if sold is None else "/" + sold["status"]))
    return exp


def main():
    name = sys.argv[1]
    root = Path(sys.argv[sys.argv.index("--repo-root") + 1]) if "--repo-root" in sys.argv else REPO
    addr, spacing, mode = PQ.POOLS[name]
    r = PQ.rpc()
    block = r.head() - 5
    inputs = PQ.read_inputs(addr, spacing, mode, block)
    PQ.INPUT_DIR.mkdir(parents=True, exist_ok=True)
    (PQ.INPUT_DIR / f"{PQ.SLUG(name)}.json").write_text(json.dumps(inputs, indent=1))
    plan = QM.trade_plan(inputs, PQ.USDG_ADDR, PQ.SIZES_USD)
    st0 = QM.make_state(inputs)
    buy_lim = QM.limit_sqrt(st0["s"], plan["usdg0"], True, LIMIT_PCT)
    sell_lim = QM.limit_sqrt(st0["s"], plan["usdg0"], False, LIMIT_PCT)
    buy_lim_x96, sell_lim_x96 = int(buy_lim * QM.Q96), int(sell_lim * QM.Q96)
    exp = expected(inputs, plan, buy_lim_x96 / QM.Q96, sell_lim_x96 / QM.Q96)
    env = dict(os.environ, FOUNDRY_PROFILE="ramses", ROBINHOOD_RPC_URL="https://rpc.mainnet.chain.robinhood.com",
               REMEDIATION_FORK_BLOCK=str(block), REMEDIATION_NATIVE_BLOCK=str(inputs["pin"]["native"]), POOL=addr, USDG=PQ.USDG_ADDR,
               BUY_AMOUNTS=",".join(map(str, plan["buy"])), SELL_AMOUNTS=",".join(map(str, plan["sell"])), BUY_LIMIT=str(buy_lim_x96), SELL_LIMIT=str(sell_lim_x96))
    proc = subprocess.run(["forge", "test", "--match-path", "ramses-vaults/test/fork/PoolQuoteReplay.t.sol", "--threads", "1", "-vv"], cwd=root, env=env, capture_output=True, text=True)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"{PQ.SLUG(name)}.forge.log").write_text(f"pin {block} {inputs['pin']['native']} {inputs['pin']['hash']}\n" + proc.stdout + proc.stderr)
    rows = re.findall(r"REPLAY,(\w+),(\d+),(\d+),(\d+),(\d+),(\d+)", proc.stdout)
    if proc.returncode != 0 or not rows:
        print("FORK TEST FAILED OR PRODUCED NO ROWS; see", OUTDIR / f"{PQ.SLUG(name)}.forge.log")
        print((proc.stdout + proc.stderr)[-1500:])
        sys.exit(2)
    report, bad = [], 0
    for kind, i, req, consumed, out, sqrt_after in rows:
        i, req, consumed, out, sqrt_after = int(i), int(req), int(consumed), int(out), int(sqrt_after)
        e_req, e_cons, e_out, e_sqrt, status = exp[(kind, i)]
        if kind == "sequential":
            ok = e_out is not None and abs(consumed - e_cons) <= max(2, TOL_REL * e_cons) and abs(out - e_out) <= max(2, TOL_REL * e_out)
            rec = {"kind": kind, "index": i, "requestedIn": req, "forkStock": consumed, "forkUsdgBack": out, "offlineStock": e_cons, "offlineUsdgBack": e_out, "offlineStatus": status, "match": bool(ok)}
        else:
            lower_bound = status == "scan_window_exhausted"
            if lower_bound:
                # the offline scan stopped at its window edge: what lies beyond is UNKNOWN to it, so the deployed pool may fill more.
                # The offline numbers must then be a LOWER BOUND of the fork's, never above.
                ok = consumed >= e_cons - max(2, TOL_REL * e_cons) and out >= e_out - max(2, TOL_REL * e_out)
            else:
                ok = (abs(consumed - e_cons) <= max(2, TOL_REL * max(e_cons, 1)) and abs(out - e_out) <= max(2, TOL_REL * max(e_out, 1))
                      and abs(sqrt_after / QM.Q96 - e_sqrt) <= 1e-9 * e_sqrt)
            rec = {"offlineIsLowerBound": lower_bound, "kind": kind, "index": i, "requestedIn": req, "forkConsumed": consumed, "forkOut": out, "forkSqrtAfter": sqrt_after,
                   "offlineConsumed": e_cons, "offlineOut": e_out, "offlineSqrtAfter": e_sqrt, "offlineStatus": status,
                   "forkFilledFraction": consumed / req if req else 1.0, "match": bool(ok)}
        report.append(rec)
        bad += 0 if ok else 1
        print(("OK   " if ok else "FAIL ") + ("[lower bound] " if rec.get("offlineIsLowerBound") else "") + f"{kind:12s} #{i} requested {req} fork consumed {consumed} out {out} | offline {e_cons:.6g} {e_out if e_out is None else format(e_out, '.6g')} [{status}]")
    doc = {"schema": "ramses-fork-replay-1", "pool": name, "address": addr, "pin": inputs["pin"], "limitPct": LIMIT_PCT, "toleranceRel": TOL_REL,
           "plan": plan, "rows": report, "mismatches": bad, "inputsFile": f"../inputs/{PQ.SLUG(name)}.json"}
    (OUTDIR / f"{PQ.SLUG(name)}.json").write_text(json.dumps(doc, indent=1))
    print(f"{name}: {len(report) - bad}/{len(report)} rows match the deployed pool at block {block}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
