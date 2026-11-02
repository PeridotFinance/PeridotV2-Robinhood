"""Read-only readback of the LIVE Uniswap NVDA/USDG vault's pair cap, range policy and reserve cut (the "existing caps" the operator named).

  python3 readback_live_caps.py      writes evidence/live-config-readback.json

This is the production Uniswap vault, not the Ramses vault (which is not deployed). It confirms the numbers used as the "current caps"
profile; it does not make them Ramses parameters."""
import datetime as _dt
import json

from common import *  # noqa: F401,F403
from pool_quote import eth_call

VAULT = "0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"
PAIR_LABEL = "NVDA/USDG"
PAIR_ID = "0x" + T(PAIR_LABEL)[2:]
r = rpc()
head = r.head()
block = head - 3
b = r.call("eth_getBlockByNumber", [hex(block), False])
w = words(eth_call(r, VAULT, "pairConfig(bytes32)", PAIR_ID[2:], block=block))
names = ["stockToken", "usdg", "stockAccount", "usdgAccount", "maxPairValueUSDG", "maxSettlementSwapUSDG", "maxCheckpointAge", "deprecatedMinDeadlineDelay",
         "maxDeadlineDelay", "reserveFeeBps", "maxSwapSlippageBps", "withdrawOverUnwindBps", "stockDecimals", "usdgDecimals", "allocationPaused", "swapsPaused", "emergencyMode", "exists"]
cfg = {}
for n, v in zip(names, w):
    cfg[n] = ("0x" + v.to_bytes(32, "big")[12:].hex()) if n in ("stockToken", "usdg", "stockAccount", "usdgAccount") else (bool(v) if n in ("allocationPaused", "swapsPaused", "emergencyMode", "exists") else v)
rp = words(eth_call(r, VAULT, "rangePolicy(bytes32)", PAIR_ID[2:], block=block))
policy = dict(zip(["enabled", "halfWidthTicks", "triggerTicks", "minIntervalSeconds", "maxPerDay", "maxLossBps", "maxRangedValueUsdE18"], [bool(rp[0])] + rp[1:]))
out = {"schema": "ramses-live-config-readback-2", "kind": "LIVE_UNISWAP_NVDA_USDG_VAULT_READBACK", "isRamsesConfiguration": False, "readOnly": True, "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "vault": VAULT, "pairLabel": PAIR_LABEL, "pairId": PAIR_ID, "block": {"l2": block, "hash": b["hash"], "native": int(b["l1BlockNumber"], 16)},
       "pairConfig": cfg, "rangePolicy": policy,
       "readable": {"pairCapUsd": cfg["maxPairValueUSDG"] / 1e18, "rangedCapUsd": policy["maxRangedValueUsdE18"] / 1e18, "settlementSwapCapUsd": cfg["maxSettlementSwapUSDG"] / 1e18,
                    "reserveCutPct": cfg["reserveFeeBps"] / 100}}
(HERE / "evidence" / "live-config-readback.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out["readable"]), out["block"]["l2"], policy)
