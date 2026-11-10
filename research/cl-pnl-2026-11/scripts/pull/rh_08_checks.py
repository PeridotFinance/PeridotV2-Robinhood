"""Step 8: checks on the written datasets -> data/robinhood/state/checks.json.

Per swap dataset (the files are streamed; some have millions of rows):
  order      strictly ascending by (block, log_index), which also means no duplicates (read back from the .csv.gz).
  refetch    5 seeded random 1,000,000-block windows (plus, for the Ramses pools, one window across the seam between the
             existing raw file and the fetched tail) re-fetched with an UNCACHED client and an unaligned 173,331-block
             split; the re-fetched logs are decoded and compared with the CSV rows of that window: row count and the
             sha256 of the lines "block,log_index,amount0,amount1,sqrt_price_x96,liquidity,tick".
  end_state  last swap's sqrt_price_x96 / tick / liquidity against slot0()/liquidity() (StateView for v4) read at END;
             liquidity also after adding in-range liquidity events that came after the last swap.
  liquidity  (where a liquidity file exists) active liquidity rebuilt from Mint/Burn or ModifyLiquidity deltas of the
             ranges containing the post-swap tick, compared with the Swap event's liquidity at evenly spaced swaps.
  v3_math    for consecutive swaps with equal event liquidity: the output leg against L and the two sqrt prices, and
             the input leg against the fee_ppm column (gross input = net input * 1e6 / (1e6 - fee)).
  ts_error   20 seeded random swap blocks: interpolated ts against the real header timestamp.
Feeds / gauges: the whole range re-fetched in 7,777,777-block chunks when the file has fewer than 9,000 rows, else 3
             random 2,000,000-block windows in 173,331-block chunks (count and (block, log_index) keys).
Nothing is asserted here: results, including failures, are recorded.

Usage: rh_08_checks.py [--offline] [--out=<name>.json] [file names ...]
  --offline skips the re-fetch and header reads; --out writes to data/robinhood/state/<name>.json (default checks.json).
"""
from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import random
import sys
from array import array

from rh_lib import *  # noqa: F401,F403
from evidence_lib import SplitWindow

Q96 = 1 << 96
SEED = 20261110
CHUNK = 173_331


def fresh_logs(r, address, topics, lo, hi, chunk=CHUNK):
    """Uncached fetch with an unaligned split; a refused window is halved."""
    out, cur, step, calls = [], lo, chunk, 0
    while cur <= hi:
        top = min(cur + step - 1, hi)
        try:
            got = r._logs_raw(address, topics, cur, top)
            calls += 1
        except SplitWindow:
            if step == 1:
                raise
            step = max(step // 2, 1)
            continue
        out += got
        cur = top + 1
    out.sort(key=log_key)
    return out, calls


def dec_v3(l):
    w = words(l["data"])
    return (l["blockNumber"], l["logIndex"], s256(w[0]), s256(w[1]), w[2], w[3], sN(w[4], 24))


def dec_v4(l):
    w = words(l["data"])
    return (l["blockNumber"], l["logIndex"], -sN(w[0], 128), -sN(w[1], 128), w[2], w[3], sN(w[4], 24))


def stream_swaps(name):
    """Yields (block, log_index, amount0, amount1, sqrt, liquidity, tick, fee_ppm, ts) per row."""
    with gzip.open(DATA / name, "rt", encoding="ascii", newline="") as f:
        rd = csv.reader(f)
        h = next(rd)
        ix = [h.index(k) for k in ("block", "log_index", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick", "fee_ppm")]
        its = h.index("ts")
        for x in rd:
            yield (int(x[ix[0]]), int(x[ix[1]]), int(x[ix[2]]), int(x[ix[3]]), int(x[ix[4]]), int(x[ix[5]]), int(x[ix[6]]), int(x[ix[7]]), float(x[its]))


def line7(t):
    return "%d,%d,%d,%d,%d,%d,%d\n" % t[:7]


class V3Math:
    """Consecutive swaps with equal event liquidity. Pool-side amounts: positive = into the pool."""

    def __init__(self, liq_keys=None):
        self.liq_keys = liq_keys   # sorted (block, log_index) of the liquidity events, when a liquidity file exists
        self.s = {"pairs_total": 0, "eligible_equal_liquidity": 0, "price_unchanged": 0,
                  "output_leg": {"within_2": 0, "within_10": 0, "over_10": 0, "max_abs": 0},
                  "input_leg_fee": {"within_2": 0, "within_10": 0, "over_10": 0, "max_abs": 0, "over_10_rel_gt_1e-6": 0},
                  "sign_mismatch": 0, "examples_output_over_10": [], "examples_input_over_10": []}
        self.exc = []   # every pair whose output or input leg is off by more than 10 units (capped at 2,000)

    def add(self, p, c):
        st = self.s
        st["pairs_total"] += 1
        S0, L0 = p[4], p[5]
        b, li, a0, a1, S1, L1, tick, fee = c[:8]
        if L0 != L1 or L1 == 0:
            return
        st["eligible_equal_liquidity"] += 1
        if S1 == S0:
            st["price_unchanged"] += 1
            return
        if S1 > S0:   # token1 in, token0 out
            net_in = -(-(L1 * (S1 - S0)) // Q96)
            out = (L1 * Q96 * (S1 - S0)) // (S1 * S0)
            gross, got_out = a1, -a0
        else:         # token0 in, token1 out
            net_in = -(-(L1 * Q96 * (S0 - S1)) // (S1 * S0))
            out = (L1 * (S0 - S1)) // Q96
            gross, got_out = a0, -a1
        if gross < 0 or got_out < 0:
            st["sign_mismatch"] += 1
            return
        d_out = abs(got_out - out)
        o = st["output_leg"]
        o["max_abs"] = max(o["max_abs"], d_out)
        o["within_2" if d_out <= 2 else "within_10" if d_out <= 10 else "over_10"] += 1
        if d_out > 10:
            between = None
            if self.liq_keys is not None:
                between = bisect.bisect_left(self.liq_keys, (b, li)) - bisect.bisect_right(self.liq_keys, (p[0], p[1]))
                k = "output_over_10_with_liquidity_event_between" if between else "output_over_10_without_liquidity_event_between"
                st[k] = st.get(k, 0) + 1
            if len(st["examples_output_over_10"]) < 5:
                st["examples_output_over_10"].append({"block": b, "log_index": li, "previous_swap": [p[0], p[1]], "tick_before": p[6], "tick_after": tick,
                                                      "event_out": str(got_out), "from_prices": str(out), "liquidity_events_between": between})
        exp_gross = -(-(net_in * 1_000_000) // (1_000_000 - fee))
        d_in = abs(gross - exp_gross)
        s = st["input_leg_fee"]
        s["max_abs"] = max(s["max_abs"], d_in)
        s["within_2" if d_in <= 2 else "within_10" if d_in <= 10 else "over_10"] += 1
        if (d_in > 10 or d_out > 10) and len(self.exc) < 2000:
            self.exc.append({"block": b, "log_index": li, "previous_swap": [p[0], p[1]], "tick_before": p[6], "tick_after": tick,
                             "event_out": str(got_out), "out_from_prices": str(out), "event_in": str(gross), "expected_in": str(exp_gross),
                             "fee_ppm_column": fee, "implied_fee_ppm": round(1e6 * (1 - net_in / gross), 3) if gross else None})
        if d_in > 10:
            if gross and d_in / gross > 1e-6:
                s["over_10_rel_gt_1e-6"] += 1
            if len(st["examples_input_over_10"]) < 5:
                st["examples_input_over_10"].append({"block": b, "log_index": li, "event_in": str(gross), "expected_in": str(exp_gross),
                                                     "fee_ppm_column": fee, "implied_fee_ppm": 1e6 * (1 - net_in / gross) if gross else None})

    def result(self):
        for k in ("output_leg", "input_leg_fee"):
            self.s[k]["max_abs"] = str(self.s[k]["max_abs"])
        self.s["tolerance_note"] = "differences in raw token units (wei); within_2 = at most 2 units"
        return self.s


def classify_exceptions(exc, liq):
    """For each exception: liquidity events between the two swaps, and how many initialized ticks (boundaries of ranges
    that held liquidity just before the swap) lie in (min(tick_before, tick_after), max(...)], i.e. were crossed by it."""
    pos, j, out = {}, 0, []
    for e in exc:
        key = (e["block"], e["log_index"])
        pk = tuple(e["previous_swap"])
        while j < len(liq) and (liq[j][0], liq[j][1]) < key:
            k = (liq[j][2], liq[j][3])
            pos[k] = pos.get(k, 0) + liq[j][4]
            j += 1
        between = sum(1 for x in liq[max(j - 50, 0):j] if pk < (x[0], x[1]) < key)
        lo, hi = min(e["tick_before"], e["tick_after"]), max(e["tick_before"], e["tick_after"])
        ticks = set()
        for (a, b), v in pos.items():
            if v > 0:
                for t in (a, b):
                    if lo < t <= hi:
                        ticks.add(t)
        out.append(dict(e, liquidity_events_between=between, initialized_ticks_crossed=len(ticks)))
    return {"exceptions": len(out), "crossed_at_least_one_initialized_tick": sum(1 for x in out if x["initialized_ticks_crossed"] > 0),
            "with_liquidity_event_between": sum(1 for x in out if x["liquidity_events_between"] > 0),
            "neither": sum(1 for x in out if not x["initialized_ticks_crossed"] and not x["liquidity_events_between"]),
            "detail": out}


def pass1(name, liq_keys=None):
    """One streaming pass: order, v3 math, the (block, log_index) columns, first/last row."""
    blocks, idxs = array("q"), array("q")
    vm = V3Math(liq_keys)
    prev = None
    dup = unsorted = 0
    first = None
    for row in stream_swaps(name):
        if prev is not None:
            ka, kb = (prev[0], prev[1]), (row[0], row[1])
            if kb == ka:
                dup += 1
            elif kb < ka:
                unsorted += 1
            vm.add(prev, row)
        else:
            first = row
        blocks.append(row[0])
        idxs.append(row[1])
        prev = row
    order = {"rows": len(blocks), "duplicates_adjacent": dup, "descending_steps": unsorted, "pass": dup == 0 and unsorted == 0}
    res = vm.result()
    res["_exc"] = vm.exc
    return blocks, idxs, first, prev, order, res


def pass2(name, want_idx, windows):
    """Second pass: the rows at ``want_idx`` and, per block window, the row count and sha256 of the 7-field lines."""
    want = set(want_idx)
    got = {}
    hs = [[hashlib.sha256(), 0] for _ in windows]
    for i, row in enumerate(stream_swaps(name)):
        if i in want:
            got[i] = row
        b = row[0]
        for j, (a, z, _) in enumerate(windows):
            if a <= b <= z:
                hs[j][0].update(line7(row).encode())
                hs[j][1] += 1
    return got, [(h.hexdigest(), n) for h, n in hs]


def liquidity_rebuild(sample_rows, liq):
    """sample_rows: [(block, log_index, .., liquidity, tick, ..)] ascending; liq: [(block, idx, lower, upper, delta)] ascending."""
    pos = {}
    j, bad, worst, examples = 0, 0, 0, []
    for row in sample_rows:
        b, li, L, tick = row[0], row[1], row[5], row[6]
        while j < len(liq) and (liq[j][0], liq[j][1]) < (b, li):
            k = (liq[j][2], liq[j][3])
            pos[k] = pos.get(k, 0) + liq[j][4]
            j += 1
        act = sum(v for (lo, hi), v in pos.items() if lo <= tick < hi)
        if act != L:
            bad += 1
            worst = max(worst, abs(act - L))
            if len(examples) < 5:
                examples.append({"block": b, "log_index": li, "tick": tick, "event_liquidity": str(L), "rebuilt": str(act)})
    while j < len(liq):
        k = (liq[j][2], liq[j][3])
        pos[k] = pos.get(k, 0) + liq[j][4]
        j += 1
    return {"samples": len(sample_rows), "mismatches": bad, "max_abs_difference": str(worst), "examples": examples, "pass": bad == 0,
            "ranges_with_negative_net_liquidity_at_END": sum(1 for v in pos.values() if v < 0),
            "ranges_open_at_END": sum(1 for v in pos.values() if v > 0)}


def end_state(last, state, liq_after_last):
    if last is None:
        return {"pass": None, "note": "no swaps"}
    b, li, _, _, sq, L, tick = last[:7]
    adj = L + sum(d for (_, _, lo, hi, d) in liq_after_last if lo <= tick < hi)
    out = {"last_swap": {"block": b, "log_index": li, "sqrt_price_x96": str(sq), "tick": tick, "liquidity": str(L)},
           "state_at_END": {"sqrtPriceX96": state["sqrtPriceX96"], "tick": state["tick"], "liquidity": state["liquidity"]},
           "sqrt_price_match": str(sq) == state["sqrtPriceX96"], "tick_match": tick == state["tick"],
           "liquidity_match_raw": str(L) == state["liquidity"],
           "liquidity_events_after_last_swap": len(liq_after_last),
           "liquidity_after_in_range_events": str(adj), "liquidity_match_adjusted": str(adj) == state["liquidity"]}
    out["pass"] = out["sqrt_price_match"] and out["tick_match"] and out["liquidity_match_adjusted"]
    return out


def load_liq(name):
    header, rows = read_csv_gz(name)
    ix = {h: i for i, h in enumerate(header)}
    return [(int(x[ix["block"]]), int(x[ix["log_index"]]), int(x[ix["tick_lower"]]), int(x[ix["tick_upper"]]), int(x[ix["liquidity_delta"]])) for x in rows]


def main():
    end = load_end()["END"]
    st = jload(END_STATE_FILE)
    offline = "--offline" in sys.argv
    only = set(a for a in sys.argv[1:] if not a.startswith("--"))
    r = rpc(cached=False)
    ti = time_index(r)
    v4info = jload(STATE / "v4_info.json") if (STATE / "v4_info.json").exists() else {}
    rinfo = {s: jload(STATE / f"ramses_{s}_info.json") for s in ("nvda", "spy") if (STATE / f"ramses_{s}_info.json").exists()}
    rew = jload(STATE / "rewards_info.json") if (STATE / "rewards_info.json").exists() else {}
    u3 = jload(STATE / "univ3_info.json") if (STATE / "univ3_info.json").exists() else {"files": {}}
    out_name = ([a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--out=")] or ["checks.json"])[0]
    checks_path = STATE / out_name
    checks = jload(checks_path) if checks_path.exists() else {}
    checks["seed"] = SEED

    sets = []
    for s, info in rinfo.items():
        seam = info["provenance"].get("existing_file_head")
        extra = [(seam - 500_000, min(seam + 499_999, end), "seam between the existing raw file and the fetched tail")] if seam else []
        sets.append({"name": f"ramses_{s}_swaps.csv.gz", "address": RAMSES_POOLS[s]["pool"], "topics": [T(SIG["v3_swap"])], "decode": dec_v3,
                     "liq": f"ramses_{s}_liquidity.csv.gz", "state": st["ramses"][s], "extra": extra, "mint_sig": "v3_mint", "mint_word": 2})
    for s, v in v4info.items():
        sets.append({"name": f"v4_{s}_swaps.csv.gz", "address": V4_POOL_MANAGER, "topics": [T(SIG["v4_swap"]), v["pool"]["pool_id"]], "decode": dec_v4,
                     "liq": f"v4_{s}_liquidity.csv.gz", "state": st["v4"][v["pool"]["pool_id"]], "extra": []})
    for fname, src in u3["files"].items():
        if fname.endswith("_swaps.csv.gz"):
            sets.append({"name": fname, "address": src["contract"], "topics": [T(SIG["v3_swap"])], "decode": dec_v3,
                         "liq": fname.replace("_swaps.csv.gz", "_liquidity.csv.gz"), "state": st["univ3"][src["contract"]], "extra": []})
    if "ram_pool" in rew:
        sets.append({"name": "ram_pool_swaps.csv.gz", "address": RAM_POOL, "topics": [T(SIG["v3_swap"])], "decode": dec_v3, "liq": None,
                     "state": st["ramses"]["ram_pool"], "extra": [], "mint_sig": "v3_mint", "mint_word": 2})

    for n_set, ds in enumerate(sets):
        name = ds["name"]
        if only and name not in only:
            continue
        rnd = random.Random(SEED + sum(name.encode()))
        say(name)
        liq = load_liq(ds["liq"]) if ds["liq"] else None
        blocks, idxs, first, last, order, vm = pass1(name, [(x[0], x[1]) for x in liq] if liq is not None else None)
        exc = vm.pop("_exc")
        if liq is not None:
            vm["exceptions_classified"] = classify_exceptions(exc, liq)
        else:
            vm["exceptions_unclassified_no_liquidity_file"] = exc[:50]
        c = {"order": order, "v3_math": vm}
        say("  order", order)
        say("  v3 math", {k: v for k, v in vm.items() if not k.startswith("examples") and not k.startswith("exceptions")})
        if "exceptions_classified" in vm:
            say("  exceptions", {k: v for k, v in vm["exceptions_classified"].items() if k != "detail"})
        n = len(blocks)
        # windows for the re-fetch
        windows = []
        if n:
            lo, width = blocks[0], 1_000_000
            for _ in range(5):
                a = rnd.randrange(lo, max(end - width + 1, lo) + 1)
                windows.append((a, min(a + width - 1, end), "random"))
            windows += ds["extra"]
        # sample rows for the liquidity rebuild, and blocks for the timestamp check
        samples = 400
        want = sorted(set([int(i * (n - 1) / (samples - 1)) for i in range(samples)] + [n - 1])) if n else []
        ts_idx = [rnd.randrange(n) for _ in range(20)] if n else []
        got, digests = pass2(name, want, windows) if n else ({}, [])

        if ds["liq"]:
            lk = [(x[0], x[1]) for x in liq]
            c["liquidity_file_order"] = {"rows": len(lk), "pass": all(b > a for a, b in zip(lk, lk[1:]))}
            c["liquidity_rebuild"] = liquidity_rebuild([got[i] for i in want], liq)
            say("  liquidity rebuild", {k: v for k, v in c["liquidity_rebuild"].items() if k != "examples"})
            last_key = (last[0], last[1]) if last else (0, 0)
            after = [x for x in liq if (x[0], x[1]) > last_key]
            c["end_state"] = end_state(last, ds["state"], after)
        elif not offline and last:
            # no liquidity file for this pool: Mint/Burn after the last swap only (for the END comparison)
            after = []
            for l in r._logs_raw(ds["address"], [T(SIG[ds["mint_sig"]])], last[0], end):
                if log_key(l) > (last[0], last[1]):
                    after.append((l["blockNumber"], l["logIndex"], topic_int(l["topics"][2], 24), topic_int(l["topics"][3], 24), words(l["data"])[ds["mint_word"]]))
            for l in r._logs_raw(ds["address"], [T(SIG["v3_burn"])], last[0], end):
                if log_key(l) > (last[0], last[1]):
                    after.append((l["blockNumber"], l["logIndex"], topic_int(l["topics"][2], 24), topic_int(l["topics"][3], 24), -words(l["data"])[0]))
            c["end_state"] = end_state(last, ds["state"], after)
        if "end_state" in c:
            say("  end state", {k: v for k, v in c["end_state"].items() if k.endswith("match") or k.endswith("adjusted") or k == "pass"})

        if not offline and n:
            res = []
            for (a, b, label), (dig, cnt) in zip(windows, digests):
                logs, calls = fresh_logs(r, ds["address"], ds["topics"], a, b)
                h = hashlib.sha256()
                for l in logs:
                    h.update(line7(ds["decode"](l)).encode())
                res.append({"from": a, "to": b, "kind": label, "refetched": len(logs), "dataset": cnt, "count_match": len(logs) == cnt,
                            "values_match": h.hexdigest() == dig, "rpc_calls": calls})
                say("   refetch", a, b, label, len(logs), cnt, h.hexdigest() == dig)
            c["refetch"] = {"windows": res, "pass": all(x["count_match"] and x["values_match"] for x in res),
                            "split": f"{CHUNK}-block unaligned chunks (halved when the node refuses), uncached client",
                            "compared": "row count and sha256 of block,log_index,amount0,amount1,sqrt_price_x96,liquidity,tick lines"}
            errs = []
            for i in ts_idx:
                b = blocks[i]
                real = int(r.block(b)["timestamp"], 16)
                errs.append({"block": b, "interpolated": round(ti.ts(b), 1), "header": real, "abs_error_s": round(abs(ti.ts(b) - real), 3)})
            e = sorted(x["abs_error_s"] for x in errs)
            c["ts_error"] = {"samples": len(errs), "max_abs_error_s": e[-1], "median_abs_error_s": e[len(e) // 2], "detail": errs}
            say("  ts error max", e[-1], "median", e[len(e) // 2])
        elif name in checks:
            for k in ("refetch", "ts_error", "end_state"):
                if k in checks[name] and k not in c:
                    c[k] = checks[name][k]
        checks[name] = c
        jdump(checks_path, checks)

    # ---- feeds and gauges: order, and count/key re-fetch ----------------------------------------------------------
    if not offline:
        feeds = jload(STATE / "feeds_info.json") if (STATE / "feeds_info.json").exists() else {}
        others = []
        for name, f in feeds.items():
            for a in f["aggregators"]:
                others.append((f"feed_{name}.csv.gz", a["aggregator"], [T(SIG["answer_updated"])], FEED_FROM_BLOCK, end))
                if f.get("pre_27000000_file"):
                    others.append((f["pre_27000000_file"]["path"], a["aggregator"], [T(SIG["answer_updated"])], 0, FEED_FROM_BLOCK - 1))
        for sym, g in GAUGES.items():
            if (DATA / f"gauge_{sym}_notify.csv.gz").exists():
                others.append((f"gauge_{sym}_notify.csv.gz", g, [T(SIG["notify_reward"])], 0, end))
        cache = {}
        for j, (fname, addr, topics, lo, hi) in enumerate(others):
            if only and fname not in only:
                continue
            if fname not in cache:
                header, rows = read_csv_gz(fname)
                cache[fname] = [(int(x[0]), int(x[1])) for x in rows]
                checks.setdefault(fname, {}).pop("refetch", None)   # windows of an earlier run are replaced, not appended to
            keys = cache[fname]
            c = checks.setdefault(fname, {})
            c["order"] = {"rows": len(keys), "pass": all(b > a for a, b in zip(keys, keys[1:]))}
            rnd = random.Random(SEED + 100 + j)
            wins = []
            multi = sum(1 for o in others if o[0] == fname) > 1
            small = len(keys) < 9000
            # small files: the whole range again in 7,777,777-block chunks; large ones: 3 random 2,000,000-block windows
            spans = [(lo, hi, 7_777_777)] if small else [(a, a + 2_000_000 - 1, CHUNK) for a in (rnd.randrange(lo, hi - 2_000_000 + 1) for _ in range(3))]
            for a, b, chunk in spans:
                logs, calls = fresh_logs(r, addr, topics, a, b, chunk)
                fresh = [log_key(l) for l in logs]
                mine = keys[bisect.bisect_left(keys, (a, -1)):bisect.bisect_right(keys, (b, 1 << 60))]
                ok = (set(fresh) <= set(mine)) if multi else (fresh == mine)
                wins.append({"aggregator_or_gauge": addr, "from": a, "to": b, "refetched": len(fresh), "dataset_rows_in_window": len(mine),
                             "match": ok, "rule": "subset (file merges several aggregators)" if multi else "equal", "chunk_blocks": chunk})
                say("  ", fname, addr, a, b, len(fresh), len(mine), ok)
            c.setdefault("refetch", {"windows": []})["windows"] += wins
            c["refetch"]["pass"] = all(w["match"] for w in c["refetch"]["windows"])
            jdump(checks_path, checks)
    say("wrote", checks_path)


if __name__ == "__main__":
    main()
