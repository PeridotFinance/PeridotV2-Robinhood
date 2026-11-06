"""Load every pool event type of interest for SPY, GLD, NVDA (read-only, cached) and save decoded JSON."""
import json, sys
from common import *
r = rpc(); head = r.head(); final = head - 200
SIGS = {
    "init": "Initialize(uint160,int24)",
    "swap": "Swap(address,address,int256,int256,uint160,uint128,int24)",
    "mint": "Mint(address,address,uint256,int24,int24,uint128,uint256,uint256)",
    "burn": "Burn(address,int24,int24,uint128,uint256,uint256)",
    "collect": "Collect(address,address,int24,int24,uint128,uint128)",
    "feeadj": "FeeAdjustment(uint24,uint24)",
    "setfee": "SetFee(uint24,uint24)",
    "proto_a": "SetFeeProtocol(uint8,uint8,uint8,uint8)",
    "proto_b": "SetFeeProtocol(uint24,uint24)",
    "proto_c": "SetFeeProtocol(uint24)",
    "collectproto": "CollectProtocol(address,address,uint128,uint128)",
    "flash": "Flash(address,address,uint256,uint256,uint256,uint256)",
}
sel = sys.argv[1:] or ["SPY", "GLD", "NVDA"]
for name in sel:
    a = ADDR[name]["pool"]
    out = {"head": head}
    for k, s in SIGS.items():
        logs = r.logs(a, [T(s)], 0, head, final)
        out[k] = logs
        print(name, k, len(logs), flush=True)
    (CACHE.parent / f"ramses-group-pool-{name}.json").write_text(json.dumps(out))
