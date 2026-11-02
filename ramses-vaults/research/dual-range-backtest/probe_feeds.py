"""Read-only on-chain proof of each Chainlink feed: description, decimals, latestRoundData, aggregator."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "lib"))
from evidence_lib import Rpc, call_data, words, iso, keccak_topic

FEEDS = {
    "SPY": "0x319724394D3A0e3669269846abE664Cd621f9f6A",
    "GLD": "0x470A51258068043bd43dC0a56245625C9fE86eB0",
    "NVDA": "0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15",
    "USDG": "0x61B7e5650328764B076A108EFF5fa7282a1B9aD2",
}
rpc = Rpc()
head = rpc.head()
print("head", head)

def s(res):
    b = bytes.fromhex(res[2:]); n = int.from_bytes(b[32:64], "big"); return b[64:64+n].decode()

out = {"head": head}
for name, a in FEEDS.items():
    r = {}
    r["code_len"] = (len(rpc.call("eth_getCode", [a, "latest"])) - 2) // 2
    r["description"] = s(rpc.eth_call(a, keccak_topic("description()")[:10]))
    r["decimals"] = int(rpc.eth_call(a, keccak_topic("decimals()")[:10]), 16)
    r["version"] = int(rpc.eth_call(a, keccak_topic("version()")[:10]), 16)
    w = words(rpc.eth_call(a, keccak_topic("latestRoundData()")[:10]))
    r["latest"] = {"roundId": w[0], "answer": w[1], "startedAt": w[2], "updatedAt": w[3], "answeredInRound": w[4], "updatedAtIso": iso(w[3])}
    agg = rpc.eth_call(a, keccak_topic("aggregator()")[:10])
    r["aggregator"] = "0x" + agg[-40:]
    r["aggregator_code_len"] = (len(rpc.call("eth_getCode", [r["aggregator"], "latest"])) - 2) // 2
    for fn in ("minAnswer()", "maxAnswer()", "latestAnswer()", "typeAndVersion()", "owner()"):
        try:
            res = rpc.eth_call(r["aggregator"], keccak_topic(fn)[:10])
            r[fn] = s(res) if fn == "typeAndVersion()" else (("0x"+res[-40:]) if fn == "owner()" else int(res, 16) if int(res,16) < 2**255 else int(res,16)-2**256)
        except Exception as e:
            r[fn] = "ERR " + str(e)[:60]
    out[name] = r
    print(name, json.dumps(r, indent=1))
Path(__file__).parent.joinpath("results/feeds.json").write_text(json.dumps(out, indent=1))
