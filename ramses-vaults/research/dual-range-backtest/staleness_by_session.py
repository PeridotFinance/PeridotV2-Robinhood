"""Break the 12-hour feed-staleness bound down by trading session (offline; reads the committed feed answers).

For each feed, every minute from 12 h after its first recorded update to its last update is classified by session (regular 09:30-16:00 ET,
overnight = the rest of the 24/5 week, weekend = Fri 20:00 ET to Sun 20:00 ET, holiday) and by whether the latest update is older than the bound.
It also counts stale EPISODES and says whether each began while the market was open or closed. The bound is NOT relaxed anywhere; this only measures it.

  python3 staleness_by_session.py          writes evidence/staleness-by-session.json
"""
import bisect
import json
import statistics

from common import HERE
from evidence_lib import session_of

BOUND = 12 * 3600
FEEDS = ("SPY", "NVDA", "GLD")


def analyse(name, ts):
    t0, t1 = ts[0] + BOUND, ts[-1]
    per = {s: {"minutes": 0, "stale": 0} for s in ("regular", "overnight", "weekend", "holiday")}
    episodes, cur = [], None
    t = t0
    while t <= t1:
        i = bisect.bisect_right(ts, t) - 1
        stale = (t - ts[i]) > BOUND
        s = session_of(int(t))
        per[s]["minutes"] += 1
        per[s]["stale"] += 1 if stale else 0
        if stale and cur is None:
            cur = {"start": t, "startSession": s}
        if cur is not None and not stale:
            cur["end"] = t
            episodes.append(cur)
            cur = None
        t += 60
    if cur is not None:
        cur["end"] = t1
        episodes.append(cur)
    out = {"feed": name, "updates": len(ts), "windowDays": (t1 - t0) / 86400, "bySession": {}}
    for s, v in per.items():
        out["bySession"][s] = {"hours": v["minutes"] / 60, "staleHours": v["stale"] / 60, "staleShare": (v["stale"] / v["minutes"]) if v["minutes"] else None}
    tot_m = sum(v["minutes"] for v in per.values())
    tot_s = sum(v["stale"] for v in per.values())
    trading_m = per["regular"]["minutes"] + per["overnight"]["minutes"]
    trading_s = per["regular"]["stale"] + per["overnight"]["stale"]
    closed_m = per["weekend"]["minutes"] + per["holiday"]["minutes"]
    closed_s = per["weekend"]["stale"] + per["holiday"]["stale"]
    out["overallStaleShare"] = tot_s / tot_m
    out["tradingSessionsStaleShare"] = trading_s / trading_m if trading_m else None
    out["closuresStaleShare"] = closed_s / closed_m if closed_m else None
    out["staleHoursPerWeek"] = tot_s / 60 / ((t1 - t0) / 86400 / 7)
    out["staleHoursPerWeekDuringTradingSessions"] = trading_s / 60 / ((t1 - t0) / 86400 / 7)
    out["staleHoursPerWeekDuringClosures"] = closed_s / 60 / ((t1 - t0) / 86400 / 7)
    d = [(e["end"] - e["start"]) / 3600 for e in episodes]
    out["episodes"] = {"count": len(d), "medianHours": statistics.median(d) if d else 0, "maxHours": max(d) if d else 0,
                       "beganInTradingSession": sum(1 for e in episodes if e["startSession"] in ("regular", "overnight")),
                       "beganInClosure": sum(1 for e in episodes if e["startSession"] in ("weekend", "holiday"))}
    return out


def main():
    answers = json.loads((HERE / "evidence" / "bundle" / "inputs" / "answer_updated.json").read_text())["series"]
    res = {n: analyse(n, [x["updatedAt"] for x in answers[n]]) for n in FEEDS}
    res["note"] = ("GLD's feed is labelled Crypto (24/7) by the directory, so its 'weekend' and 'holiday' rows are calendar labels, not closures of its market. "
                   "Sessions use evidence_lib.session_of (US holidays through Feb 2027). The guard's 12 h bound is not relaxed by anything here.")
    (HERE / "evidence" / "staleness-by-session.json").write_text(json.dumps(res, indent=1))
    for n in FEEDS:
        r = res[n]
        print(f"{n}: stale {100 * r['overallStaleShare']:.1f}% overall; trading sessions {100 * r['tradingSessionsStaleShare']:.1f}%; closures {100 * (r['closuresStaleShare'] or 0):.1f}%; "
              f"{r['staleHoursPerWeek']:.1f} h/week ({r['staleHoursPerWeekDuringTradingSessions']:.1f} in trading sessions); episodes {r['episodes']}")
        for s, v in r["bySession"].items():
            print(f"    {s:9s} {v['hours']:8.0f} h, stale {v['staleHours']:7.0f} h = {100 * (v['staleShare'] or 0):5.1f}%")


if __name__ == "__main__":
    main()
