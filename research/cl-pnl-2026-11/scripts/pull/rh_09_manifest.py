"""Step 9: MANIFEST.json and SHA256SUMS for data/robinhood (offline; reads the files and the state/*.json facts)."""
from __future__ import annotations

from rh_lib import *  # noqa: F401,F403


def grid_ts():
    g = {int(k): v[0] for k, v in jload(HEADERS_CACHE).items() if int(k) % GRID == 0}
    keys = sorted(g)

    def f(n):
        i = bisect.bisect_right(keys, n) - 1
        if keys[i] == n:
            return float(g[n])
        a, b = keys[i], keys[i + 1]
        return g[a] + (g[b] - g[a]) * (n - a) / (b - a)
    return f


def opt(path):
    return jload(path) if path.exists() else None


def main():
    end = load_end()
    disc = jload(DISCOVERY_FILE)
    st = jload(END_STATE_FILE)
    # checks.json: the first (offline) run; checks_online.json / checks_univ3.json: later runs with the re-fetch and header reads.
    # A later run recomputes the offline parts too, so its entry replaces the earlier one key by key.
    checks, check_files = {}, []
    for name in ("checks.json", "checks_online.json", "checks_univ3.json"):
        c = opt(STATE / name)
        if c:
            check_files.append("state/" + name)
            for k, v in c.items():
                if isinstance(v, dict):
                    checks.setdefault(k, {}).update(v)
    v4 = opt(STATE / "v4_info.json") or {}
    feeds = opt(STATE / "feeds_info.json") or {}
    rew = opt(STATE / "rewards_info.json") or {}
    hdr = opt(STATE / "headers_info.json") or {}
    univ3 = opt(STATE / "univ3_info.json")
    ram = {s: opt(STATE / f"ramses_{s}_info.json") for s in ("nvda", "spy")}
    tsf = grid_ts()

    source = {}
    for s in ("nvda", "spy"):
        i = ram[s]
        if not i:
            continue
        common = {"contract": i["pool"], "token0": i["token0"], "decimals0": i["decimals0"], "token1": i["token1"], "decimals1": i["decimals1"],
                  "tick_spacing": i["tick_spacing"], "initialize": i["initialize"], "provenance": i["provenance"]}
        source[f"ramses_{s}_swaps.csv.gz"] = dict(common, topics=[T(SIG["v3_swap"])], event=SIG["v3_swap"], fee_events=i["fee_events"],
                                                   protocol_share_events=i["protocol_share_events"],
                                                   amount_sign="as emitted by the pool: positive = paid into the pool")
        source[f"ramses_{s}_liquidity.csv.gz"] = dict(common, topics=[[T(SIG["v3_mint"]), T(SIG["v3_burn"])]], events=[SIG["v3_mint"], SIG["v3_burn"]],
                                                       mints=i["mints"], burns=i["burns"],
                                                       decoding="Mint: topics owner,tickLower,tickUpper; data sender,index,amount,amount0,amount1. Burn: data amount,amount0,amount1 (negated)")
    for s, i in v4.items():
        p = i["pool"]
        common = {"contract": V4_POOL_MANAGER, "pool_id": p["pool_id"], "currency0": p["currency0"], "currency1": p["currency1"], "fee": p["fee"],
                  "tick_spacing": p["tick_spacing"], "hooks": p["hooks"],
                  "initialize": {"block": p["init_block"], "log_index": p["init_log_index"], "sqrtPriceX96": p["init_sqrt_price_x96"], "tick": p["init_tick"]}}
        source[f"v4_{s}_swaps.csv.gz"] = dict(common, topics=i["topics"]["swap"], event=SIG["v4_swap"], swap_fee_field_histogram=i["swap_fee_field_histogram"],
                                               amount_sign="event reports the swapper's delta; both signs flipped so positive = paid into the pool")
        source[f"v4_{s}_liquidity.csv.gz"] = dict(common, topics=i["topics"]["modify"], event=SIG["v4_modify"])
    for name, i in feeds.items():
        source[f"feed_{name}.csv.gz"] = {k: i[k] for k in ("proxy", "description", "decimals", "phaseId_at_discovery", "aggregators", "topic0", "signature",
                                                           "from_block", "updated_at_non_monotonic_steps", "latestRoundData_at_END",
                                                           "last_row_matches_latestRoundData_at_END")}
        if i.get("pre_27000000_file"):
            source[i["pre_27000000_file"]["path"]] = dict({k: i[k] for k in ("proxy", "description", "decimals", "topic0", "signature")},
                                                          aggregators=[a["aggregator"] for a in i["aggregators"]], block_range=[0, FEED_FROM_BLOCK - 1],
                                                          **{k: v for k, v in i["pre_27000000_file"].items() if k not in ("path", "rows")})
            source[f"feed_{name}.csv.gz"]["pre_27000000_file"] = i["pre_27000000_file"]["path"]
        if "reproduces_feed247_rounds_json" in i:
            source[f"feed_{name}.csv.gz"]["reproduces_feed247_rounds_json"] = i["reproduces_feed247_rounds_json"]
    for k, i in rew.items():
        fname = "ram_pool_swaps.csv.gz" if k == "ram_pool" else f"{k}_notify.csv.gz"
        source[fname] = {a: b for a, b in i.items() if a not in ("path", "rows", "first_row", "last_row")}
    source["headers.csv.gz"] = {"method": "eth_getBlockByNumber on every multiple of 10,000", **{k: v for k, v in hdr.items() if k not in ("path", "rows", "first_row", "last_row")}}
    if univ3:
        for fname, i in univ3.get("files", {}).items():
            source[fname] = i

    files = []
    for path in sorted(DATA.glob("*.csv.gz")):
        # streamed: some files have millions of rows
        n, first, last = 0, None, None
        with gzip.open(path, "rt", encoding="ascii", newline="") as fh:
            rd = csv.reader(fh)
            header = next(rd)
            for row in rd:
                if first is None:
                    first = row
                last = row
                n += 1
        rows = [first, last] if n else []
        ix = {h: i for i, h in enumerate(header)}
        ent = {"path": path.name, "columns": header, "rows": n, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        if rows:
            b0, b1 = int(rows[0][ix["block"]]), int(rows[-1][ix["block"]])
            ent["first_block"], ent["last_block"] = b0, b1
            if "ts" in ix:
                t0, t1, how = float(rows[0][ix["ts"]]), float(rows[-1][ix["ts"]]), "ts column (interpolated on the header grid)"
            elif "updated_at" in ix:
                t0, t1, how = int(rows[0][ix["updated_at"]]), int(rows[-1][ix["updated_at"]]), "updated_at column (from the event)"
            elif "timestamp" in ix:
                t0, t1, how = int(rows[0][ix["timestamp"]]), int(rows[-1][ix["timestamp"]]), "timestamp column (block header)"
            else:
                t0, t1, how = tsf(b0), tsf(b1), "block interpolated on the header grid (file has no time column)"
            ent["first_time"], ent["last_time"], ent["time_source"] = iso(t0), iso(t1), how
        ent["source"] = source.get(path.name)
        ent["checks"] = checks.get(path.name)
        files.append(ent)
    extra = []
    for path in sorted(list(DATA.glob("*.json")) + list(STATE.glob("*.json"))):
        if path.name == "MANIFEST.json":
            continue
        rel = str(path.relative_to(DATA))
        mine = rel.startswith("state/") or rel in ("v4_pools.json", "univ3_pools.json")
        extra.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path),
                      "origin": "rh_* pull scripts" if mine else "not written by the rh_* pull scripts (listed and hashed only)"})

    summary = {}
    for f in files:
        c = f.get("checks") or {}
        row = {}
        if "order" in c:
            row["order_pass"] = c["order"].get("pass")
        if "refetch" in c:
            row["refetch_pass"] = c["refetch"].get("pass")
            row["refetch_windows"] = len(c["refetch"].get("windows", []))
        if "end_state" in c:
            row["end_state_pass"] = c["end_state"].get("pass")
        if "liquidity_rebuild" in c:
            row["liquidity_rebuild_pass"] = c["liquidity_rebuild"].get("pass")
        if "v3_math" in c:
            m = c["v3_math"]
            row["v3_math_eligible_pairs"] = m["eligible_equal_liquidity"]
            row["v3_math_output_leg_over_10_units"] = m["output_leg"]["over_10"]
            row["v3_math_input_leg_over_10_units"] = m["input_leg_fee"]["over_10"]
            if "exceptions_classified" in m:
                row["v3_math_exceptions"] = {k: v for k, v in m["exceptions_classified"].items() if k != "detail"}
        if "ts_error" in c:
            row["ts_max_abs_error_s"] = c["ts_error"].get("max_abs_error_s")
        summary[f["path"]] = row or None

    manifest = {
        "generated_by": "research/cl-pnl-2026-11/scripts/pull (rh_01_discover .. rh_09_manifest, rh_lib)",
        "chain": {"name": "Robinhood Chain mainnet", "chain_id": disc["chain_id"], "rpc": "https://rpc.mainnet.chain.robinhood.com"},
        "END": end,
        "conventions": {
            "integers": "plain decimal strings", "ts": "unix seconds, linear interpolation between real headers on the 10,000-block grid, 1 decimal",
            "gzip": "mtime 0, no embedded file name (sha256 stable across re-runs of the same content)",
            "pool_side_amounts": "positive = paid into the pool",
        },
        "state_at_END": {"ramses": {k: {a: b for a, b in v.items() if a != "slot0_words"} for k, v in st["ramses"].items()}, "univ3": {k: {a: b for a, b in v.items() if a != "slot0_words"} for k, v in st.get("univ3", {}).items()},
                         "v4": st["v4"], "feeds": st["feeds"],
                         "read_seconds": st.get("read_seconds"), "head_after_reads": st.get("head_after_reads")},
        "state_retention_probe": disc.get("state_retention_probe_blocks_behind_head"),
        "v4_pools_table": "v4_pools.json",
        "univ3_check": {"table": "univ3_pools.json", "factory": univ3.get("factory"), "factory_source": univ3.get("factory_source"),
                        "repo_grep_note": univ3.get("repo_grep_note"),
                        "pools": (opt(DATA / "univ3_pools.json") or {}).get("pools")} if univ3 else None,
        "check_files_merged": check_files,
        "checks_summary": summary,
        "gauge_signature_check": disc.get("gauges"),
        "feeds_discovery": disc.get("feeds"),
        "files": files,
        "other_files": extra,
    }
    jdump(DATA / "MANIFEST.json", manifest)
    lines = []
    for path in sorted(p for p in DATA.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{sha256_file(path)}  {path.relative_to(DATA)}")
    (DATA / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    for f in files:
        say(f["path"], f["rows"], f.get("first_block"), f.get("last_block"), f.get("first_time"), f.get("last_time"), f["sha256"][:12])


if __name__ == "__main__":
    main()
