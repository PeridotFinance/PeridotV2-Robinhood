"""Gauge reward streams (RAM on Ramses, PHAR on Pharaoh) from the committed notification events and price tables.

Rewards are never added to a return silently: callers report tokens, and a USD figure only "if sold" at the price in
force when the tokens were earned, less a stated sale cost. No rate is projected forward.
"""
from __future__ import annotations

import csv
import gzip

import numpy as np

from lib_cl import DATA, Series, VENUES, load_swaps

WEEK = 604800


def notifications(rel: str, token: str, decimals: int = 18) -> list[tuple[int, float, float]]:
    """(period, timestamp, tokens) for every notification of ``token`` on the gauge."""
    out = []
    with gzip.open(DATA / rel, "rt") as f:
        for row in csv.DictReader(f):
            if row["token"].lower() != token.lower():
                continue
            ts = float(row["ts"])
            period = int(row["period"]) if row.get("period") not in (None, "") else int(ts // WEEK)
            out.append((period, ts, int(row["amount"]) / 10 ** decimals))
    return out


def emission_rate(cfg: dict):
    """t -> reward tokens per second emitted to the whole gauge. A notification funds its own weekly period; tokens
    notified part-way through a period are spread over what is left of it (the gauge streams from the notification)."""
    notes = notifications(cfg["notify_file"], cfg["token"], cfg.get("decimals", 18))
    # a period whose notification predates the table: amount read from gauge state (source named in params.json)
    notes += [(int(x["period"]), int(x["period"]) * WEEK, float(x["tokens"])) for x in cfg.get("periods_from_state", [])]
    spans = []
    for period, ts, amt in notes:
        start = max(ts, period * WEEK)
        end = (period + 1) * WEEK
        if end > start:
            spans.append((start, end, amt / (end - start)))

    def rate(t: float) -> float:
        return sum(r for a, b, r in spans if a <= t < b)
    rate.spans = spans
    rate.notes = notes
    return rate


def reward_price(cfg: dict) -> Series | None:
    """Reward token price in USD: hourly median of its pool's swap prices, times the quote's USD price if the quote is
    not a dollar token. The median keeps one dust print from setting an hour's price."""
    if not cfg.get("price_venue"):
        return None
    s = load_swaps(cfg["price_venue"])
    px = s.p
    if cfg.get("quote_usd_bars"):
        from lib_cl import load_bars
        px = px * load_bars(cfg["quote_usd_bars"]).at(s.ts)
    elif cfg.get("quote_usd_feed"):
        from lib_cl import load_feed
        px = px * load_feed(cfg["quote_usd_feed"], cfg.get("quote_feed_decimals", 8)).at(s.ts)
    hour = (s.ts // 3600).astype(np.int64)
    uniq, start = np.unique(hour, return_index=True)
    end = np.append(start[1:], len(px))
    med = np.array([np.median(px[a:b]) for a, b in zip(start, end)])
    return Series((uniq + 1) * 3600.0, med)      # in force from the end of its hour


def load_rewards(venue: str):
    """(emission rate function, USD price series, config) for a venue, or (None, None, None)."""
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).parent / "params.json").read_text()).get("rewards", {}).get(venue)
    if not cfg:
        return None, None, None
    return emission_rate(cfg), reward_price(cfg), cfg


def usd_if_sold(t, tokens_cum, price: Series, sale_cost: float = 0.0) -> float:
    """Value of a cumulative token series when each week's accrual is sold at that week's last price, less the sale cost."""
    t = np.asarray(t, dtype=float)
    c = np.asarray(tokens_cum, dtype=float)
    if len(t) == 0 or price is None:
        return float("nan")
    week = (t // WEEK).astype(int)
    last = np.flatnonzero(np.diff(week, append=week[-1] + 1))
    got = np.diff(np.concatenate([[0.0], c[last]]))
    px = price.at(t[last])
    return float(np.nansum(got * px) * (1 - sale_cost))
