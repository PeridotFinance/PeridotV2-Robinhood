"""Step 6: oracle feeds -> feed_<name>.csv.gz with block,log_index,updated_at,answer,round_id.

AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt) on every AGGREGATOR a feed's proxy
has pointed at (phaseAggregators(1..phaseId) from discovery, the aggregator at END, and the aggregator named in the
task), blocks 27,000,000..END. Answers before block 27,000,000 go to feed_<name>_pre27m.csv.gz. ``round_id`` is the aggregator's own round number as emitted (the proxy's round id is
phase << 64 | round). Rows are sorted by (block, log_index); per-aggregator block ranges go to the manifest.
"""
from __future__ import annotations

from rh_lib import *  # noqa: F401,F403


FEED247_ROUNDS = PERIDOT / "RobinhoodVaults" / "deployments" / "nvda-247-feed-2026-11-07" / "feed247-rounds.json"


def main():
    end = load_end()["END"]
    disc = jload(DISCOVERY_FILE)
    st = jload(END_STATE_FILE)
    r = rpc()
    topic = T(SIG["answer_updated"])
    infos = {}
    for name, f in disc["feeds"].items():
        aggs = []  # (phase or None, address)
        for ph, a in sorted(f["phaseAggregators"].items(), key=lambda kv: int(kv[0])):
            if not a.startswith("ERR") and int(a, 16) != 0:
                aggs.append((int(ph), a.lower()))
        for extra in (st["feeds"][name]["aggregator_at_END"], f.get("aggregator_named_in_task")):
            if extra and extra.lower() not in {a for _, a in aggs}:
                aggs.append((None, extra.lower()))
        rows, per, early = [], [], []
        for ph, a in aggs:
            logs = r.logs(a, [topic], FEED_FROM_BLOCK, end, end)
            before = r.logs(a, [topic], 0, FEED_FROM_BLOCK - 1, end)
            rs = []
            for l in logs:
                w = words(l["data"])
                rs.append((l["blockNumber"], l["logIndex"], w[0], topic_int(l["topics"][1]), int(l["topics"][2], 16)))
            for l in before:
                w = words(l["data"])
                early.append((l["blockNumber"], l["logIndex"], w[0], topic_int(l["topics"][1]), int(l["topics"][2], 16)))
            rounds = [x[4] for x in rs]
            gaps = sum(1 for x, y in zip(rounds, rounds[1:]) if y != x + 1)
            per.append({"phase": ph, "aggregator": a, "rows": len(rs), "events_before_block_27000000": len(before),
                        "first_block": rs[0][0] if rs else None, "last_block": rs[-1][0] if rs else None,
                        "first_round": rounds[0] if rounds else None, "last_round": rounds[-1] if rounds else None,
                        "round_id_not_consecutive_steps": gaps,
                        "first_updated_at": rs[0][2] if rs else None, "last_updated_at": rs[-1][2] if rs else None})
            rows += rs
            say(name, a, "rows", len(rs), "before 27M", len(before), "round gaps", gaps)
        rows.sort(key=lambda x: (x[0], x[1]))
        if len({(x[0], x[1]) for x in rows}) != len(rows):
            raise EvidenceError(f"{name}: duplicate (block, log_index)")
        info = write_csv_gz(f"feed_{name}.csv.gz", ["block", "log_index", "updated_at", "answer", "round_id"], rows)
        # the feeds also answered before block 27,000,000: kept apart so the main file is exactly the requested range
        early.sort(key=lambda x: (x[0], x[1]))
        info["pre_27000000_file"] = None
        if early:
            e = write_csv_gz(f"feed_{name}_pre27m.csv.gz", ["block", "log_index", "updated_at", "answer", "round_id"], early)
            er = [x[4] for x in early]
            info["pre_27000000_file"] = {"path": e["path"], "rows": e["rows"], "first_block": early[0][0], "last_block": early[-1][0],
                                         "first_round": er[0], "last_round": er[-1],
                                         "round_id_not_consecutive_steps": sum(1 for x, y in zip(er, er[1:]) if y != x + 1),
                                         "continues_into_main_file": bool(rows) and rows[0][4] == er[-1] + 1}
        latest = st["feeds"][name]
        last = rows[-1] if rows else None
        info.update({
            "proxy": f["proxy"], "description": f["description"], "decimals": f["decimals"], "phaseId_at_discovery": f["phaseId"],
            "aggregators": per, "topic0": topic, "signature": SIG["answer_updated"], "from_block": FEED_FROM_BLOCK,
            "updated_at_non_monotonic_steps": sum(1 for x, y in zip(rows, rows[1:]) if y[2] < x[2]),
            "latestRoundData_at_END": {k: latest[k] for k in ("roundId", "phase", "aggregator_round", "answer", "updatedAt", "aggregator_at_END")},
            "last_row_matches_latestRoundData_at_END": bool(last and str(last[3]) == latest["answer"] and last[2] == latest["updatedAt"]
                                                            and last[4] == latest["aggregator_round"]),
        })
        if name == "nvda_247" and FEED247_ROUNDS.exists():
            ref = jload(FEED247_ROUNDS)   # rounds read through the proxy on 2026-11-07 (read only)
            got = {x[4]: (x[3], x[2]) for x in rows}
            info["reproduces_feed247_rounds_json"] = {
                "file": str(FEED247_ROUNDS), "rounds_in_file": len(ref),
                "all_match": all(x["round"] in got and got[x["round"]] == (round(x["answer"] * 1e8), x["updatedAt"]) for x in ref)}
        infos[name] = info
        say(name, "rows", info["rows"], "last row == latestRoundData at END:", info["last_row_matches_latestRoundData_at_END"])
    jdump(STATE / "feeds_info.json", infos)


if __name__ == "__main__":
    main()
