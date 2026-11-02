"""Compare the executable ways to SELL r33 for USDG on Robinhood Chain at pinned blocks (read-only).

The partner asked for execution and range-management needs to be compared across routes rather than defaulting to CL500 or DLMM200.
For fixed r33 amounts this quotes every route found in the gateway snapshot:

  direct:  r33/USDG CL200, CL500, CL5 (concentrated; local pool math over fully scanned ticks, the same walk validated against the
           deployed pool on a fork), r33/USDG DLMM bin200 (the pair's own getSwapOut), r33/USDG legacy constant-product pair
  two hop: r33 -> RAM via r33/RAM CL100, CL200, DLMM bin200, DLMM bin25, then RAM -> USDG via RAM/USDG DLMM bin100, the RAM/USDG legacy
           pair, or RAM -> WETH (WETH/RAM CL100) -> USDG (WETH/USDG CL1)

Each pool is read at its own recent block (hashes recorded), so a multi-hop figure is a NON-ATOMIC composite of independent pins. Nothing
is executed; quotes are not router quotes. A hop that cannot take its whole input reports its status instead of a cost. The conclusion is
about THESE quoted routes only.

  python3 r33_routes.py            writes evidence/pools/r33-routes.json (+ inputs/ for the CL pools)
"""
from __future__ import annotations

import copy
import datetime as _dt
import json

from common import *  # noqa: F401,F403
import pool_quote as PQ
import quote_math as QM

R33 = "0x4e5195469a0360f5dfe811730c9ccacff0a0feee"
RAM = "0x5173d45a1191ee33cbb7d8c7e65f21b04ed54802"
AMOUNTS_R33 = [1_000, 5_000, 20_000, 100_000]          # r33 tokens (about $47, $234, $934, $4,700 at the CL200 mid)

CL = {   # name -> (address, spacing, scan mode)
    "r33/USDG CL200": ("0xc276b9a27ab1e60e32b581db6a23bb1fab0fc30b", 200, "full"),
    "r33/USDG CL500": ("0x9f301fa81ccdda903a3590e7b9abee12e035709e", 500, "full"),
    "r33/USDG CL5": ("0x2aa22c8970d278156e3cbdeb58f40d42f7d8aa7c", 5, "full"),
    "r33/RAM CL100": ("0xb2f5a2eef7cca3d0057f95352b2d0cf6e084bb96", 100, "full"),
    "r33/RAM CL200": ("0xe8bcb3243c79d313f6d6f168f4237989b1648f5c", 200, "full"),
    "WETH/RAM CL100": ("0x5860dd468b4fc1740eaf80a3b7f22e78d70a076a", 100, "full"),
    "WETH/USDG CL1": ("0xfae65eaa11943f45e83d5b45dc7a7c801c51bfb0", 1, "window"),
}
DLMM = {"r33/USDG DLMM200": "0x544c010194d904a152524455fd9f3de59bbac034", "r33/RAM DLMM200": "0x865ce837dd246e4059f3d435eec9eab9a78fa8e6",
        "r33/RAM DLMM25": "0xcf507f940d7b58c46efaaa7d307e98c1be27f12f", "RAM/USDG DLMM100": "0xe5c549d30eb7ac99dcaaddd4a7fec48c5d523a31"}
V2 = {"r33/USDG V2": "0xd8b66ac1a247db8f32ce0ea9770ce6966eb9f567", "RAM/USDG V2": "0x039e4cc74db4983c7c1d5bc9ab50fb8e43ffbd05"}


def addr_of(word):
    return "0x" + word.to_bytes(32, "big")[12:].hex()


def pin(r):
    block = r.head() - 5
    b = r.call("eth_getBlockByNumber", [hex(block), False])
    return {"l2Block": block, "hash": b["hash"], "native": int(b["l1BlockNumber"], 16), "timestamp": int(b["timestamp"], 16)}, block


class ClHop:
    def __init__(self, name, inputs):
        self.name, self.inputs = name, inputs
        self.state = QM.make_state(inputs)
        self.t0, self.t1 = inputs["token0"].lower(), inputs["token1"].lower()

    def sell(self, token_in, amount):
        zfo = token_in == self.t0
        st = copy.deepcopy(self.state)
        r = QM.swap(st, float(amount), zfo)
        return {"status": r["status"], "out": r["out"], "consumed": r["consumed"], "token_out": self.t1 if zfo else self.t0}

    def mid(self, token_in):
        r = self.state["s"] ** 2
        return r if token_in == self.t0 else 1.0 / r


class DlmmHop:
    def __init__(self, name, addr, r, block):
        self.name, self.addr, self.r, self.block = name, addr, r, block
        self.x = addr_of(words(PQ.eth_call(r, addr, "getTokenX()", block=block))[0]).lower()
        self.y = addr_of(words(PQ.eth_call(r, addr, "getTokenY()", block=block))[0]).lower()
        self.bin_step = words(PQ.eth_call(r, addr, "getBinStep()", block=block))[0]
        self.calls = []

    def sell(self, token_in, amount):
        sfy = token_in == self.x
        res = words(PQ.eth_call(self.r, self.addr, "getSwapOut(uint128,bool)", format(int(amount), "064x") + format(1 if sfy else 0, "064x"), block=self.block))
        left, out, fee = res[0], res[1], res[2]
        self.calls.append({"amountIn": int(amount), "swapForY": sfy, "amountInLeft": left, "amountOut": out, "fee": fee})
        return {"status": "filled" if left == 0 else "capacity_exhausted", "out": float(out), "consumed": float(int(amount) - left), "token_out": self.y if sfy else self.x}


class V2Hop:
    def __init__(self, name, addr, r, block):
        self.name, self.addr, self.r, self.block = name, addr, r, block
        self.t0 = addr_of(words(PQ.eth_call(r, addr, "token0()", block=block))[0]).lower()
        self.t1 = addr_of(words(PQ.eth_call(r, addr, "token1()", block=block))[0]).lower()
        res = words(PQ.eth_call(r, addr, "getReserves()", block=block))
        self.r0, self.r1 = res[0], res[1]
        self.fee = words(PQ.eth_call(r, addr, "fee()", block=block))[0] / 1e6
        self.on_chain = {}

    def sell(self, token_in, amount):
        a, b = (self.r0, self.r1) if token_in == self.t0 else (self.r1, self.r0)
        net = amount * (1 - self.fee)
        out = b * net / (a + net)
        try:
            on = words(PQ.eth_call(self.r, self.addr, "getAmountOut(uint256,address)", format(int(amount), "064x") + token_in[2:].rjust(64, "0"), block=self.block))[0]
            self.on_chain[str(int(amount))] = {"onChain": on, "formula": out}
        except Exception:
            pass
        return {"status": "filled", "out": out, "consumed": float(amount), "token_out": self.t1 if token_in == self.t0 else self.t0}


def route_eval(hops, token_in, amount):
    cur_tok, cur_amt, trail = token_in, float(amount), []
    for h in hops:
        r = h.sell(cur_tok, cur_amt)
        trail.append({"pool": h.name, "status": r["status"], "in": cur_amt, "consumed": r["consumed"], "out": r["out"]})
        if r["status"] != "filled":
            return {"status": r["status"] + "@" + h.name, "usdgOut": None, "trail": trail}
        cur_tok, cur_amt = r["token_out"], r["out"]
    return {"status": "filled", "usdgOut": cur_amt / 1e6, "trail": trail}


def main():
    r = rpc()
    out = {"schema": "ramses-r33-routes-1", "readOnly": True, "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "amountsR33": AMOUNTS_R33, "note": "each pool is read at its own recent block; multi-hop figures are NON-ATOMIC composites of independent pins; quotes only; shortfalls are relative to the r33/USDG CL200 SPOT (a thin pool), not to a defensible price", "pins": {}, "routes": []}
    hops = {}
    for name, (addr, spacing, mode) in CL.items():
        blk = r.head() - 5
        inp = PQ.read_inputs(addr, spacing, mode, blk)
        rel, sha = PQ.save_inputs(name, inp, subdir="routes")
        hops[name] = ClHop(name, inp)
        out["pins"][name] = dict(inp["pin"], kind="CL", inputsFile=rel, inputsSha256=sha, fee_ppm=inp["fee_ppm"], scan=inp["scan"]["mode"], ticks=len(inp["ticks"]), liquidity=inp["liquidity"])
        print("read", name, "block", blk, "fee", inp["fee_ppm"], "ticks", len(inp["ticks"]), flush=True)
    for name, addr in DLMM.items():
        p, blk = pin(r)
        hops[name] = DlmmHop(name, addr, r, blk)
        out["pins"][name] = dict(p, kind="DLMM", binStep=hops[name].bin_step, pool=addr)
        print("read", name, flush=True)
    for name, addr in V2.items():
        p, blk = pin(r)
        hops[name] = V2Hop(name, addr, r, blk)
        out["pins"][name] = dict(p, kind="legacy constant product", pool=addr, fee=hops[name].fee, reserves=[hops[name].r0, hops[name].r1])
        print("read", name, flush=True)
    ref_mid = hops["r33/USDG CL200"].mid(R33)
    out["referenceMidUsdgPerR33"] = ref_mid * 1e12
    direct = ["r33/USDG CL200", "r33/USDG CL500", "r33/USDG CL5", "r33/USDG DLMM200", "r33/USDG V2"]
    first = ["r33/RAM CL100", "r33/RAM CL200", "r33/RAM DLMM200", "r33/RAM DLMM25"]
    second = [["RAM/USDG DLMM100"], ["RAM/USDG V2"], ["WETH/RAM CL100", "WETH/USDG CL1"]]
    plans = [[d] for d in direct] + [[f] + s for f in first for s in second]
    for plan in plans:
        row = {"route": " -> ".join(plan), "hops": len(plan), "results": []}
        for n in AMOUNTS_R33:
            res = route_eval([hops[p] for p in plan], R33, int(n * 10 ** 18))
            nominal = n * ref_mid * 1e12
            res.update({"r33": n, "nominalUsdAtReferenceMid": nominal, "shortfallBpVsReferenceMid": None if res["usdgOut"] is None else (1 - res["usdgOut"] / nominal) * 1e4})
            row["results"].append(res)
        out["routes"].append(row)
    out["dlmmCalls"] = {n: h.calls for n, h in hops.items() if isinstance(h, DlmmHop)}
    out["v2OnChainCheck"] = {n: h.on_chain for n, h in hops.items() if isinstance(h, V2Hop)}
    (PQ.OUT.parent / "r33-routes.json").write_text(json.dumps(out, indent=1))
    print("%-62s" % "route (USDG received)", *["%12s" % f"{n:,} r33" for n in AMOUNTS_R33])
    for row in out["routes"]:
        print("%-62s" % row["route"], *["%12s" % (x["status"][:12] if x["usdgOut"] is None else f"${x['usdgOut']:,.1f}") for x in row["results"]])
    print("reference mid $%.5f per r33; nominal $:" % out["referenceMidUsdgPerR33"], [round(n * out["referenceMidUsdgPerR33"], 1) for n in AMOUNTS_R33])


if __name__ == "__main__":
    main()
