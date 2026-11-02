"""Pin a pool, save its full inputs BY BLOCK AND HASH, and validate the offline quote walk against the DEPLOYED pool on a fork at the SAME block.

  python3 replay_pool.py "<pool name>" [--repo-root PATH]

Steps: read the pinned inputs (pool_quote.read_inputs) and save the snapshot (never overwritten), run test/fork/PoolQuoteReplay.t.sol at that block with the
exact integer amounts and price limits the offline walk uses, then compare through replay_check.check_rows, which REJECTS missing, duplicate, unexpected
or mismatched rows. The proof document records the snapshot path and its sha256, the raw forge log and ITS sha256, and every fork row, so
verify_replays.py can re-check it later without the network. Fails (non-zero) on any error. Read-only: the fork is local.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from common import HERE
import pool_quote as PQ
import quote_math as QM
import replay_check as RC

REPO = HERE.parent.parent.parent
OUTDIR = HERE / "evidence" / "pools" / "fork-replay"
LIMIT_PCT = 10.0
ROW_RE = re.compile(r"REPLAY,(\w+),(\d+),(\d+),(\d+),(\d+),(\d+)")


def parse_rows(text):
    return [{"kind": k, "index": int(i), "requestedIn": int(req), "consumed": int(c), "out": int(o), "sqrtAfter": int(s)} for k, i, req, c, o, s in ROW_RE.findall(text)]


def main():
    name = sys.argv[1]
    root = Path(sys.argv[sys.argv.index("--repo-root") + 1]) if "--repo-root" in sys.argv else REPO
    addr, spacing, mode = PQ.POOLS[name]
    r = PQ.rpc()
    block = r.head() - 5
    inputs = PQ.read_inputs(addr, spacing, mode, block)
    rel, sha = PQ.save_inputs(name, inputs)
    plan, _exp, (buy_lim, sell_lim) = RC.expected_rows(inputs, PQ.USDG_ADDR, PQ.SIZES_USD, LIMIT_PCT)
    env = dict(os.environ, FOUNDRY_PROFILE="ramses", ROBINHOOD_RPC_URL="https://rpc.mainnet.chain.robinhood.com",
               REMEDIATION_FORK_BLOCK=str(block), REMEDIATION_NATIVE_BLOCK=str(inputs["pin"]["native"]), POOL=addr, USDG=PQ.USDG_ADDR,
               BUY_AMOUNTS=",".join(map(str, plan["buy"])), SELL_AMOUNTS=",".join(map(str, plan["sell"])), BUY_LIMIT=str(buy_lim), SELL_LIMIT=str(sell_lim))
    proc = subprocess.run(["forge", "test", "--match-path", "ramses-vaults/test/fork/PoolQuoteReplay.t.sol", "--threads", "1", "-vv"], cwd=root, env=env, capture_output=True, text=True)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    stem = f"{PQ.SLUG(name)}@{block}"
    log_path = OUTDIR / f"{stem}.forge.log"
    log_text = f"pin {block} {inputs['pin']['native']} {inputs['pin']['hash']}\n" + proc.stdout + proc.stderr
    log_path.write_text(log_text)
    fork_rows = parse_rows(proc.stdout)
    if proc.returncode != 0 or not fork_rows:
        print("FORK TEST FAILED OR PRODUCED NO ROWS; see", log_path)
        print((proc.stdout + proc.stderr)[-1500:])
        sys.exit(2)
    report, errors = RC.check_rows(inputs, PQ.USDG_ADDR, PQ.SIZES_USD, LIMIT_PCT, fork_rows)
    for rep in report:
        print(("OK   " if rep["match"] else "FAIL ") + ("[lower bound] " if rep["offlineIsLowerBound"] else "") + f"{rep['kind']:12s} #{rep['index']} requested {rep['requestedIn']} fork consumed {rep['forkConsumed']} out {rep['forkOut']}")
    for e in errors:
        print("ERROR", e)
    doc = {"schema": "ramses-fork-replay-2", "pool": name, "address": addr, "pin": inputs["pin"], "usdg": PQ.USDG_ADDR, "sizesUsd": PQ.SIZES_USD, "limitPct": LIMIT_PCT,
           "toleranceRel": RC.TOL_REL, "inputsFile": rel, "inputsSha256": sha, "forgeLogFile": f"fork-replay/{log_path.name}", "forgeLogSha256": PQ.file_sha256(log_path),
           "expectedRowCount": len(RC.KINDS) * len(PQ.SIZES_USD) + len(PQ.SIZES_USD), "forkRows": fork_rows, "report": report, "errors": errors, "verified": not errors}
    (OUTDIR / f"{stem}.json").write_text(json.dumps(doc, indent=1))
    print(f"{name}: {'VERIFIED' if not errors else 'NOT VERIFIED'}: {len(report) - len(errors)}/{doc['expectedRowCount']} rows at block {block}; snapshot {rel}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
