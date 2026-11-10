"""Decode the cached logs into the CSV datasets (data/avalanche/*.csv.gz) and write data/avalanche/build_report.json.

usage: python3 avax_03_build.py [pool] [gauge] [feed] [phar_pool] [phar_cl_pool] [ledgers]     (default: all;
       `ledgers` alone only refreshes the side ledgers in the report)

Conventions
  * integers are plain decimal strings; rows are in (block, log_index) order;
  * swap amounts are signed from the pool's side exactly as the v3-style event reports them (positive = the pool received);
  * ts is the block header's own timestamp, read by eth_getBlockByNumber (never interpolated);
  * fee_ppm is the fee in force at that log: the newFee of the latest FeeAdjustment(uint24 oldFee, uint24 newFee) before
    it in (block, log_index) order, and before the first such event the fee() read by eth_call at START - 1;
  * liquidity_delta is +amount for Mint and -amount for Burn (zero-amount burns, which only poke fees, are kept).
Read-only: this script only reads the cache and block headers.
"""
from __future__ import annotations

import sys
from collections import Counter

from avax_lib import (CACHE, DATA_AVAX, RPC, TOPIC, TOPIC_NAME, block_timestamps, isqrt, ledger_rows, read_json,
                      read_json_gz, s256, tick_at_sqrt_ratio, topic_addr, words, write_csv_gz, write_json)

REPORT = DATA_AVAX / "build_report.json"
HDR_SRC = "publicnode"


def load(name: str, a: int, b: int) -> tuple[list[dict], dict]:
    """All cached logs of a dataset, with the structural checks: windows tile [a, b], every log lies inside the window
    that returned it, (block, log_index) strictly ascending (hence no duplicates)."""
    rows = ledger_rows(name)
    pos = a
    logs: list[dict] = []
    for r in rows:
        assert r["from"] == pos, f"{name}: gap/overlap at {pos}"
        part = read_json_gz(CACHE / "logs" / name / f"{r['from']}-{r['to']}.json.gz")
        assert len(part) == r["count"], f"{name}: cached count differs from the ledger in {r['from']}-{r['to']}"
        for x in part:
            assert r["from"] <= x["b"] <= r["to"], f"{name}: log outside its window"
        logs.extend(part)
        pos = r["to"] + 1
    assert pos == b + 1, f"{name}: windows end at {pos - 1}, expected {b}"
    keys = [(x["b"], x["i"]) for x in logs]
    ascending = all(k1 < k2 for k1, k2 in zip(keys, keys[1:]))
    if not ascending:
        logs.sort(key=lambda x: (x["b"], x["i"]))
        keys = [(x["b"], x["i"]) for x in logs]
    dup = len(keys) - len(set(keys))
    assert dup == 0, f"{name}: {dup} duplicate (block, log_index)"
    info = {"dataset": name, "requested_from": a, "requested_to": b, "windows": len(rows),
            "sources": dict(Counter(r["host"] for r in rows)), "logs": len(logs),
            "served_in_ascending_order": ascending, "duplicates": dup,
            "window_tiling_ok": True,
            "logs_per_topic0": {TOPIC_NAME.get(t, t): n for t, n in Counter(x["t"][0] for x in logs).most_common()},
            "ranges": [[r["from"], r["to"], r["count"], r["host"]] for r in rows]}
    return logs, info


def stamp(logs: list[dict], info: dict) -> dict[int, int]:
    """Header timestamps of the logs' blocks; also checks each log's blockHash against the header served by the header
    endpoint (a cross-endpoint consistency check when the logs came from another host)."""
    hd = block_timestamps({x["b"] for x in logs}, source=HDR_SRC)
    bad = sum(1 for x in logs if hd[x["b"]][1] != x["h"])
    info.setdefault("header_checks", []).append({"blocks": len(hd), "header_source": RPC[HDR_SRC].split("/")[2],
                                                  "logs_checked": len(logs), "block_hash_mismatches": bad})
    assert bad == 0, "log blockHash differs from the canonical header"
    return {b: v[0] for b, v in hd.items()}


def cl_pool(name: str, prefix_swaps: str, state_fee_before: int | None, a: int, b: int, liquidity_file: str | None,
            fee_file: str | None) -> dict:
    logs, info = load(name, a, b)
    swaps = [x for x in logs if x["t"][0] == TOPIC["Swap"]]
    fee_ev = [x for x in logs if x["t"][0] in (TOPIC["FeeAdjustment"], TOPIC["SetFeeProtocol"], TOPIC["SetFeeProtocolLegacy"])]
    ts = stamp(swaps + fee_ev, info)

    # fee series
    adj = [x for x in logs if x["t"][0] == TOPIC["FeeAdjustment"]]
    chain_breaks = 0
    prev = None
    for x in adj:
        o, n = words(x["d"])
        if prev is not None and o != prev:
            chain_breaks += 1
        prev = n
    first_old = words(adj[0]["d"])[0] if adj else None
    fee0 = state_fee_before if state_fee_before is not None else first_old
    info["fee_series"] = {"fee_adjustment_events": len(adj), "first_old_fee": first_old, "last_new_fee": prev,
                          "fee_before_first_log": fee0, "fee_before_first_log_source": "eth_call fee() at START-1" if state_fee_before is not None else "oldFee of the first FeeAdjustment",
                          "chain_breaks_old_ne_previous_new": chain_breaks,
                          "distinct_fees": sorted({words(x["d"])[1] for x in adj} | ({fee0} if fee0 is not None else set()))}

    def rows():
        fee = fee0
        for x in logs:
            t0 = x["t"][0]
            if t0 == TOPIC["FeeAdjustment"]:
                fee = words(x["d"])[1]
            elif t0 == TOPIC["Swap"]:
                w = words(x["d"])
                yield [x["b"], x["i"], ts[x["b"]], s256(w[0]), s256(w[1]), w[2], w[3], s256(w[4]), fee]

    n = write_csv_gz(DATA_AVAX / prefix_swaps, ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm"], rows())
    info["files"] = {prefix_swaps: {"rows": n, "first_block": swaps[0]["b"] if swaps else None, "last_block": swaps[-1]["b"] if swaps else None,
                                    "first_ts": ts[swaps[0]["b"]] if swaps else None, "last_ts": ts[swaps[-1]["b"]] if swaps else None}}
    if swaps:
        w = words(swaps[-1]["d"])
        info["last_swap"] = {"block": swaps[-1]["b"], "log_index": swaps[-1]["i"], "sqrt_price_x96": str(w[2]), "liquidity": str(w[3]), "tick": s256(w[4])}

    if liquidity_file:
        def lrows():
            for x in logs:
                t0 = x["t"][0]
                if t0 == TOPIC["Mint"]:      # data: sender, index, amount, amount0, amount1
                    w = words(x["d"])
                    yield [x["b"], x["i"], "mint", s256(int(x["t"][2], 16)), s256(int(x["t"][3], 16)), w[2]]
                elif t0 == TOPIC["Burn"]:    # data: amount, amount0, amount1
                    w = words(x["d"])
                    yield [x["b"], x["i"], "burn", s256(int(x["t"][2], 16)), s256(int(x["t"][3], 16)), -w[0]]
        lb = [x for x in logs if x["t"][0] in (TOPIC["Mint"], TOPIC["Burn"])]
        n = write_csv_gz(DATA_AVAX / liquidity_file, ["block", "log_index", "kind", "tick_lower", "tick_upper", "liquidity_delta"], lrows())
        net = sum(words(x["d"])[2] if x["t"][0] == TOPIC["Mint"] else -words(x["d"])[0] for x in lb)
        info["files"][liquidity_file] = {"rows": n, "first_block": lb[0]["b"] if lb else None, "last_block": lb[-1]["b"] if lb else None,
                                         "mints": sum(1 for x in lb if x["t"][0] == TOPIC["Mint"]),
                                         "burns": sum(1 for x in lb if x["t"][0] == TOPIC["Burn"]),
                                         "zero_amount_burns": sum(1 for x in lb if x["t"][0] == TOPIC["Burn"] and words(x["d"])[0] == 0),
                                         "net_liquidity_delta_all_ranges": str(net)}
    if fee_file and fee_ev:
        def frows():
            for x in fee_ev:
                w = words(x["d"])
                if x["t"][0] == TOPIC["FeeAdjustment"]:
                    yield [x["b"], x["i"], ts[x["b"]], "fee", w[0], w[1]]
                elif x["t"][0] == TOPIC["SetFeeProtocol"]:
                    yield [x["b"], x["i"], ts[x["b"]], "fee_protocol", w[0], w[1]]
                else:
                    yield [x["b"], x["i"], ts[x["b"]], "fee_protocol_legacy_uint8x4", "/".join(map(str, w[:2])), "/".join(map(str, w[2:4]))]
        n = write_csv_gz(DATA_AVAX / fee_file, ["block", "log_index", "ts", "kind", "old_value", "new_value"], frows())
        info["files"][fee_file] = {"rows": n, "first_block": fee_ev[0]["b"], "last_block": fee_ev[-1]["b"],
                                   "first_ts": ts[fee_ev[0]["b"]], "last_ts": ts[fee_ev[-1]["b"]],
                                   "kinds": dict(Counter("fee" if x["t"][0] == TOPIC["FeeAdjustment"] else "fee_protocol" for x in fee_ev))}
    return info


def gauge(a: int, b: int, st: dict) -> dict:
    logs, info = load("gauge", a, b)
    ev = [x for x in logs if x["t"][0] in (TOPIC["NotifyReward"], TOPIC["Bribe"])]
    ts = stamp(ev, info)
    sym = {t: e["symbol"] for t, e in st["decoded"]["gauge"]["reward_tokens"].items()}

    def rows():
        for x in ev:
            w = words(x["d"])            # amount, period
            tok = topic_addr(x["t"][2])
            yield [x["b"], x["i"], ts[x["b"]], topic_addr(x["t"][1]), tok, w[0], w[1],
                   "NotifyReward" if x["t"][0] == TOPIC["NotifyReward"] else "Bribe", sym.get(tok, "")]
    n = write_csv_gz(DATA_AVAX / "pharaoh_gauge_notify.csv.gz", ["block", "log_index", "ts", "from", "token", "amount", "period", "kind", "token_symbol"], rows())
    per = Counter()
    amt = Counter()
    for x in ev:
        k = ("NotifyReward" if x["t"][0] == TOPIC["NotifyReward"] else "Bribe", sym.get(topic_addr(x["t"][2]), topic_addr(x["t"][2])))
        per[k] += 1
        amt[k] += words(x["d"])[0]
    info["files"] = {"pharaoh_gauge_notify.csv.gz": {
        "rows": n, "first_block": ev[0]["b"] if ev else None, "last_block": ev[-1]["b"] if ev else None,
        "first_ts": ts[ev[0]["b"]] if ev else None, "last_ts": ts[ev[-1]["b"]] if ev else None,
        "events_by_kind_and_token": {f"{k[0]}:{k[1]}": per[k] for k in per},
        "raw_amount_by_kind_and_token": {f"{k[0]}:{k[1]}": str(amt[k]) for k in amt},
        "senders": dict(Counter(topic_addr(x["t"][1]) for x in ev))}}
    # what the gauge actually paid out in the window: ClaimRewards(uint256 period, bytes32 positionHash, address receiver, address reward, uint256 amount)
    cc, ca = Counter(), Counter()
    for x in logs:
        if x["t"][0] == TOPIC["ClaimRewards"]:
            w = words(x["d"])
            tok = "0x" + hex(w[3])[2:].rjust(40, "0")
            cc[sym.get(tok, tok)] += 1
            ca[sym.get(tok, tok)] += w[4]
    info["claims_in_window_by_reward_token"] = {k: {"events": cc[k], "raw_amount": str(ca[k])} for k in cc}
    return info


def feed(a: int, b: int) -> dict:
    logs, info = load("feed", a, b)
    ev = [x for x in logs if x["t"][0] == TOPIC["AnswerUpdated"]]

    def rows():      # AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt)
        for x in ev:
            yield [x["b"], x["i"], words(x["d"])[0], s256(int(x["t"][1], 16)), int(x["t"][2], 16)]
    n = write_csv_gz(DATA_AVAX / "feed_avax_usd.csv.gz", ["block", "log_index", "updated_at", "answer", "round_id"], rows())
    rid = [int(x["t"][2], 16) for x in ev]
    info["files"] = {"feed_avax_usd.csv.gz": {
        "rows": n, "first_block": ev[0]["b"] if ev else None, "last_block": ev[-1]["b"] if ev else None,
        "first_ts": words(ev[0]["d"])[0] if ev else None, "last_ts": words(ev[-1]["d"])[0] if ev else None,
        "first_round_id": rid[0] if rid else None, "last_round_id": rid[-1] if rid else None,
        "round_ids_consecutive": all(q == p + 1 for p, q in zip(rid, rid[1:])),
        "round_id_gaps": sum(1 for p, q in zip(rid, rid[1:]) if q != p + 1)}}
    return info


def legacy_pair(a: int, b: int, pools: dict, st: dict) -> dict:
    """Solidly-style pair. The v3 columns do not exist for it; sqrt_price_x96, liquidity and tick are DERIVED from the
    reserves of the Sync event that the pair emits just before each Swap (same transaction):
        sqrt_price_x96 = floor(sqrt(reserve1 * 2^192 / reserve0)),  liquidity = floor(sqrt(reserve0 * reserve1)),
        tick = TickMath.getTickAtSqrtRatio(sqrt_price_x96).   reserve0/reserve1 are the event's own values."""
    addr = pools["chosen"]["address"]
    cand = pools["candidates"][addr]
    logs, info = load("phar_pool", a, b)
    swaps_idx = [k for k, x in enumerate(logs) if x["t"][0] == TOPIC["SwapV2"]]
    ts = stamp([logs[k] for k in swaps_idx], info)
    # fee: SetPairFee(address indexed pair, uint256 indexed fee) on the legacy factory
    SET_PAIR_FEE = TOPIC["SetPairFee"]
    fl, finfo = load("legacy_factory", a, b)
    fev = [(x["b"], x["i"], int(x["t"][2], 16)) for x in fl if x["t"][0] == SET_PAIR_FEE and topic_addr(x["t"][1]) == addr.lower()]
    fee_start, fee_end = cand.get("pair.fee()_at_start"), cand.get("pair.fee()")
    info["fee_series"] = {"source": "SetPairFee(pair, fee) events of the legacy factory " + st["decoded"]["gauge"]["voter.legacyFactory()"],
                          "factory_logs_in_window": finfo["logs"], "factory_logs_per_topic0": finfo["logs_per_topic0"],
                          "set_pair_fee_events_for_this_pair": [[b_, i_, f_] for b_, i_, f_ in fev],
                          "pair.fee()_at_START": fee_start, "pair.fee()_at_END": fee_end, "fee_denominator": cand.get("factory.FEE_DENOM()"),
                          "last_event_equals_fee_at_END": (fev[-1][2] == fee_end) if fev else (fee_start == fee_end)}
    unmatched = 0
    out = []
    for k in swaps_idx:
        x = logs[k]
        sy = logs[k - 1] if k > 0 else None
        if not sy or sy["t"][0] != TOPIC["Sync"] or sy["tx"] != x["tx"]:
            unmatched += 1
            continue
        r0, r1 = words(sy["d"])
        w = words(x["d"])            # amount0In, amount1In, amount0Out, amount1Out
        fee = fee_start
        for b_, i_, f_ in fev:
            if (b_, i_) < (x["b"], x["i"]):
                fee = f_
        sq = isqrt((r1 << 192) // r0)
        out.append([x["b"], x["i"], ts[x["b"]], w[0] - w[2], w[1] - w[3], sq, isqrt(r0 * r1), tick_at_sqrt_ratio(sq), fee, r0, r1])
    n = write_csv_gz(DATA_AVAX / "phar_pool_swaps.csv.gz",
                     ["block", "log_index", "ts", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm", "reserve0", "reserve1"], out)
    info["files"] = {"phar_pool_swaps.csv.gz": {
        "rows": n, "first_block": out[0][0] if out else None, "last_block": out[-1][0] if out else None,
        "first_ts": out[0][2] if out else None, "last_ts": out[-1][2] if out else None,
        "swaps_without_a_preceding_sync_in_the_same_tx_dropped": unmatched,
        "last_row_reserves": [str(out[-1][9]), str(out[-1][10])] if out else None}}
    return info


def main() -> None:
    want = set(sys.argv[1:]) or {"pool", "gauge", "feed", "phar_pool", "phar_cl_pool"}
    end = read_json(DATA_AVAX / "END.json")
    st = read_json(DATA_AVAX / "state_at_end.json")
    a, b = end["start"]["number"], end["end"]["number"]
    rep = read_json(REPORT) if REPORT.exists() else {}
    if "pool" in want:
        rep["pool"] = cl_pool("pool", "pharaoh_wavax_usdc_swaps.csv.gz", st["decoded"]["pool"]["at_start"]["fee_ppm_at_start_minus_1"], a, b,
                              "pharaoh_wavax_usdc_liquidity.csv.gz", "pharaoh_wavax_usdc_fee_events.csv.gz")
        write_json(REPORT, rep)
    if "gauge" in want:
        rep["gauge"] = gauge(a, b, st)
        write_json(REPORT, rep)
    if "feed" in want:
        rep["feed"] = feed(a, b)
        write_json(REPORT, rep)
    pools = read_json(DATA_AVAX / "phar_pools.json")
    if "phar_pool" in want:
        rep["phar_pool"] = legacy_pair(a, b, pools, st)
        write_json(REPORT, rep)
    if "phar_cl_pool" in want:
        rep["phar_cl_pool"] = cl_pool("phar_cl_pool", "phar_wavax_cl_swaps.csv.gz", None, a, b, None, None)
        write_json(REPORT, rep)
    # ledgers of the pulls that have no dataset of their own (second pass of the pool, legacy factory events)
    for side in ("pool_second", "legacy_factory"):
        try:
            rows = ledger_rows(side)
        except FileNotFoundError:
            continue
        rep.setdefault("side_ledgers", {})[side] = {
            "requested_from": rows[0]["from"], "requested_to": rows[-1]["to"], "windows": len(rows),
            "sources": dict(Counter(r["host"] for r in rows)), "logs": sum(r["count"] for r in rows),
            "window_tiling_ok": rows[0]["from"] == a and rows[-1]["to"] == b and all(x["to"] + 1 == y["from"] for x, y in zip(rows, rows[1:])),
            "ranges": [[r["from"], r["to"], r["count"], r["host"]] for r in rows]}
    for ds, flt in (("feed", "topic0 = AnswerUpdated"), ("phar_pool", "topic0 in {Swap (v2 style), Sync}")):
        if ds in rep:
            rep[ds]["topic_filter"] = flt
    write_json(REPORT, rep)
    for k in sorted(want & set(rep)):
        v = rep[k]
        print(k, v["logs"], "logs", v["logs_per_topic0"], {f: x["rows"] for f, x in v["files"].items()}, v.get("fee_series", ""))


if __name__ == "__main__":
    main()
