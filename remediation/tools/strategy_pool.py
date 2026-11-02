"""Replay of a pool's event stream: active liquidity, our fee share, depth and capital placement.

Everything is computed from decoded events; no state reads. Human units: stock tokens, USD (USDG = $1),
liquidity in raw/1e12 (so amounts follow the usual x = L(1/sqrtP - 1/sqrtPb) with sqrtP in USD per stock).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from evidence_lib import EvidenceError, amounts_for_liquidity, usd_per_stock
from strategy_data import Trade
from strategy_ledger import PositionBook

RAW_TO_HUMAN_L = 1e-12  # 10 ** -((18 + 6) / 2)


# --------------------------------------------------------------------------------------- tick helpers


def tick_to_usd_price(tick: int, stock_is_token0: bool) -> float:
    p_raw = 1.0001 ** tick
    return p_raw * 1e12 if stock_is_token0 else 1e12 / p_raw


def range_overlap_fraction(t0: int, t1: int, lower: int, upper: int) -> float:
    """Share of the tick path t0 -> t1 that lies inside [lower, upper). A swap that does not move counts as
    fully inside when the tick is in range."""
    if t0 == t1:
        return 1.0 if lower <= t0 < upper else 0.0
    lo, hi = min(t0, t1), max(t0, t1)
    inter = min(hi, upper) - max(lo, lower)
    return max(0.0, inter) / (hi - lo)


# --------------------------------------------------------------------------------------------- v4 replay


@dataclass
class SwapRecord:
    block: int
    idx: int
    ts: float
    tick_before: int
    tick_after: int
    liq_before_raw: int
    our_liq_raw: int          # our liquidity that is in range at tick_before (raw)
    our_overlap: float        # fraction of the swap path inside our range (liquidity-weighted)
    fee_usd: float            # total swap fee in USD
    price: float
    notional: float
    sqrt_after: float = 0.0   # sqrtPriceX96 / 2**96 after the swap (raw token1/token0 units)
    positions: tuple = ()     # ((lower, upper, liq_raw), ...) our open positions AFTER the swap
    kind: str = "swap"        # "swap" or "position" (one of our positions changed; no trade)


def replay_v4(init: dict, swaps: list[Trade], raw_swaps: list, mods: list[dict], our_ids: set[int],
              record_from_block: int = 0) -> tuple[list[SwapRecord], PositionBook]:
    """Walk swaps and ModifyLiquidity events in order. ``swaps`` and ``raw_swaps`` are parallel.

    The whole history is replayed so the active liquidity is exact; only swaps at or after
    ``record_from_block`` are returned."""
    if len(swaps) != len(raw_swaps):
        raise EvidenceError("swap/raw length mismatch")
    stream = [((t.block, t.idx), 0, i) for i, t in enumerate(swaps)] + \
             [((m["block"], m["idx"]), 1, j) for j, m in enumerate(mods)]
    stream.sort()
    tick = init["tick"]
    last_sqrt = init["sqrt"] / (1 << 96)
    liq = 0
    book = PositionBook()
    out: list[SwapRecord] = []
    for _, kind, i in stream:
        if kind == 1:
            m = mods[i]
            if m["lower"] <= tick < m["upper"]:
                liq += m["delta"]
            if m["salt"] in our_ids:
                book.apply(m)
                if m["block"] >= record_from_block:
                    out.append(SwapRecord(m["block"], m["idx"], 0.0, tick, tick, liq, 0, 0.0, 0.0, 0.0, 0.0, last_sqrt,
                                          tuple((p.lower, p.upper, p.liq) for p in book.live()), kind="position"))
            continue
        t = swaps[i]
        liq_after_raw = raw_swaps[i][4]
        ours_in = [p for p in book.live() if p.lower <= tick < p.upper]
        our_liq = sum(p.liq for p in ours_in)
        # liquidity-weighted overlap of the swap path with each of our ranges
        overlap_weighted = 0.0
        if book.live():
            denom = sum(p.liq for p in book.live())
            overlap_weighted = sum(p.liq * range_overlap_fraction(tick, t.tick, p.lower, p.upper) for p in book.live()) / denom
        if t.block >= record_from_block:
            out.append(SwapRecord(t.block, t.idx, t.ts, tick, t.tick, liq, our_liq, overlap_weighted, t.fee_usd, t.price,
                                  t.notional, raw_swaps[i][3] / (1 << 96),
                                  tuple((p.lower, p.upper, p.liq) for p in book.live())))
        tick, liq, last_sqrt = t.tick, liq_after_raw, raw_swaps[i][3] / (1 << 96)
    return out, book


def fee_share(rec: SwapRecord, scale: float = 1.0) -> float:
    """Fraction of the swap's LP fee that our liquidity (scaled by ``scale``) would earn."""
    if rec.our_liq_raw <= 0 or rec.liq_before_raw <= 0:
        return 0.0
    others = max(rec.liq_before_raw - rec.our_liq_raw, 0)
    mine = rec.our_liq_raw * scale
    return rec.our_overlap * mine / (others + mine)


# -------------------------------------------------------------------------------------- depth / volume


def depth_usd(price: float, liq_human: float, move: float = 0.01) -> tuple[float, float]:
    """USD (up) and USD-equivalent (down) needed to move the price by ``move`` against constant liquidity."""
    if liq_human <= 0 or price <= 0:
        return 0.0, 0.0
    sp = math.sqrt(price)
    up = liq_human * (math.sqrt(price * (1 + move)) - sp)                      # USD paid in to buy stock
    down_stock = liq_human * (1 / math.sqrt(price * (1 - move)) - 1 / sp)      # stock paid in to sell
    return up, down_stock * price


def position_value(p, sqrt_price_raw: float, stock_is_token0: bool, oracle_price: float) -> tuple[float, float, bool]:
    """(stock tokens, USD, in_range) for a position at a raw sqrt price (float of sqrtPriceX96 / 2**96)."""
    a0, a1 = amounts_for_liquidity(p.liq, sqrt_price_raw, p.lower, p.upper)
    stock_raw, usd_raw = (a0, a1) if stock_is_token0 else (a1, a0)
    tick_now = math.floor(math.log(sqrt_price_raw ** 2) / math.log(1.0001))
    return stock_raw / 1e18, usd_raw / 1e6, p.lower <= tick_now < p.upper
