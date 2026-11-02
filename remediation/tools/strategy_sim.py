"""Single-position range-policy replay over a pool's swap stream (handoff 08 Phase 1 comparisons, Phase 2 base).

The vault's behaviour modelled here: one range centred on the ORACLE price, recentred when the oracle moved
``trigger`` ticks (or the pool price left the range), at most ``max_per_day`` per rolling day with a cooldown,
never while the feed is stale; only matched value is deployed (the surplus of the larger side stays idle and
earns nothing); idle inventory is redeployed hourly; fees arrive in the swap's input token and join idle.
Not modelled: the per-recenter loss bound (it only blocks), settlement swaps (paused in production), rewards.
All amounts are human units (stock tokens, USD); liquidity is in the same human units (raw / 1e12).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from evidence_lib import EvidenceError, snap_down, tick_for_usd_price
from strategy_data import Oracle, Trade
from strategy_pool import tick_to_usd_price


@dataclass
class Policy:
    half_width: int = 1200
    trigger: int = 300
    cooldown_s: int = 3600
    max_per_day: int = 4
    spacing: int = 60
    check_interval_s: int = 300
    rebalance_interval_s: int = 3600
    max_oracle_age_s: int = 12 * 3600
    min_deploy_usd: float = 0.01


@dataclass
class Costs:
    recenter_usd: float = 0.0
    rebalance_usd: float = 0.0


@dataclass
class Pos:
    pa: float
    pb: float
    liq: float
    center_tick: int


def pos_amounts(pos: Pos | None, price: float) -> tuple[float, float]:
    """(stock, usd) held by the position at a pool price."""
    if pos is None or pos.liq <= 0:
        return 0.0, 0.0
    pc = min(max(price, pos.pa), pos.pb)
    return pos.liq * (1 / math.sqrt(pc) - 1 / math.sqrt(pos.pb)), pos.liq * (math.sqrt(pc) - math.sqrt(pos.pa))


def liq_for(stock: float, usd: float, price: float, pa: float, pb: float) -> float:
    sa, sb, sp = math.sqrt(pa), math.sqrt(pb), math.sqrt(price)
    if price <= pa:
        return stock / (1 / sa - 1 / sb)
    if price >= pb:
        return usd / (sb - sa)
    return min(stock / (1 / sp - 1 / sb), usd / (sp - sa))


def make_range(oracle_price: float, policy: Policy, stock_is_token0: bool) -> tuple[float, float, int]:
    center = tick_for_usd_price(oracle_price, stock_is_token0)
    lower = snap_down(center - policy.half_width, policy.spacing)
    upper = snap_down(center + policy.half_width, policy.spacing) + policy.spacing
    p1, p2 = tick_to_usd_price(lower, stock_is_token0), tick_to_usd_price(upper, stock_is_token0)
    return min(p1, p2), max(p1, p2), center


def overlap_fraction(price_before: float, price_after: float, pos: Pos, input_is_usd: bool) -> float:
    """Share of the swap's input that traded while the price was inside the position's range."""
    if input_is_usd:  # price rises; input scales with sqrt(P)
        c0, c1, lo, hi = math.sqrt(price_before), math.sqrt(price_after), math.sqrt(pos.pa), math.sqrt(pos.pb)
    else:             # price falls; input scales with 1/sqrt(P)
        c0, c1, lo, hi = 1 / math.sqrt(price_before), 1 / math.sqrt(price_after), 1 / math.sqrt(pos.pb), 1 / math.sqrt(pos.pa)
    a, b = min(c0, c1), max(c0, c1)
    if b - a < 1e-18:
        return 1.0 if lo <= a <= hi else 0.0
    return max(0.0, min(b, hi) - max(a, lo)) / (b - a)


@dataclass
class Result:
    size_usd: float
    start_equity: float
    end_equity: float
    fees_gross_usd: float
    fees_net_usd: float
    gas_usd: float
    recenters: int
    rebalances: int
    blocked_stale: int
    blocked_limits: int
    time_in_range: float
    avg_liquidity: float
    avg_deployed_fraction: float
    hold_pnl: float
    pnl_before_fees_vs_hold: float
    net_vs_hold_usd: float
    seconds: float

    def asdict(self) -> dict:
        d = dict(self.__dict__)
        d["net_vs_hold_pct"] = 100 * self.net_vs_hold_usd / self.size_usd
        d["net_vs_hold_apr_pct"] = 100 * self.net_vs_hold_usd / self.size_usd * (365.25 * 86400) / self.seconds if self.seconds else None
        d["fees_gross_apr_pct"] = 100 * self.fees_gross_usd / self.size_usd * (365.25 * 86400) / self.seconds if self.seconds else None
        return d


def simulate(trades: list[Trade], liq_other: list[float], oracle: Oracle, size_usd: float, policy: Policy, stock_is_token0: bool,
             lp_fraction: float = 1.0, reserve_cut: float = 0.2, costs: Costs | None = None, start_stock_fraction: float = 0.5) -> Result:
    if len(trades) != len(liq_other) or len(trades) < 2:
        raise EvidenceError("need aligned trades and liquidity, at least two")
    costs = costs or Costs()
    t0 = trades[0].ts
    p_o0 = oracle.price(t0)
    stock, usd = size_usd * start_stock_fraction / p_o0, size_usd * (1 - start_stock_fraction)
    pool_price0 = trades[0].price
    pos: Pos | None = None
    fees_gross = gas = 0.0
    n_rec = n_reb = blocked_stale = blocked_limits = 0
    last_check = last_rb = t0
    recent: deque = deque()  # recenter timestamps (rolling day)
    last_recenter = -1e18
    in_range_s = 0.0
    liq_s = dep_s = 0.0  # time-weighted liquidity and deployed fraction of capital

    def deploy(price: float):
        nonlocal stock, usd, pos
        if pos is None:
            return
        v = min(stock * price, usd)
        if v < policy.min_deploy_usd:
            return
        add = liq_for(v / price, v, price, pos.pa, pos.pb)
        if add <= 0:
            return
        ax, ay = pos_amounts(Pos(pos.pa, pos.pb, add, pos.center_tick), price)
        stock, usd = stock - ax, usd - ay
        pos.liq += add

    pa, pb, c = make_range(p_o0, policy, stock_is_token0)
    pos = Pos(pa, pb, 0.0, c)
    deploy(pool_price0)
    start_equity = stock * p_o0 + usd + sum(a * b for a, b in zip(pos_amounts(pos, pool_price0), (p_o0, 1.0)))
    p_hold0 = p_o0
    h_stock, h_usd = size_usd * start_stock_fraction / p_o0, size_usd * (1 - start_stock_fraction)
    prev_price = pool_price0
    prev_ts = t0
    for i, tr in enumerate(trades):
        if pos is not None and pos.liq > 0 and pos.pa <= prev_price <= pos.pb:
            in_range_s += tr.ts - prev_ts
        if pos is not None:
            ax_, ay_ = pos_amounts(pos, prev_price)
            total_ = (stock + ax_) * prev_price + usd + ay_
            liq_s += pos.liq * (tr.ts - prev_ts)
            dep_s += ((ax_ * prev_price + ay_) / total_ if total_ > 0 else 0.0) * (tr.ts - prev_ts)
        if pos is not None and pos.liq > 0:
            f = overlap_fraction(prev_price, tr.price, pos, tr.input_is_usd)
            if f > 0:
                share = pos.liq / (liq_other[i] + pos.liq) if (liq_other[i] + pos.liq) > 0 else 0.0
                fee = tr.fee_usd * lp_fraction * f * share
                fees_gross += fee
                if tr.input_is_usd:
                    usd += fee
                else:
                    stock += fee / tr.exec_price
        prev_price, prev_ts = tr.price, tr.ts
        if tr.ts - last_check < policy.check_interval_s:
            continue
        last_check = tr.ts
        p_o = oracle.price(tr.ts)
        stale = oracle.age(tr.ts) > policy.max_oracle_age_s
        out_of_range = pos is not None and pos.liq > 0 and not (pos.pa <= tr.price <= pos.pb)
        moved = pos is not None and abs(tick_for_usd_price(p_o, stock_is_token0) - pos.center_tick) >= policy.trigger
        if (moved or out_of_range) and not stale:
            while recent and tr.ts - recent[0] > 86400:
                recent.popleft()
            if tr.ts - last_recenter >= policy.cooldown_s and len(recent) < policy.max_per_day:
                ax, ay = pos_amounts(pos, tr.price)
                stock, usd = stock + ax, usd + ay
                pa, pb, c = make_range(p_o, policy, stock_is_token0)
                pos = Pos(pa, pb, 0.0, c)
                deploy(tr.price)
                recent.append(tr.ts)
                last_recenter = tr.ts
                n_rec += 1
                gas += costs.recenter_usd
                last_rb = tr.ts
            else:
                blocked_limits += 1
        elif (moved or out_of_range) and stale:
            blocked_stale += 1
        if tr.ts - last_rb >= policy.rebalance_interval_s:
            before = pos.liq if pos else 0.0
            deploy(tr.price)
            if pos and pos.liq > before:
                n_reb += 1
                gas += costs.rebalance_usd
            last_rb = tr.ts
    p_end_o, p_end_pool = oracle.price(trades[-1].ts), trades[-1].price
    ax, ay = pos_amounts(pos, p_end_pool)
    end_equity = (stock + ax) * p_end_o + usd + ay
    seconds = trades[-1].ts - t0
    hold_pnl = h_stock * (p_end_o - p_hold0)
    fees_net = fees_gross * (1 - reserve_cut)
    # fees are already inside end_equity (credited to idle); remove them to isolate inventory effects
    pnl_before_fees = (end_equity - fees_gross) - start_equity - hold_pnl
    net_vs_hold = (end_equity - fees_gross * reserve_cut) - start_equity - hold_pnl - gas
    return Result(size_usd, start_equity, end_equity, fees_gross, fees_net, gas, n_rec, n_reb, blocked_stale, blocked_limits,
                  in_range_s / seconds if seconds else 0.0, liq_s / seconds if seconds else 0.0, dep_s / seconds if seconds else 0.0,
                  hold_pnl, pnl_before_fees, net_vs_hold, seconds)
