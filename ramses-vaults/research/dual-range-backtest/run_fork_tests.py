"""Pin a recent block of the public Robinhood RPC (which serves archive state for only a few minutes) and run a forge fork test file at it.
Writes the log with the same first line the committed fork logs use:  pin <l2 block> <native EVM block> <block hash>

  python3 run_fork_tests.py <test path under repo root> <log file> [forge --match-contract/--match-test args ...]
Read-only: the fork is local, nothing is signed or sent.
"""
import os
import subprocess
import sys
from pathlib import Path

import json

REPO = Path(__file__).resolve().parents[3]


def main():
    path, log = sys.argv[1], Path(sys.argv[2])
    extra = sys.argv[3:]
    def call(method, params):
        out = subprocess.run(["curl", "-s", "-m", "30", "-A", "curl/8.0", "-H", "content-type: application/json", "--data",
                              json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}), "https://rpc.mainnet.chain.robinhood.com"],
                             capture_output=True, text=True).stdout
        return json.loads(out)["result"]

    # compile FIRST: the public RPC serves archive state for only a few minutes, so the pin must be taken right before the fork test starts
    benv = dict(os.environ, FOUNDRY_PROFILE="ramses")
    b0 = subprocess.run(["forge", "build"], cwd=REPO, env=benv, capture_output=True, text=True)
    if b0.returncode != 0:
        print(b0.stdout[-2000:], b0.stderr[-2000:])
        sys.exit(3)
    block = int(call("eth_blockNumber", []), 16) - 5
    b = call("eth_getBlockByNumber", [hex(block), False])
    native = int(b["l1BlockNumber"], 16)
    env = dict(os.environ, FOUNDRY_PROFILE="ramses", ROBINHOOD_RPC_URL="https://rpc.mainnet.chain.robinhood.com",
               REMEDIATION_FORK_BLOCK=str(block), REMEDIATION_NATIVE_BLOCK=str(native))
    proc = subprocess.run(["forge", "test", "--match-path", path, "--threads", "1", "-vv"] + extra, cwd=REPO, env=env, capture_output=True, text=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"pin {block} {native} {b['hash']}\nrc={proc.returncode}\n" + proc.stdout + proc.stderr)
    print(f"pin {block} {native} {b['hash']} rc={proc.returncode} -> {log}")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
