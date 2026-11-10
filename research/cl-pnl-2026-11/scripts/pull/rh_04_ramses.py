"""Step 4: Ramses NVDA/USDG and SPY/USDG pools -> ramses_<sym>_swaps.csv.gz, ramses_<sym>_liquidity.csv.gz.

Raw logs up to the existing files' ``head`` are read from /Users/joshua/Peridot/ramses-group-pool-<SYM>.json (read
only); the tail (head+1 .. END) is fetched with eth_getLogs. If an existing file is missing, everything is fetched.

Swap columns: block,log_index,ts,amount0,amount1,sqrt_price_x96,liquidity,tick,fee_ppm,protocol_share
  amounts: signed integers exactly as the v3-style event reports them (pool's side; positive = paid into the pool);
  fee_ppm: dynamic fee in force at (block, log_index) from FeeAdjustment(uint24 old, uint24 new) and SetFee(uint24,uint24);
           before the first such event the fee is that event's ``old`` value;
  protocol_share: SetFeeProtocol(uint24 old, uint24 new) / 1e6; before the first event its ``old`` value.
Liquidity columns: block,log_index,kind,tick_lower,tick_upper,liquidity_delta (burn negative).
"""
from __future__ import annotations

import json
import sys

from rh_lib import *  # noqa: F401,F403

KEYS = {"swap": "v3_swap", "mint": "v3_mint", "burn": "v3_burn", "init": "v3_init", "feeadj": "feeadj", "setfee": "setfee",
        "proto_a": "proto_a", "proto_b": "proto_b", "proto_c": "proto_c"}


def iter_raw_file(path):
    """Streams the existing raw file ({"head": n, "<key>": [log, ...], ...}) without loading it whole (it is up to 0.5 GB):
    yields (key, scalar) for scalar values and (key, log) for each list item."""
    dec = json.JSONDecoder()
    with open(path, "r") as f:
        buf, pos, eof = "", 0, False

        def fill():
            nonlocal buf, pos, eof
            if not eof and len(buf) - pos < (1 << 16):
                chunk = f.read(1 << 23)
                eof = not chunk
                buf = buf[pos:] + chunk
                pos = 0

        def skip():
            nonlocal pos
            fill()
            while pos < len(buf) and buf[pos] in " \t\r\n":
                pos += 1

        def value():
            nonlocal pos
            fill()
            v, pos = dec.raw_decode(buf, pos)
            return v

        skip()
        if buf[pos] != "{":
            raise EvidenceError("raw file is not a JSON object")
        pos += 1
        while True:
            skip()
            if buf[pos] == "}":
                return
            key = value()
            skip()
            pos += 1  # ':'
            skip()
            if buf[pos] == "[":
                pos += 1
                skip()
                while buf[pos] != "]":
                    yield key, value()
                    skip()
                    if buf[pos] == ",":
                        pos += 1
                        skip()
                pos += 1
            else:
                yield key, value()
            skip()
            if buf[pos] == ",":
                pos += 1


def compact(l: dict, pool: str, topic0: str):
    """(block, log_index, topics[1:], data, tx) after checking the emitter and the event."""
    if l["topics"][0] != topic0 or l["address"].lower() != pool.lower():
        raise EvidenceError("a log does not match the pool/topic")
    return (l["blockNumber"], l["logIndex"], tuple(l["topics"][1:]), l["data"], l["tx"])


def load_raw(sym: str, r, end: int) -> tuple[dict, dict]:
    cfg = RAMSES_POOLS[sym]
    src = EXISTING_RAW[cfg["raw"]]
    prov = {}
    out = {k: [] for k in KEYS}
    old_head = -1
    topic0 = {k: T(SIG[sig]) for k, sig in KEYS.items()}   # keccak is pure Python: compute once, not per log
    if src.exists():
        for key, v in iter_raw_file(src):
            if key == "head":
                old_head = v
            elif key in KEYS:
                out[key].append(compact(v, cfg["pool"], topic0[key]))
        if old_head < 0:
            raise EvidenceError("existing raw file has no head")
        prov["existing_file"] = str(src)
        prov["existing_file_head"] = old_head
        prov["existing_file_sha256"] = sha256_file(src)
    else:
        prov["existing_file"] = None
    for k, sig in KEYS.items():
        have = [x for x in out[k] if x[0] <= min(old_head, end)]
        tail = [compact(l, cfg["pool"], topic0[k]) for l in r.logs(cfg["pool"], [topic0[k]], old_head + 1, end, end)] if old_head < end else []
        rows = sorted(have + tail, key=lambda x: (x[0], x[1]))
        for x, y in zip(rows, rows[1:]):
            if (x[0], x[1]) == (y[0], y[1]):
                raise EvidenceError(f"{sym} {k}: duplicate log position {(x[0], x[1])}")
        out[k] = rows
        prov[k] = {"from_existing_file": len(have), "fetched_tail": len(tail), "topic0": topic0[k], "signature": SIG[sig]}
        say(sym, k, "existing", len(have), "tail", len(tail))
    prov["tail_range"] = [old_head + 1, end]
    return out, prov


def key2(x):
    return (x[0], x[1])


def fee_steps(raw: dict):
    evs = sorted(raw["feeadj"] + raw["setfee"], key=key2)
    if not evs:
        return None
    return Step(words(evs[0][3])[0], [(l[0], l[1], words(l[3])[1]) for l in evs])


def proto_steps(raw: dict):
    if raw["proto_a"] or raw["proto_c"]:
        raise EvidenceError("unexpected SetFeeProtocol variant present (uint8x4 or uint24): decode it before use")
    evs = sorted(raw["proto_b"], key=key2)
    if not evs:
        return None
    return Step(words(evs[0][3])[0], [(l[0], l[1], words(l[3])[1]) for l in evs])


def frac(ppm: int) -> str:
    return format(ppm / 1e6, "g")


def main():
    end = load_end()["END"]
    st = jload(END_STATE_FILE)
    r = rpc()
    ti = time_index(r)
    info_all = {}
    for sym in (sys.argv[1:] or ["nvda", "spy"]):
        raw, prov = load_raw(sym, r, end)
        if len(raw["init"]) != 1:
            raise EvidenceError(f"{sym}: expected one Initialize, got {len(raw['init'])}")
        iw = words(raw["init"][0][3])
        init = {"block": raw["init"][0][0], "log_index": raw["init"][0][1], "tx": raw["init"][0][4],
                "sqrtPriceX96": str(iw[0]), "tick": sN(iw[1], 24)}
        fee = fee_steps(raw)
        proto = proto_steps(raw)
        end_state = st["ramses"][sym]
        if fee is None:
            fee = Step(end_state["fee"], [])
        if proto is None:
            proto = Step(end_state["feeProtocol_raw"], [])

        def swap_rows():
            for l in raw["swap"]:
                w = words(l[3])
                if len(w) != 5:
                    raise EvidenceError("Swap data is not 5 words")
                b, i = l[0], l[1]
                yield (b, i, ts_fmt(ti.ts(b)), s256(w[0]), s256(w[1]), w[2], w[3], sN(w[4], 24), fee.at(b, i), frac(proto.at(b, i)))

        def liq_rows():
            ev = [("mint", l) for l in raw["mint"]] + [("burn", l) for l in raw["burn"]]
            ev.sort(key=lambda x: key2(x[1]))
            for kind, l in ev:
                w = words(l[3])
                lo, hi = topic_int(l[2][1], 24), topic_int(l[2][2], 24)
                if kind == "mint":
                    if len(w) != 5:
                        raise EvidenceError("Mint data is not 5 words (sender,index,amount,amount0,amount1)")
                    d = w[2]
                else:
                    if len(w) != 3:
                        raise EvidenceError("Burn data is not 3 words (amount,amount0,amount1)")
                    d = -w[0]
                yield (l[0], l[1], kind, lo, hi, d)

        sw = write_csv_gz(f"ramses_{sym}_swaps.csv.gz",
                          ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "protocol_share"], swap_rows())
        lq = write_csv_gz(f"ramses_{sym}_liquidity.csv.gz", ["block", "log_index", "kind", "tick_lower", "tick_upper", "liquidity_delta"], liq_rows())
        cfg = RAMSES_POOLS[sym]
        info = {
            "pool": cfg["pool"], "token0": cfg["token0"], "decimals0": cfg["dec0"], "token1": cfg["token1"], "decimals1": cfg["dec1"],
            "tick_spacing": end_state["tickSpacing"], "initialize": init, "provenance": prov,
            "swaps": sw, "liquidity": lq, "mints": len(raw["mint"]), "burns": len(raw["burn"]),
            "fee_events": {"FeeAdjustment": len(raw["feeadj"]), "SetFee": len(raw["setfee"]), "initial_fee_ppm": fee.initial,
                           "first_event_block": fee.keys[0][0] if fee.keys else None, "fee_ppm_in_force_at_END_from_events": fee.last,
                           "fee()_at_END": end_state["fee"], "match": fee.last == end_state["fee"],
                           "min_fee_ppm": min([fee.initial] + [c[2] for c in fee.changes]), "max_fee_ppm": max([fee.initial] + [c[2] for c in fee.changes])},
            "protocol_share_events": {"SetFeeProtocol(uint24,uint24)": len(raw["proto_b"]), "SetFeeProtocol(uint8,uint8,uint8,uint8)": len(raw["proto_a"]),
                                      "SetFeeProtocol(uint24)": len(raw["proto_c"]), "initial_ppm": proto.initial,
                                      "changes": [{"block": c[0], "log_index": c[1], "new_ppm": c[2]} for c in proto.changes],
                                      "in_force_at_END_from_events_ppm": proto.last, "slot0_feeProtocol_at_END": end_state["feeProtocol_raw"],
                                      "match": proto.last == end_state["feeProtocol_raw"]},
        }
        info_all[sym] = info
        jdump(STATE / f"ramses_{sym}_info.json", info)
        say(sym, "swaps", sw["rows"], "liq", lq["rows"], "fee match", info["fee_events"]["match"], "proto match", info["protocol_share_events"]["match"])


if __name__ == "__main__":
    main()
