"""Render the README tables for section 16 from evidence/oracle/{fallback-study,twap-probe}.json and the pool depth. Numbers are generated, never typed."""
import json
from common import HERE

S = json.load(open(HERE / "evidence/oracle/fallback-study.json"))
P = json.load(open(HERE / "evidence/pools/pool-quotes.json"))["pools"]
f = lambda x, d=2: "-" if x is None else f"{100 * x:.{d}f}%"


def basis_table():
    rows = ["| pair | session | feed updates | 30 min TWAP vs feed: median | p90 | p99 | max |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for pair, d in S["pairs"].items():
        for sess, b in d["basisPoolTwapVsFeedAtFeedUpdate"]["30m"].items():
            if b["n"] >= 5:
                rows.append(f"| {pair} | {sess} | {b['n']} | {f(b['p50'])} | {f(b['p90'])} | {f(b['p99'])} | {f(b['max'])} |")
    return "\n".join(rows)


def error_table():
    rows = ["| pair | gap between feed answers | pairs | keep the last price: median / p90 / max | anchored 30 min TWAP: median / p90 / max |", "| --- | --- | --- | --- | --- |"]
    for pair, d in S["pairs"].items():
        for g, v in list(d["anchoredErrorByGap"].items()) + list(d["anchoredErrorByGapSpanningClosure"].items()):
            n, a = v["naiveKeepLastPrice"], v["anchoredTwap_30m"]
            rows.append(f"| {pair} | {g} | {v['pairs']} | {f(n['p50'])} / {f(n['p90'])} / {f(n['max'])} | {f(a['p50'])} / {f(a['p90'])} / {f(a['max'])} |")
    return "\n".join(rows)


def availability_table():
    rows = ["| pair | rule | max age D | move limit L | stale trading-session time recovered | stale closure time recovered | stale hours/week now -> after | error vs next answer: p99 / max | wrongly liquidated at 98% / 99% of limit |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for pair, d in S["pairs"].items():
        wk = d["weeksObserved"]
        now = d["staleMinutes"]["total"] / 60 / wk
        for p in d["policies"]:
            if p["deviationLimit"] != 0.02:
                continue
            after = now - p["recoveredHoursPerWeek"]
            e = p["errorVsNextAnswer"]
            fl = p["falseLiquidationExposure"]
            rows.append(f"| {pair} | {p['rule']} | {p['maxDurationHours']:.0f} h | {f(p['deviationLimit'], 0)} | {f(p['recoveredShareOfStaleTradingSessionTime'], 1)} | {f(p['recoveredShareOfStaleClosureTime'], 1)} | {now:.1f} -> {after:.1f} | "
                        f"{f(e['p99']) if e else '-'} / {f(e['max']) if e else '-'} | {f(fl['u=0.98'], 1)} / {f(fl['u=0.99'], 1)} |")
    return "\n".join(rows)


def limit_sensitivity():
    rows = ["| pair | rule (D = 72 h) | L | trading-session time recovered | closure time recovered | error max |", "| --- | --- | --- | --- | --- | --- |"]
    for pair, d in S["pairs"].items():
        for p in d["policies"]:
            if p["maxDurationHours"] == 72 and p["rule"].startswith("all"):
                e = p["errorVsNextAnswer"]
                rows.append(f"| {pair} | all sessions | {f(p['deviationLimit'], 0)} | {f(p['recoveredShareOfStaleTradingSessionTime'], 1)} | {f(p['recoveredShareOfStaleClosureTime'], 1)} | {f(e['max']) if e else '-'} |")
    return "\n".join(rows)


def pools_table():
    T = json.load(open(HERE / "evidence/oracle/twap-probe.json"))
    rows = ["| pool | observation cardinality | longest TWAP window the pool serves | USD to move the price 2% (buy / sell) |", "| --- | --- | --- | --- |"]
    pools = T.get("pools", T)
    for name, v in (pools.items() if isinstance(pools, dict) else []):
        key = name
        dep = P.get(key, {}).get("depth") or []
        up = next((x["inputUsd"] for x in dep if x["direction"] == "price_up_buy_stock" and x["movePct"] == 2.0), None)
        dn = next((x["inputUsd"] for x in dep if x["direction"] == "price_down_sell_stock" and x["movePct"] == 2.0), None)
        card = v.get("cardinality", v.get("observationCardinality"))
        win = v.get("longestServedWindowSeconds", v.get("longestWindow"))
        rows.append(f"| {name} | {card} | {win} s | " + (f"${up:,.0f} / ${dn:,.0f}" if up and dn else "n/a") + " |")
    return "\n".join(rows)


def mult():
    m = S["multiplierCheckSPY"]
    rows = ["| hypothesis for SPY | expected pool-over-feed basis | distance from the measured mean |", "| --- | --- | --- |"]
    for k, h in m["hypotheses"].items():
        rows.append(f"| {k} | {100 * h['expected']:+.3f}% | {h['sigmas']:.1f} standard errors |")
    return f"Measured over {m['n']} feed updates in open sessions: mean basis {100 * m['meanBasis']:+.3f}% (median {100 * m['medianBasis']:+.3f}%, standard error {100 * m['standardError']:.3f}%).\n\n" + "\n".join(rows)


if __name__ == "__main__":
    for fn in (pools_table, basis_table, error_table, availability_table, limit_sensitivity, mult):
        print(fn.__name__.upper()); print(fn()); print()


# ---------------------------------------------------------------- replay tables (fallback_replay.py): the executable policy, step by step
RP = None


def _rp():
    global RP
    if RP is None:
        RP = json.load(open(HERE / "evidence/oracle/fallback-replay.json"))
    return RP


def _fails(d):
    return "; ".join(f"{k.replace('AnchorMissing: ', 'no anchor: ')} {v} min" for k, v in list(d.items())[:4]) or "none"


def replay_reference():
    rows = ["| pair | weeks | oracle returns a price: trading / closure | exit completes (price AND removal guard at 6%): trading / closure | refusals (stale minutes) | one multiplier step per window loses | error vs next answer p99 / max |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for pair, r in _rp()["pairs"].items():
        x = r["reference"]
        e = x["errorVsNextAnswer"]
        rows.append(f"| {pair} | {r['weeksObserved']:.1f} | {f(x['returnsShareTrading'], 1)} / {f(x['returnsShareClosure'], 1)} | {f(x['completesShareTrading'], 1)} / {f(x['completesShareClosure'], 1)} | {_fails(x['failures'])} | {f(x['multiplierStepLossShare'], 1)} of recovered minutes | {f(e['p99'])} / {f(e['max'])} |")
    return "\n".join(rows)


def replay_grid():
    rows = ["| pair | D open | D closed | L | trading time: price returned | closure time: price returned | error vs next answer p99 / max | refusals |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for pair, r in _rp()["pairs"].items():
        for g in r["grid"]:
            if g["clock"] != "as-written" or g["L"] != 0.02:
                continue
            e = g["errorVsNextAnswer"]
            rows.append(f"| {pair} | {g['dOpenH']} h | {g['dClosedH']} h | 2% | {f(g['returnsShareTrading'], 1)} | {f(g['returnsShareClosure'], 1)} | {f(e['p99']) if e else '-'} / {f(e['max']) if e else '-'} | {_fails(g['failures'])} |")
    return "\n".join(rows)


def replay_limit():
    rows = ["| pair | L (D open 24 h, D closed 72 h) | trading time | closure time | error p99 / max |", "| --- | --- | --- | --- | --- |"]
    for pair, r in _rp()["pairs"].items():
        for g in r["grid"]:
            if g["clock"] == "as-written" and g["dOpenH"] == 24 and g["dClosedH"] == 72:
                e = g["errorVsNextAnswer"]
                rows.append(f"| {pair} | {f(g['L'], 0)} | {f(g['returnsShareTrading'], 1)} | {f(g['returnsShareClosure'], 1)} | {f(e['p99']) if e else '-'} / {f(e['max']) if e else '-'} |")
    return "\n".join(rows)


def replay_pokes():
    rows = ["| pair | poke model | max basis accepted at poke | answers anchored | trading time | closure time | why answers were not anchored |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for pair, r in _rp()["pairs"].items():
        for s in r["sensitivityPokeAndBasis"]:
            if s["maxBasis"] not in (0.01, 0.02):
                continue
            why = ", ".join(f"{k} {v}" for k, v in s["notAnchoredReasons"].items()) or "-"
            rows.append(f"| {pair} | {s['poke']} | {f(s['maxBasis'], 1)} | {f(s['anchoredAnswerShare'], 0)} | {f(s['returnsShareTrading'], 1)} | {f(s['returnsShareClosure'], 1)} | {why} |")
    return "\n".join(rows)


def replay_modes():
    rows = ["| pair | variant | removal bound R | price returned: trading / closure | exit completes: trading / closure | error vs next answer p99 / max |", "| --- | --- | --- | --- | --- | --- |"]
    for pair, r in _rp()["pairs"].items():
        for s in r["sensitivityRemovalBoundModeWindow"]:
            if "mode" not in s:
                continue
            e = s["errorVsNextAnswer"]
            rows.append(f"| {pair} | {s['mode']} | {f(s['removalBound'], 0)} | {f(s['returnsShareTrading'], 1)} / {f(s['returnsShareClosure'], 1)} | {f(s['completesShareTrading'], 1)} / {f(s['completesShareClosure'], 1)} | {f(e['p99'])} / {f(e['max'])} |")
    return "\n".join(rows)
