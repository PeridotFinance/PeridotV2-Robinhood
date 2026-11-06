"""READ-ONLY, same pin as snapshot.json: can margin-vault users withdraw free pUSDG and redeem it? Simulated with eth_simulateV1. Output: followup.json."""
import json
from bs_lib import *
PU = "0x55aed0569c8f0d166d71face57b57c2f2624a563"; USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"; MV = "0x04d4a5555b7a37017a67b4d21a1da5838de28b9e"
S = json.load(open(HERE / "snapshot.json")); B = hex(S["pin"]["l2"]); RR = S["reads"]
out = {"pin": S["pin"]["l2"], "results": {}}
for u in S["marginVaultLogs"]["users"]:
    free = words(RR[f"mv.free.{u}"])[0]
    calls = [{"from": u, "to": MV, "data": sel("withdraw(address,uint256)") + pa(PU) + pi(free)}, {"from": u, "to": PU, "data": sel("redeem(uint256)") + pi(free)}, {"from": u, "to": USDG, "data": sel("balanceOf(address)") + pa(u)}, {"from": u, "to": PU, "data": sel("balanceOf(address)") + pa(u)}]
    r = call("eth_simulateV1", [{"blockStateCalls": [{"calls": calls}], "validation": False}, B])[0]["calls"]
    bal0 = words(call("eth_call", [{"to": USDG, "data": sel("balanceOf(address)") + pa(u)}, B]))[0]
    out["results"][u] = {"free": free, "withdraw": {"status": int(r[0]["status"], 16), "err": (r[0].get("error") or {}).get("data"), "gas": int(r[0]["gasUsed"], 16)}, "redeem": {"status": int(r[1]["status"], 16), "err": (r[1].get("error") or {}).get("data"), "gas": int(r[1]["gasUsed"], 16)}, "USDG_before": bal0, "USDG_after": words(r[2]["returnData"])[0], "pUSDG_after": words(r[3]["returnData"])[0]}
json.dump(out, open(HERE / "followup.json", "w"), indent=1)
print(json.dumps(out))
