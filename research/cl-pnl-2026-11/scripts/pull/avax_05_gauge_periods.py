"""Per-period reward supply of the pool's gauge, read at END (data/avalanche/gauge_periods.json).

GaugeV3.notifyRewardAmount adds the notified amount to tokenTotalSupplyByPeriod[period][token] (period =
floor(block.timestamp / 604800)); the position manager pays each position its share of that period's supply. This script
reads tokenTotalSupplyByPeriod for PHAR and xPHAR for every period that overlaps the window, including the period that
contains START (its NotifyReward was emitted before START, so it is not in pharaoh_gauge_notify.csv.gz). Read-only.
"""
from __future__ import annotations

from avax_lib import DATA_AVAX, PHAR, RPC, SEL, XPHAR, addr_arg, eth_call, iso, read_csv_gz, read_json, uint_arg, words, write_json

WEEK = 604800
SRC = "avax"       # archive eth_call (first run of this script used the tenderly gateway; values are identical)


def main() -> None:
    end = read_json(DATA_AVAX / "END.json")
    st = read_json(DATA_AVAX / "state_at_end.json")["decoded"]
    gauge = st["gauge"]["gauge"]
    E = end["end"]["number"]
    p0, p1 = end["start"]["timestamp"] // WEEK, end["end"]["timestamp"] // WEEK
    rows = []
    for p in range(p0, p1 + 1):
        e = {"period": p, "period_start_ts": p * WEEK, "period_start_iso": iso(p * WEEK)}
        for name, tok in (("PHAR", PHAR), ("xPHAR", XPHAR)):
            r = eth_call(SRC, gauge, SEL["tokenTotalSupplyByPeriod(uint256,address)"] + uint_arg(p) + addr_arg(tok), E)
            e[f"tokenTotalSupplyByPeriod_{name}"] = str(words(r)[0])
        rows.append(e)
    # compare with the NotifyReward events of the window, if the dataset has been built
    f = DATA_AVAX / "pharaoh_gauge_notify.csv.gz"
    if f.exists():
        head, data = read_csv_gz(f)
        ev: dict = {}
        for r in data:
            d = dict(zip(head, r))
            if d["token"].lower() == PHAR.lower():
                ev[int(d["period"])] = ev.get(int(d["period"]), 0) + int(d["amount"])
        for e in rows:
            e["sum_of_PHAR_notify_events_in_window"] = str(ev.get(e["period"], 0))
            e["equal"] = ev.get(e["period"], 0) == int(e["tokenTotalSupplyByPeriod_PHAR"])
            if not e["equal"] and e["period"] == p0:
                e["note"] = "this period began before START; its NotifyReward was emitted before the window, so only the state value is available"
    for e in rows:
        print(e)
    write_json(DATA_AVAX / "gauge_periods.json", {"gauge": gauge, "read_at_block": E, "source": "eth_call via " + RPC[SRC].split("/")[2],
                                                  "week_seconds": WEEK, "periods": rows})


if __name__ == "__main__":
    main()
