"""Pure metric functions for the strategy evidence: returns, markouts, session splits, scenario arithmetic."""
from __future__ import annotations

import bisect
import math
from collections import defaultdict

from evidence_lib import EvidenceError, session_of
from strategy_data import Trade, Oracle

YEAR_S = 365.25 * 86400


def apr(simple_return: float, seconds: float) -> float:
    """Simple (non-compounded) annualised rate for a return earned over ``seconds``."""
    if seconds <= 0:
        raise EvidenceError("non-positive window")
    return simple_return * YEAR_S / seconds


def apy(simple_return: float, seconds: float) -> float:
    if seconds <= 0:
        raise EvidenceError("non-positive window")
    if simple_return <= -1:
        return -1.0
    return math.exp(math.log1p(simple_return) * YEAR_S / seconds) - 1.0


def twr(values: list[float]) -> float:
    """Time-weighted return of a per-share value series (flows do not move a per-share price)."""
    if len(values) < 2 or values[0] <= 0:
        raise EvidenceError("need at least two positive values")
    out = 1.0
    for a, b in zip(values, values[1:]):
        if a <= 0:
            raise EvidenceError("non-positive share value")
        out *= b / a
    return out - 1.0


def irr(flows: list[tuple[float, float]], end_time: float, end_value: float) -> float:
    """Money-weighted annualised return (IRR) of a depositor: ``flows`` = (time, amount contributed (+) /
    withdrawn (-)); the position is worth ``end_value`` at ``end_time``. Bisection on the annual rate."""
    if not flows or end_value < 0:
        raise EvidenceError("irr needs flows and a non-negative end value")

    def fv(rate: float) -> float:
        total = 0.0
        for t, cf in flows:
            total += cf * (1.0 + rate) ** ((end_time - t) / YEAR_S)
        return total - end_value

    lo, hi = -0.999999, 1000.0
    if fv(lo) * fv(hi) > 0:
        raise EvidenceError("irr is not bracketed")
    for _ in range(200):
        mid = (lo + hi) / 2
        if fv(lo) * fv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def lp_sign_and_size(t: Trade) -> tuple[float, float]:
    """(+1 if the LP bought stock / -1 if it sold, |stock size|)."""
    if t.stock_in == 0:
        return 0.0, 0.0
    return (1.0 if t.stock_in > 0 else -1.0), abs(t.stock_in)


def markouts(trades: list[Trade], horizons: tuple[int, ...], oracle: Oracle | None = None, lo_ts: float = 0, hi_ts: float = float("inf"),
             fee_fraction: float = 1.0) -> dict:
    """LP markouts, measured from the pool's MID price right after each trade (not the fee-inclusive execution price,
    which would count the fee twice).

    ``pool``: sign x (pool price at t+h - pool price after the trade) x stock size; the pool price only moves when someone
    trades, so a quiet pool scores zero. ``oracle``: sign x (oracle at t+h - oracle at t) x stock size, i.e. the information
    that arrived at the reference feed (frozen while the feed is stale). Positive = the LP gained after the trade; fees are
    reported separately. A trade is only scored if the full horizon lies inside the observed stream."""
    ts = [t.ts for t in trades]
    end_ts = ts[-1] if ts else 0.0
    out: dict = {}
    for h in horizons:
        rows: dict = defaultdict(lambda: {"trades": 0, "notional": 0.0, "pool_markout_usd": 0.0, "oracle_markout_usd": 0.0, "fee_usd": 0.0, "oracle_scored": 0})
        for i, t in enumerate(trades):
            if not (lo_ts <= t.ts <= hi_ts) or t.ts + h > end_ts:
                continue
            sign, size = lp_sign_and_size(t)
            if size == 0:
                continue
            j = bisect.bisect_right(ts, t.ts + h) - 1
            mark_pool = trades[max(j, i)].price
            row = rows[session_of(int(t.ts))]
            row["trades"] += 1
            row["notional"] += t.notional
            row["fee_usd"] += t.fee_usd * fee_fraction
            row["pool_markout_usd"] += sign * (mark_pool - t.price) * size
            if oracle is not None:
                row["oracle_markout_usd"] += sign * (oracle.price(t.ts + h) - oracle.price(t.ts)) * size
                row["oracle_scored"] += 1
        total = {k: sum(r[k] for r in rows.values()) for k in ("trades", "notional", "pool_markout_usd", "oracle_markout_usd", "fee_usd", "oracle_scored")}
        out[str(h)] = {"total": total, "bySession": {k: dict(v) for k, v in rows.items()}}
    return out


def bps(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else 1e4 * numerator / denominator


def liquidity_scenario(total: float, strategy_pct: float, loans_pct: float, cash_pct: float, redeem: float, strategy_returns: float | None = None) -> dict:
    """The 00 section 6a worked example as arithmetic: local cash first, then a bounded strategy withdrawal.

    ``strategy_returns`` caps what the strategy can pay (None = as much as asked). Insufficient output reverts
    the redemption atomically (balances unchanged). This is a scenario, not the live configuration."""
    if abs(strategy_pct + loans_pct + cash_pct - 1.0) > 1e-9:
        raise EvidenceError("percentages must sum to 1")
    strategy, loans, cash = total * strategy_pct, total * loans_pct, total * cash_pct
    from_cash = min(cash, redeem)
    shortfall = redeem - from_cash
    avail = strategy if strategy_returns is None else min(strategy, strategy_returns)
    if shortfall > avail + 1e-12:
        return {"status": "reverted", "reason": "strategy cannot return the shortfall", "before": {"strategy": strategy, "loans": loans, "cash": cash},
                "after": {"strategy": strategy, "loans": loans, "cash": cash}, "shortfall": shortfall, "strategyCanReturn": avail}
    return {"status": "paid", "fromCash": from_cash, "fromStrategy": shortfall, "before": {"strategy": strategy, "loans": loans, "cash": cash},
            "after": {"strategy": strategy - shortfall, "loans": loans, "cash": cash - from_cash},
            "newBorrowingBlocked": (cash - from_cash) < 0.1 * (total - redeem) - 1e-12}
