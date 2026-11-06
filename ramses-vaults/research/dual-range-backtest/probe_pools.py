"""Static on-chain facts for the three pools and gauges (read-only)."""
import json
from common import *
r = rpc(); head = r.head()
def a(res): return "0x" + res[-40:]
def call(to, sig, blk="latest"): return r.eth_call(to, T(sig)[:10], blk)
def u(res, signed=False):
    v = int(res, 16); return v - 2**256 if signed and v >= 2**255 else v
out = {"head": head}
for name, c in ADDR.items():
    p = c["pool"]; g = c["gauge"]; o = {}
    o["token0"], o["token1"] = a(call(p, "token0()")), a(call(p, "token1()"))
    o["factory"] = a(call(p, "factory()"))
    o["tickSpacing"] = u(call(p, "tickSpacing()"), True)
    o["fee_ppm"] = u(call(p, "fee()"))
    o["liquidity"] = u(call(p, "liquidity()"))
    w = words(call(p, "slot0()"))
    o["slot0"] = dict(sqrtPriceX96=w[0], tick=sN(w[1], 24), obsIdx=w[2], obsCard=w[3], obsCardNext=w[4], feeProtocol=w[5], unlocked=w[6])
    o["protocolFees"] = words(call(p, "protocolFees()"))
    o["gauge_pool"] = a(call(g, "pool()"))
    o["gauge_nfpManager"] = a(call(g, "nfpManager()"))
    o["gauge_voter"] = a(call(g, "voter()"))
    o["gauge_rewardValidator"] = a(call(g, "rewardValidator()"))
    res = call(g, "getRewardTokens()"); ww = words(res); n = ww[1]
    o["reward_tokens"] = ["0x" + format(x, "040x") for x in ww[2:2+n]]
    o["rewardRate"] = {t: u(r.eth_call(g, T("rewardRate(address)")[:10] + t[2:].rjust(64, "0"))) for t in o["reward_tokens"]}
    try:
        o["pool_gauge_via_voter"] = a(r.eth_call(o["gauge_voter"], T("gaugeForPool(address)")[:10] + p[2:].lower().rjust(64, "0")))
    except Exception as e: o["pool_gauge_via_voter"] = "ERR " + str(e)[:80]
    for fn in ("feeCollector()", "owner()", "accessHub()", "gauge()", "feeProtocol()"):
        try: o["pool_" + fn] = a(call(p, fn)) if fn != "feeProtocol()" else u(call(p, fn))
        except Exception as e: o["pool_" + fn] = "ERR"
    o["feeGrowthGlobal"] = [u(call(p, "feeGrowthGlobal0X128()")), u(call(p, "feeGrowthGlobal1X128()"))]
    out[name] = o
    print(name, json.dumps(o, indent=1))
(RESULTS / "pools_static.json").write_text(json.dumps(out, indent=1))
