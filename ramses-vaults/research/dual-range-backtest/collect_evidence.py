"""Read-only collector for the checkpoint evidence: block hashes for every pin, the keeper's full transaction hashes and
RPC receipts, and the keeper's nonce/balance. Writes evidence/pins-and-receipts.json. No signing, no sends.

  python3 collect_evidence.py

Provenance of each pin is stated in the output. The depth pin's L2 number is NOT derivable from the fork log (the log
prints only the native number, and ~100+ consecutive L2 blocks share one native number); it was recovered from an earlier
subagent report and is only CONSISTENT with the logged value.
"""
import datetime as _dt
import json
import sys

from common import *  # noqa: F401,F403  (Rpc, CACHE, HERE)

KEEPER = "0x978e2ADF18da8B846552932c0aB2B1193da1fAc4"
VAULT = "0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"  # production Uniswap NVDA/USDG vault (the keeper's target)
OUT = HERE / "evidence" / "pins-and-receipts.json"

PINS = [
    # name, L2 block, expected native (l1BlockNumber), provenance
    ("fork-final.log (GroupFeeEntitlement x3 pairs)", 104116096, 26326238, "printed as 'pin 104116096 26326238' at the top of results/fork-final.log"),
    ("fork-depth-all.log (GroupDepth x3 pairs)", 104096178, 26326064,
     "RECOVERED: the log prints only DEPTH_BLOCK: 26326064 (native). The L2 number 104096178 comes from an earlier subagent report; its header's "
     "l1BlockNumber equals the logged native value, but many consecutive L2 blocks share one native number, so this is consistent with, not derived from, the log"),
    ("keeper verification head", 104172613, None, "recorded in the 2026-11-02 keeper verification (chain id, balance, nonce)"),
    ("SPY dataset pool head", 104071867, None, "meta.head of results/dataset_SPY.pkl (baseline_SPY.json dataset.poolHeadBlock); logs were loaded up to 200 blocks behind it"),
    ("pools_static.json head", 104071957, None, "head recorded by the static pool probe (results/pools_static.json), a few blocks after the SPY dataset head"),
    ("NVDA dataset pool head", 104187711, None, "meta.head of results/dataset_NVDA.pkl; swaps after grid anchor 104180000 (8 swaps) were dropped for lack of a header anchor"),
    ("NVDA dataset grid anchor", 104180000, None, "last 10,000-block header anchor used for timestamps (build_dataset.py grid_limit_block)"),
]
KEEPER_TX_BLOCKS = [(104165913, "checkpoint"), (104165952, "rebalance")]


def hx(n):
    return hex(n)


def main():
    r = rpc()
    out = {"schema": "ramses-pins-receipts-1", "readOnly": True, "rpc": "https://rpc.mainnet.chain.robinhood.com",
           "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "command": "python3 " + " ".join(sys.argv),
           "chainId": int(r.call("eth_chainId", []), 16)}
    assert out["chainId"] == 4663, out["chainId"]
    pins = []
    for name, n, native, prov in PINS:
        b = r.call("eth_getBlockByNumber", [hx(n), False])
        got = int(b["l1BlockNumber"], 16)
        pins.append({"name": name, "l2Block": n, "hash": b["hash"], "parentHash": b["parentHash"], "timestamp": int(b["timestamp"], 16),
                     "timestampIso": _dt.datetime.fromtimestamp(int(b["timestamp"], 16), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "nativeL1BlockNumber": got, "expectedNative": native, "nativeMatchesExpected": (native is None or native == got), "provenance": prov})
    out["pins"] = pins
    txs = []
    for n, label in KEEPER_TX_BLOCKS:
        b = r.call("eth_getBlockByNumber", [hx(n), True])
        mine = [t for t in b["transactions"] if t["from"].lower() == KEEPER.lower()]
        for t in mine:
            rc = r.call("eth_getTransactionReceipt", [t["hash"]])
            txs.append({"label": label, "hash": t["hash"], "block": n, "blockHash": b["hash"], "from": t["from"], "to": t["to"], "nonce": int(t["nonce"], 16),
                        "selector": t["input"][:10], "status": int(rc["status"], 16), "gasUsed": int(rc["gasUsed"], 16),
                        "effectiveGasPrice": int(rc["effectiveGasPrice"], 16), "l1Fee": int(rc["l1Fee"], 16) if rc.get("l1Fee") else 0,
                        "feeEthL2Part": int(rc["gasUsed"], 16) * int(rc["effectiveGasPrice"], 16) / 1e18, "logs": len(rc["logs"]),
                        "receipt": rc})
    out["keeperTransactions"] = txs
    out["keeper"] = {"address": KEEPER, "nonceAtLatest": int(r.call("eth_getTransactionCount", [KEEPER, "latest"]), 16),
                     "balanceWeiAtLatest": int(r.call("eth_getBalance", [KEEPER, "latest"]), 16), "latestBlock": int(r.call("eth_blockNumber", []), 16),
                     "floorWei": 300_000_000_000_000}
    out["revertedSends"] = {
        "minedReverted": 0,
        "basis": "nonce accounting: PR #13 evidence r3 (end block 104146691) found keeper nonce 138 = 138 transactions that emitted vault events (0 without events, 0 reverted among them); "
                 "the two transactions above (nonces 138 and 139, status 1) account for the rest, so the nonce at the verification block (140) leaves no mined transaction unexplained. "
                 "A reverted transaction would still consume a nonce.",
        "notMined": "'CheckpointStale' / 'execution reverted' lines in the keeper log are eth_call simulations or estimateGas rejections from the local run (no transaction was sent, no receipt, no cost); they are not mined reverted sends.",
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print("wrote", OUT)
    for p in pins:
        print(p["l2Block"], p["hash"], p["nativeL1BlockNumber"], "OK" if p["nativeMatchesExpected"] else "MISMATCH")
    for t in txs:
        print(t["label"], t["hash"], "status", t["status"], "nonce", t["nonce"])


if __name__ == "__main__":
    main()
