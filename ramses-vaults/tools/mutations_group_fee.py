#!/usr/bin/env python3
"""Mutation check for test/fork/GroupFeeEntitlement.t.sol (handoff 01 section 1a gate).

Each case edits the fork test, runs the SPY contract on a fresh pinned head and requires the run to FAIL on the
named assertion; the original bytes are always restored. A mutation that still passes means the gate proves nothing.
Run from the repository root:  python3 ramses-vaults/tools/mutations_group_fee.py [--only NAME]
"""
import argparse, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "ramses-vaults/test/fork/GroupFeeEntitlement.t.sol"
PIN = ROOT / "ramses-vaults/tools/pin.py"
# (name, old, new, expected failing test, expected message fragment)
CASES = [
    ("protocol share expectation", "assertApproxEqAbs(R.proto0, R.gross0 * feeProto / 1_000_000, 8,",
     "assertApproxEqAbs(R.proto0, R.gross0 * 0 / 1_000_000, 8,", "testFork_feeEntitlement", "protocol share token0"),
    ("LP share must exclude protocol", "uint256 lp0 = R.gross0 - R.proto0;", "uint256 lp0 = R.gross0;",
     "testFork_feeEntitlement", "LP collect token0"),
    ("fees must be generated", "Result memory R = _measure(12);\n        (,,,,, uint24 feeProto,)", "Result memory R = _measure(0);\n        (,,,,, uint24 feeProto,)",
     "testFork_feeEntitlement", "no fees generated"),
    ("out-of-range layer earns nothing", "int24 farLo = (tick0 / spacing) * spacing + 20 * spacing * 30;",
     "int24 farLo = (tick0 / spacing) * spacing - 50 * spacing;", "testFork_gaugeRewardsThroughManager", "out-of-range layer"),
    ("manager must pay the wide layer", "        pm.getReward(wideId, tokens);\n", "", "testFork_gaugeRewardsThroughManager",
     "manager pays what the gauge says is earned"),
    ("treasury receives the protocol share", 'assertApproxEqAbs(t0.balanceOf(treasury) - tr0, p0, 2, "treasury token0");',
     'assertApproxEqAbs(t0.balanceOf(treasury) - tr0, 0, 2, "treasury token0");', "testFork_protocolShareDestination", "treasury token0"),
]

def pin_env():
    env = dict(os.environ)
    env.setdefault("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
    out = subprocess.check_output([sys.executable, str(PIN)], env=env, text=True)
    for line in out.splitlines():
        if line.startswith("export "):
            k, v = line[7:].split("=", 1)
            env[k] = v
    return env

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--contract", default="GroupFeeEntitlementSPY")
    a = ap.parse_args()
    original = TEST.read_bytes()
    bad = []
    try:
        for name, old, new, test, frag in CASES:
            if a.only and a.only != name: continue
            src = original.decode()
            assert src.count(old) == 1, f"mutation anchor not unique/present: {name}"
            TEST.write_text(src.replace(old, new))
            env = pin_env(); env["FOUNDRY_PROFILE"] = "ramses"
            r = subprocess.run(["forge", "test", "--threads", "1", "--match-path", "ramses-vaults/test/fork/GroupFeeEntitlement.t.sol",
                                "--match-contract", a.contract, "--match-test", test, "-vv"], cwd=ROOT, env=env, text=True,
                               capture_output=True)
            out = r.stdout + r.stderr
            killed = r.returncode != 0 and frag in out
            print(("KILLED  " if killed else "SURVIVED"), name, "| expected", frag, flush=True)
            if not killed:
                bad.append(name); print(out[-1500:])
    finally:
        TEST.write_bytes(original)
    if bad:
        print("SURVIVORS:", bad); sys.exit(1)
    print("all mutations killed")

if __name__ == "__main__":
    main()
