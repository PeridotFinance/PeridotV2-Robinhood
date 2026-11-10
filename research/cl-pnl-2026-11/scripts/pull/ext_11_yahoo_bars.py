"""NVDA and SPY intraday bars including pre-market and after-hours, from the keyless Yahoo Finance chart API.

For each symbol three granularities are requested, each over the longest period the API serves for it:
  1m : the last ~30 days, in requests of <= 7 days
  5m : the last ~60 days, in requests of <= 20 days
  1h : 2026-08-01 .. now, one request per <= 60 days
Outputs (data/external/): <sym>_1m.csv.gz, <sym>_5m.csv.gz, <sym>_1h.csv.gz with columns ts,open,high,low,close,volume
(ts = bar open, unix seconds), raw_yahoo_<sym>_<interval>.json.gz (every raw response with its period1/period2), and
yahoo_bars.fetch.json (per file: requests, rows, null bars dropped, session mix).

Bars are written exactly as served. Bars whose close is null are dropped and counted; a bar still open at fetch time is
dropped and counted; nothing is filled or synthesised. Read-only.
"""
from __future__ import annotations

import datetime as dt
import sys
import time
import urllib.error
from zoneinfo import ZoneInfo

from avax_lib import DATA_EXT, http_json, iso, write_csv_gz, write_json, write_json_gz

HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
T_START = 1785542400                        # 2026-08-01 00:00 UTC
NY = ZoneInfo("America/New_York")
PLAN = {  # interval -> (seconds per bar, how far back the API serves it, max days per request)
    "1m": (60, 29.5, 7),
    "5m": (300, 59.5, 20),
    "1h": (3600, None, 60),
}


def session(ts: int) -> str:
    t = dt.datetime.fromtimestamp(ts, NY)
    m = t.hour * 60 + t.minute
    if t.weekday() >= 5:
        return "weekend"
    if m < 570:
        return "pre"
    if m < 960:
        return "regular"
    return "post"


def fetch(sym: str, interval: str, p1: int, p2: int):
    last = None
    for attempt in range(4):
        for host in HOSTS:
            url = f"https://{host}/v8/finance/chart/{sym}?interval={interval}&includePrePost=true&period1={p1}&period2={p2}"
            try:
                return host, http_json(url, timeout=60)
            except urllib.error.HTTPError as e:
                body = e.read()[:300].decode("utf-8", "replace")
                last = f"HTTP {e.code} {body}"
                if e.code in (400, 404, 422):
                    return host, {"http_error": e.code, "body": body}
            except Exception as e:
                last = repr(e)[:200]
            time.sleep(2 + 2 * attempt)
    return None, {"http_error": "unreachable", "body": last}


def main() -> None:
    now = int(time.time())
    report = {"fetched_at_unix": now, "fetched_at_iso": iso(now),
              "url_pattern": "https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval={1m|5m|1h}&includePrePost=true&period1={unix}&period2={unix}",
              "files": {}}
    for sym in ("NVDA", "SPY"):
        for interval, (sec, back_days, max_days) in PLAN.items():
            lo = T_START if back_days is None else max(T_START, now - int(back_days * 86400))
            raws, bars, reqs = [], {}, []
            nulls = 0
            p1 = lo
            while p1 < now:
                p2 = min(p1 + max_days * 86400, now)
                host, d = fetch(sym, interval, p1, p2)
                raws.append({"host": host, "period1": p1, "period2": p2, "response": d})
                res = ((d.get("chart") or {}).get("result") or [None])[0] if isinstance(d, dict) else None
                err = d.get("http_error") or ((d.get("chart") or {}).get("error")) if isinstance(d, dict) else "bad reply"
                got = 0
                if res and res.get("timestamp"):
                    q = res["indicators"]["quote"][0]
                    for i, ts in enumerate(res["timestamp"]):
                        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
                        if c is None or o is None:
                            nulls += 1
                            continue
                        bars[ts] = (ts, repr(o), repr(h), repr(l), repr(c), v if v is not None else "")
                        got += 1
                reqs.append({"host": host, "period1": p1, "period2": p2, "bars": got, "error": err if not got else None,
                             "dataGranularity": (res or {}).get("meta", {}).get("dataGranularity") if res else None})
                print(f"  {sym} {interval} {iso(p1)}..{iso(p2)}: {got} bars {('ERR ' + str(err)[:120]) if not got else ''}", flush=True)
                p1 = p2
                time.sleep(1.5)
            rows = [bars[k] for k in sorted(bars)]
            # a bar is kept only if it is on the interval grid and had closed when fetched
            open_bars = [r for r in rows if r[0] + sec > now]
            off_grid = [r for r in rows if r[0] % 60 != 0]
            rows = [r for r in rows if r[0] + sec <= now and r[0] % 60 == 0]
            mix: dict[str, int] = {}
            for r in rows:
                s = session(r[0])
                mix[s] = mix.get(s, 0) + 1
            name = f"{sym.lower()}_{interval}.csv.gz"
            write_json_gz(DATA_EXT / f"raw_yahoo_{sym.lower()}_{interval}.json.gz", raws)
            n = write_csv_gz(DATA_EXT / name, ["ts", "open", "high", "low", "close", "volume"], rows)
            report["files"][name] = {
                "symbol": sym, "interval": interval, "rows": n, "requests": reqs,
                "requested_from_iso": iso(lo), "requested_to_iso": iso(now),
                "first_ts": rows[0][0] if rows else None, "last_ts": rows[-1][0] if rows else None,
                "first_iso": iso(rows[0][0]) if rows else None, "last_iso": iso(rows[-1][0]) if rows else None,
                "null_bars_dropped": nulls, "open_bars_dropped": len(open_bars), "off_grid_bars_dropped": len(off_grid),
                "session_mix_new_york": mix, "pre_post_present": bool(mix.get("pre") or mix.get("post")),
                "trading_days": len({dt.datetime.fromtimestamp(r[0], NY).date() for r in rows}),
            }
            print(f"{name}: {n} rows {report['files'][name]['first_iso']} .. {report['files'][name]['last_iso']} mix {mix} nulls {nulls}", flush=True)
    write_json(DATA_EXT / "yahoo_bars.fetch.json", report)


if __name__ == "__main__":
    sys.exit(main())
