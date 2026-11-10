"""AVAX/USDT 1-minute klines from the keyless Binance public market-data API, for the swap window [START, END].

Outputs (data/external/):
  raw_binance_avaxusdt_1m.json.gz   the raw kline arrays exactly as served (12 fields each), in order
  avaxusdt_1m.csv.gz                open_time,open,high,low,close,volume   (open_time in ms, prices as served)
  binance_avaxusdt_1m.fetch.json    request ledger (host, startTime, rows) and the gap check

Paged 1,000 per call from the START timestamp; pages are cached in data/.cache/external/binance/. Read-only.
"""
from __future__ import annotations

import time

from avax_lib import CACHE_EXT, DATA_AVAX, DATA_EXT, http_json, iso, read_json, write_csv_gz, write_json, write_json_gz

HOSTS = ["api.binance.com", "data-api.binance.vision"]
SYMBOL, INTERVAL, STEP = "AVAXUSDT", "1m", 60_000


def main() -> None:
    end = read_json(DATA_AVAX / "END.json")
    t0 = (end["start"]["timestamp"] // 60) * 60 * 1000          # the minute containing START
    t1 = (end["end"]["timestamp"] // 60) * 60 * 1000            # the minute containing END (still open at END: dropped below)
    cache = CACHE_EXT / "binance"
    cache.mkdir(parents=True, exist_ok=True)
    rows: list[list] = []
    ledger = []
    cur = t0
    while cur < t1:
        f = cache / f"{SYMBOL}_{INTERVAL}_{cur}.json"
        if f.exists():
            page, host = read_json(f)["rows"], read_json(f)["host"]
        else:
            page = None
            for host in HOSTS:
                try:
                    page = http_json(f"https://{host}/api/v3/klines?symbol={SYMBOL}&interval={INTERVAL}&startTime={cur}&endTime={t1 - 1}&limit=1000")
                    break
                except Exception as e:
                    print("  ", host, "failed:", str(e)[:100])
                    time.sleep(2)
            if page is None:
                raise SystemExit("no Binance host answered")
            if len(page) == 1000:                                # only full pages are final
                write_json(f, {"host": host, "rows": page})
            time.sleep(0.3)
        if not page:
            break
        ledger.append({"host": host, "startTime": cur, "rows": len(page), "first": page[0][0], "last": page[-1][0]})
        rows.extend(page)
        cur = page[-1][0] + STEP
    # closed bars only: close_time < END
    end_ms = end["end"]["timestamp"] * 1000
    rows = [r for r in rows if r[6] < end_ms]
    ot = [r[0] for r in rows]
    assert ot == sorted(ot) and len(set(ot)) == len(ot), "klines not strictly ascending"
    gaps = [{"after": a, "next": b, "missing_minutes": (b - a) // STEP - 1} for a, b in zip(ot, ot[1:]) if b - a != STEP]
    write_json_gz(DATA_EXT / "raw_binance_avaxusdt_1m.json.gz", rows)
    n = write_csv_gz(DATA_EXT / "avaxusdt_1m.csv.gz", ["open_time", "open", "high", "low", "close", "volume"],
                     ([r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows))
    info = {"symbol": SYMBOL, "interval": INTERVAL, "url_pattern": "https://{host}/api/v3/klines?symbol=AVAXUSDT&interval=1m&startTime={ms}&endTime={ms}&limit=1000",
            "hosts_used": sorted({l["host"] for l in ledger}), "requests": ledger, "rows": n,
            "first_open_time": ot[0], "last_open_time": ot[-1], "first_iso": iso(ot[0] / 1000), "last_iso": iso(ot[-1] / 1000),
            "expected_minutes": (ot[-1] - ot[0]) // STEP + 1, "gaps": gaps,
            "window": {"start_ts": end["start"]["timestamp"], "end_ts": end["end"]["timestamp"]},
            "note": "raw kline fields: open time, open, high, low, close, volume, close time, quote volume, trades, taker buy base, taker buy quote, ignore"}
    write_json(DATA_EXT / "binance_avaxusdt_1m.fetch.json", info)
    print(f"{n} klines {info['first_iso']} .. {info['last_iso']}, expected {info['expected_minutes']}, gaps {len(gaps)}")


if __name__ == "__main__":
    main()
