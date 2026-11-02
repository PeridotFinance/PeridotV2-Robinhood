"""Write evidence/proposed-ramses-settings.json: the settings the Ramses baselines ASSUME, each with its source and status.

None of these is deployed or approved. The Ramses vault is not deployed, so there is nothing on chain to read back for it; the only on-chain
readback is the LIVE Uniswap NVDA/USDG vault (live-config-readback.json), which is a different contract."""
import datetime as _dt
import json

from common import HERE
import run_baseline as RB

live = json.loads((HERE / "evidence" / "live-config-readback.json").read_text())
cap = RB.PROFILES["caps"]
out = {
    "schema": "ramses-proposed-settings-1", "status": "PROPOSED / ASSUMED FOR RESEARCH. NOT DEPLOYED. NOT APPROVED. NOT READ BACK FROM A RAMSES VAULT.",
    "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "liveUniswapReadback": {"file": "live-config-readback.json", "vault": live["vault"], "block": live["block"]["l2"], "isRamsesConfiguration": False,
                            "values": live["readable"], "rangePolicy": live["rangePolicy"]},
    "assumedForRamsesBaselines": {
        "pairCapUsd": {"value": cap[0], "source": "operator-supplied", "status": "pending configuration readback of the Ramses vault; the live Uniswap vault reads back the same number", "matchesLiveUniswap": live["readable"]["pairCapUsd"] == cap[0]},
        "rangedCapUsd": {"value": cap[1], "source": "operator-supplied", "status": "pending configuration readback; matches the live Uniswap vault's maxRangedValueUsd", "matchesLiveUniswap": live["readable"]["rangedCapUsd"] == cap[1]},
        "reserveFeeBps": {"value": RB.RESERVE_BPS, "source": "ramses-vaults/README.md fixture value", "status": "configuration choice, not decided", "matchesLiveUniswap": live["pairConfig"]["reserveFeeBps"] == RB.RESERVE_BPS},
        "rangePolicyBaseline": {"halfWidthTicks": 600, "triggerTicks": 300, "minIntervalSeconds": 3600, "maxPerDay": 24, "maxLossBps": 10, "source": "chosen by this research as the nearest contract-feasible single range", "status": "proposal for analysis only; the live Uniswap policy is different (see liveUniswapReadback.rangePolicy)"},
        "recenterGasUsd": {"value": RB.RECENTER_GAS_USD, "source": "Uniswap keeper measured cost used as a proxy (PR #13 evidence r3)", "status": "assumption, not a Ramses measurement"},
        "maintenanceUsdPerDay": {"value": RB.MAINTENANCE_USD_PER_DAY, "observedAverage": RB.MAINTENANCE_OBSERVED_AVG_USD_PER_DAY, "source": "same evidence", "status": "assumption, operator/protocol-paid"},
        "keeperPollSeconds": {"value": RB.POLL_S, "source": "the deployed Uniswap keeper's INTERVAL_SECONDS", "status": "assumption for the Ramses keeper"},
    },
}
(HERE / "evidence" / "proposed-ramses-settings.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v["value"] for k, v in out["assumedForRamsesBaselines"].items() if "value" in v}))
