from __future__ import annotations
"""Glue: load a pool dataset + feed + gauge emissions into an engine.Dataset."""
import bisect, json, pickle
from common import *
import engine as E

def clean_glitches(cols: dict, max_jump_ticks: int = 1000):
    """Drop swaps whose resulting tick is more than ``max_jump_ticks`` (~10%) away from the last accepted tick.

    The GLD pool was driven to MAX_TICK (price 3e-27, zero active liquidity) for 28.5 minutes on 2026-09-28 by a
    dust swap while no third-party liquidity was in range. Our own layers would have filled that gap, so replaying the
    jump would manufacture a total loss that could not have happened to us. The event is reported separately."""
    keep, dropped = [], []
    last = cols["tick_after"][0]
    for i, t in enumerate(cols["tick_after"]):
        if abs(t - last) > max_jump_ticks:
            dropped.append(i)
            continue
        keep.append(i)
        last = t
    if not dropped:
        return cols, dropped
    out = {k: [v[i] for i in keep] for k, v in cols.items()}
    return out, dropped


def load_dataset(name: str, since: float | None = None, ram: bool = True):
    cols, meta = pickle.load(open(RESULTS / f"dataset_{name}.pkl", "rb"))
    cols, dropped = clean_glitches(cols)
    meta["glitch_swaps_dropped"] = dropped
    feed = json.load(open(RESULTS / "answer_updated.json"))["series"][name]
    ot = [x["updatedAt"] for x in feed]
    op = [x["answer"] / 1e8 for x in feed]
    # drop non-positive or bootstrap answers (the first rounds of a feed can be seed values)
    keep = [i for i in range(len(ot)) if op[i] > 1.0]
    ot = [ot[i] for i in keep]; op = [op[i] for i in keep]
    notif = json.load(open(RESULTS / "gauge_notify.json"))[name]
    per = {}
    for x in notif:
        if x["token"].lower() == RAM.lower():
            per[x["period"]] = per.get(x["period"], 0) + x["amount"] / 1e18
    def ram_rate(t):
        return per.get(int(t // 604800), 0.0) / 604800.0
    ds = E.Dataset(cols["ts"], cols["s_after"], cols["tick_after"], cols["L_after"], cols["in_tok"], cols["in_amt"],
                   cols["fee_ppm"], ot, op, E.Market(ADDR[name]["stock_is_token0"]), ram_rate=ram_rate if ram else None,
                   proto=cols["proto"])
    i0 = 0 if since is None else bisect.bisect_left(ds.ts, since)
    return ds, meta, i0
