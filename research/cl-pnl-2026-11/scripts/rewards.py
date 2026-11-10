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
    """Reward token price in USD from its deepest pool's swaps (hourly last price), via the quote's USD feed if needed."""
    if not cfg.get("price_venue"):
        return None
    s = load_swaps(cfg["price_venue"])
    px = s.p
    if cfg.get("quote_usd_feed"):
        from lib_cl import load_feed
        px = px * load_feed(cfg["quote_usd_feed"], cfg.get("quote_feed_decimals", 8)).at(s.ts)
    hour = (s.ts // 3600).astype(np.int64)
    last = np.flatnonzero(np.diff(hour, append=hour[-1] + 1))
    return Series(s.ts[last], px[last])
