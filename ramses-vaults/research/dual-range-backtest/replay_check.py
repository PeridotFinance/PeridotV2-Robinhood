"""Pure comparison of fork-replay rows against the offline quote walk, with STRICT row accounting.

The set of expected rows is fixed by (sizes, kinds): for every size index i: buy, sell, buyLimited, sellLimited, and sequential. A replay is
rejected if any expected row is missing, any row is duplicated or unexpected, a requested amount differs from the plan, or a number differs
beyond rounding. The expectations are recomputed here from the saved pool-inputs snapshot, never read back from the replay document."""
from __future__ import annotations

import copy

import quote_math as QM

KINDS = ("buy", "sell", "buyLimited", "sellLimited")
TOL_REL = 1e-6
Q96 = QM.Q96


def limits(inputs, usdg, limit_pct):
    st = QM.make_state(inputs)
    plan = QM.trade_plan(inputs, usdg, [1])          # only for usdg0
    usdg0 = plan["usdg0"]
    buy = int(QM.limit_sqrt(st["s"], usdg0, True, limit_pct) * Q96)
    sell = int(QM.limit_sqrt(st["s"], usdg0, False, limit_pct) * Q96)
    return usdg0, buy, sell


def expected_rows(inputs, usdg, sizes, limit_pct):
    plan = QM.trade_plan(inputs, usdg, sizes)
    usdg0, buy_lim, sell_lim = limits(inputs, usdg, limit_pct)
    st0 = QM.make_state(inputs)
    exp = {}
    for i, (b, s) in enumerate(zip(plan["buy"], plan["sell"])):
        for kind, amt, zfo, lim in (("buy", b, usdg0, None), ("sell", s, not usdg0, None), ("buyLimited", b, usdg0, buy_lim / Q96), ("sellLimited", s, not usdg0, sell_lim / Q96)):
            st = copy.deepcopy(st0)
            r = QM.swap(st, amt, zfo, lim)
            exp[(kind, i)] = {"requested": amt, "consumed": r["consumed"], "out": r["out"], "sqrtAfter": r["sqrtAfter"], "status": r["status"]}
        st = copy.deepcopy(st0)
        buy = QM.swap(st, b, usdg0)
        sold = QM.swap(st, buy["out"], not usdg0) if buy["status"] == "filled" else None
        exp[("sequential", i)] = {"requested": b, "consumed": buy["out"], "out": sold["out"] if sold else None, "sqrtAfter": 0.0,
                                  "status": buy["status"] + ("" if sold is None else "/" + sold["status"])}
    return plan, exp, (buy_lim, sell_lim)


def check_rows(inputs, usdg, sizes, limit_pct, fork_rows):
    """fork_rows: list of dicts {kind,index,requestedIn,consumed,out,sqrtAfter} (ints). Returns (report, errors); errors == [] means VERIFIED."""
    plan, exp, _lims = expected_rows(inputs, usdg, sizes, limit_pct)
    errors, seen, report = [], {}, []
    for r in fork_rows:
        key = (r["kind"], r["index"])
        if key in seen:
            errors.append(f"duplicate row {key}")
            continue
        seen[key] = r
    for key in exp:
        if key not in seen:
            errors.append(f"missing row {key}")
    for key in seen:
        if key not in exp:
            errors.append(f"unexpected row {key}")
    for key, r in seen.items():
        if key not in exp:
            continue
        e = exp[key]
        kind = key[0]
        if r["requestedIn"] != e["requested"]:
            errors.append(f"{key}: requested {r['requestedIn']} != planned {e['requested']}")
            continue
        if kind == "sequential":
            # r["consumed"] is the stock obtained by the buy leg, r["out"] the USDG got back by selling it. When the offline buy leg itself hit the
            # end of its scan window it is a lower bound and the sell leg has no offline expectation; when the buy leg is exact but did not fill
            # fully (capacity), the sell leg is not simulated either.
            lower = e["status"].startswith("scan_window_exhausted")
            if lower:
                ok = r["consumed"] >= e["consumed"] - max(2, TOL_REL * e["consumed"])
            elif e["out"] is None:
                ok = abs(r["consumed"] - e["consumed"]) <= max(2, TOL_REL * max(e["consumed"], 1))
            else:
                ok = abs(r["consumed"] - e["consumed"]) <= max(2, TOL_REL * e["consumed"]) and abs(r["out"] - e["out"]) <= max(2, TOL_REL * e["out"])
        else:
            lower = e["status"] == "scan_window_exhausted"
            if lower:      # the offline scan stopped at its window edge: it must be a LOWER BOUND of what the deployed pool did
                ok = r["consumed"] >= e["consumed"] - max(2, TOL_REL * e["consumed"]) and r["out"] >= e["out"] - max(2, TOL_REL * e["out"])
            else:
                ok = (abs(r["consumed"] - e["consumed"]) <= max(2, TOL_REL * max(e["consumed"], 1)) and abs(r["out"] - e["out"]) <= max(2, TOL_REL * max(e["out"], 1))
                      and abs(r["sqrtAfter"] / Q96 - e["sqrtAfter"]) <= 1e-9 * e["sqrtAfter"])
        if not ok:
            errors.append(f"{key}: fork ({r['consumed']}, {r['out']}) != offline ({e['consumed']}, {e['out']}) [{e['status']}]")
        report.append({"kind": kind, "index": key[1], "requestedIn": r["requestedIn"], "forkConsumed": r["consumed"], "forkOut": r["out"], "forkSqrtAfter": r["sqrtAfter"],
                       "offlineConsumed": e["consumed"], "offlineOut": e["out"], "offlineStatus": e["status"], "offlineIsLowerBound": lower, "match": bool(ok)})
    report.sort(key=lambda x: (x["kind"], x["index"]))
    return report, errors
