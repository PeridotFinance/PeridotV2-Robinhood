"""Dual-range concentrated-liquidity backtest engine (handoff 01 section 6d; shared risk model section 3).

Pure Python, deterministic, read-only. It replays one pool's swap history together with the oracle series and
simulates the CONTRACT policy that exists in ramses-vaults/src/libraries/RamsesRangeLib.sol, extended to two layers:

  * the keeper evaluates on every swap and oracle update;
  * ranges are centred on the ORACLE tick (the vault recentres on ``TickMath.getTickAtSqrtPrice(ref)``);
  * a recenter removes the layer, adds everything to idle, and redeploys MATCHED value (min of the two idle sides at
    oracle prices, capped at the layer's share). There is NO swap: the unmatched remainder stays idle. This is the
    behaviour of ``RamsesRangeLib.deploy`` (matched = min(stockIdleValue, usdgIdleValue, cap));
  * recentering is blocked unless the feed is fresh (age <= maxStaleness, session open) and the pool is within the
    allocation gate of the oracle; cooldown and a rolling per-24h budget apply; a deploy loss bound is checked at the
    oracle reference price.

Everything is expressed in raw pool units (token0/token1) and marked in USD at an explicit price. Fee entitlement is
the fork-measured one: the pool keeps ``protocol_share`` of every swap fee (treasury), the rest is paid pro rata to the
in-range liquidity. RAM is tracked in tokens and valued separately (zero by default).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

LN = math.log(1.0001)
Q = 1e12  # decimals difference 18 - 6 between stock and USDG


# ------------------------------------------------------------------------------------------------ geometry
def tick_sqrt(tick: float) -> float:
    return math.exp(tick * LN / 2.0)


def sqrt_tick(s: float) -> float:
    return 2.0 * math.log(s) / LN


def amounts(L: float, s: float, lo: int, hi: int) -> tuple[float, float]:
    sa, sb = tick_sqrt(lo), tick_sqrt(hi)
    sc = min(max(s, sa), sb)
    return L * (1.0 / sc - 1.0 / sb), L * (sc - sa)


def liquidity_for(a0: float, a1: float, s: float, lo: int, hi: int) -> float:
    sa, sb = tick_sqrt(lo), tick_sqrt(hi)
    if s <= sa:
        return a0 / (1.0 / sa - 1.0 / sb)
    if s >= sb:
        return a1 / (sb - sa)
    return min(a0 / (1.0 / s - 1.0 / sb), a1 / (s - sa))


# ------------------------------------------------------------------------------------------------ pool descriptor
@dataclass
class Market:
    """Token order of one pool. ``stock_is_token0``: SPY yes, GLD/NVDA no (USDG is token0)."""
    stock_is_token0: bool

    def usd_per_stock(self, s: float) -> float:
        p = s * s  # token1 raw per token0 raw
        return p * Q if self.stock_is_token0 else Q / p

    def sqrt_for_usd(self, price: float) -> float:
        p = price / Q if self.stock_is_token0 else Q / price
        return math.sqrt(p)

    def tick_for_usd(self, price: float) -> float:
        return sqrt_tick(self.sqrt_for_usd(price))

    def value(self, a0: float, a1: float, price: float) -> float:
        """USD value of raw (token0, token1) with the stock at ``price`` and USDG at $1."""
        if self.stock_is_token0:
            return a0 * price / 1e18 + a1 / 1e6
        return a1 * price / 1e18 + a0 / 1e6

    def split_raw(self, stock_raw: float, usdg_raw: float) -> tuple[float, float]:
        return (stock_raw, usdg_raw) if self.stock_is_token0 else (usdg_raw, stock_raw)

    def stock_usd_to_raw(self, usd: float, price: float) -> float:
        return usd / price * 1e18

    @staticmethod
    def usdg_usd_to_raw(usd: float) -> float:
        return usd * 1e6


# ------------------------------------------------------------------------------------------------ inputs
@dataclass
class Dataset:
    """Columns of one pool's swap history (all python lists, oldest first) plus the oracle series."""
    ts: list            # seconds
    s_after: list       # sqrt price after the swap (float, sqrt(token1/token0 raw))
    tick_after: list
    L_after: list       # third-party active liquidity after the swap (raw)
    in_tok: list        # 0 or 1: the token the swapper paid in
    in_amt: list        # raw amount paid in (gross, includes fee)
    fee_ppm: list       # fee in force for this swap
    oracle_t: list      # feed updatedAt
    oracle_p: list      # USD per stock
    market: Market
    sessions: list = field(default_factory=list)  # optional precomputed session label per oracle-time grid (unused)
    ram_rate: object = None  # callable t -> RAM tokens per second for this gauge (or None)
    proto: list = None       # protocol share in force per swap (used when Config.protocol_share is None)

    def __post_init__(self):
        n = len(self.ts)
        assert n == len(self.s_after) == len(self.tick_after) == len(self.L_after) == len(self.in_tok) == len(self.in_amt) == len(self.fee_ppm)
        assert all(self.ts[i] <= self.ts[i + 1] for i in range(n - 1)), "swaps must be time ordered"
        assert all(self.oracle_t[i] <= self.oracle_t[i + 1] for i in range(len(self.oracle_t) - 1))


# ------------------------------------------------------------------------------------------------ config
@dataclass
class Config:
    name: str = "dual"
    capital_usd: float = 10_000.0
    core_half: int = 100            # ticks; 0 disables the core
    wide_half: int = 490            # ticks; 0 disables the backup
    core_weight: float = 0.70
    full_range: bool = False        # single full-range position (never recentres)
    core_trigger_frac: float = 0.70
    wide_trigger_frac: float = 0.60
    wide_drift_mult: float = 2.0    # drift trigger = mult * core trigger ticks
    wide_drift_dwell: float = 6 * 3600.0
    cooldown: float = 900.0
    max_per_day: int = 24
    max_stale: float = 12 * 3600.0
    gate_bps: float = 150.0         # allocation gate: |pool - oracle| must be within this to (re)deploy
    loss_bps: float = 10.0          # per-recenter loss bound at the oracle reference
    gas_usd: float = 0.40           # per layer recenter
    protocol_share: float | None = 0.50  # fork-measured: pool keeps 50% of every swap fee (-> treasury); None = per-swap history
    ram_usd: float = 0.0            # value of one RAM token (0 = count RAM at zero)
    sample_s: float = 300.0
    spacing: int = 1
    block_sessions: tuple = ("weekend", "holiday")
    dilute: bool = True             # our liquidity dilutes the third-party share (fixed observed volume)
    min_tick: int = -887272
    max_tick: int = 887272


@dataclass
class Layer:
    lo: int = 0
    hi: int = 0
    L: float = 0.0
    center: float = 0.0
    last: float = -1e18
    drift_since: float | None = None


def align(tick: float, spacing: int) -> int:
    return int(math.floor(tick / spacing)) * spacing


def new_range(center_tick: float, half: int, spacing: int) -> tuple[int, int]:
    """Same shape as RamsesRangeLib.newRange: exactly 2*half + spacing ticks containing the centre."""
    c = align(center_tick, spacing)
    return c - half, c + half + spacing


# ------------------------------------------------------------------------------------------------ simulator
class Sim:
    def __init__(self, ds: Dataset, cfg: Config, session_of, start_index: int = 0, end_index: int | None = None):
        self.ds, self.cfg, self.session_of = ds, cfg, session_of
        self.m = ds.market
        self.i0 = start_index
        self.i1 = len(ds.ts) if end_index is None else end_index
        self.layers: dict[str, Layer] = {"core": Layer(), "wide": Layer()}
        self.idle0 = self.idle1 = 0.0       # raw idle
        self.fee0 = self.fee1 = 0.0         # uncollected (accrued) LP fees, raw
        self.fees_usd_cum = 0.0
        self.ram = 0.0
        self.gas = 0.0
        self.recenters = {"core": 0, "wide": 0}
        self.blocked = {"stale": 0, "session": 0, "gate": 0, "cooldown": 0, "budget": 0, "loss": 0}
        self.recent: list = []
        self.in_range_s = {"core": 0.0, "wide": 0.0}
        self.tracked_s = 0.0
        self.series: list = []  # (t, equity, hold, bench)
        self.bench_pnl = 0.0
        self.recenter_log: list = []

    # ---- oracle ---------------------------------------------------------------------------------------
    def oracle_at(self, t: float):
        ds = self.ds
        i = bisect.bisect_right(ds.oracle_t, t) - 1
        if i < 0:
            return None, None
        return ds.oracle_p[i], t - ds.oracle_t[i]

    def oracle_fresh(self, t: float):
        p, age = self.oracle_at(t)
        if p is None:
            return None
        if age > self.cfg.max_stale:
            return None
        if self.session_of(int(t)) in self.cfg.block_sessions:
            return None
        return p

    # ---- valuation ------------------------------------------------------------------------------------
    def holdings(self, s: float):
        a0, a1 = self.idle0 + self.fee0, self.idle1 + self.fee1
        for ly in self.layers.values():
            if ly.L > 0:
                x0, x1 = amounts(ly.L, s, ly.lo, ly.hi)
                a0 += x0
                a1 += x1
        return a0, a1

    def equity(self, s: float, price: float) -> float:
        a0, a1 = self.holdings(s)
        return self.m.value(a0, a1, price) + self.ram * self.cfg.ram_usd - self.gas

    # ---- deployment -----------------------------------------------------------------------------------
    def _deploy(self, name: str, s_pool: float, price_o: float, cap_usd_per_side: float, s_ref: float):
        """Deploy matched value for one layer at the oracle-centred range; returns False if the loss bound trips."""
        c, ly = self.cfg, self.layers[name]
        half = c.core_half if name == "core" else c.wide_half
        if c.full_range:
            lo, hi = align(c.min_tick, c.spacing) + c.spacing, align(c.max_tick, c.spacing) - c.spacing
        else:
            lo, hi = new_range(self.m.tick_for_usd(price_o), half, c.spacing)
        stock_idle = self.idle0 if self.m.stock_is_token0 else self.idle1
        usdg_idle = self.idle1 if self.m.stock_is_token0 else self.idle0
        v_stock = stock_idle / 1e18 * price_o
        v_usdg = usdg_idle / 1e6
        matched = min(v_stock, v_usdg, cap_usd_per_side)
        if matched <= 0:
            return True
        a0, a1 = self.m.split_raw(self.m.stock_usd_to_raw(matched, price_o), self.m.usdg_usd_to_raw(matched))
        L = liquidity_for(a0, a1, s_pool, lo, hi)
        if L <= 0:
            return True
        u0, u1 = amounts(L, s_pool, lo, hi)
        self.idle0 -= u0
        self.idle1 -= u1
        ly.lo, ly.hi, ly.L = lo, hi, L
        ly.center = self.m.tick_for_usd(price_o)
        return True

    def _remove(self, name: str, s_pool: float):
        ly = self.layers[name]
        if ly.L > 0:
            x0, x1 = amounts(ly.L, s_pool, ly.lo, ly.hi)
            self.idle0 += x0
            self.idle1 += x1
            ly.L = 0.0
        # collecting the position pays out its accrued fees
        self.idle0 += self.fee0
        self.idle1 += self.fee1
        self.fee0 = self.fee1 = 0.0

    def _targets(self, price_o: float):
        """Per-side USD cap of each layer = its weight share of half the pair equity at oracle prices."""
        c = self.cfg
        if c.full_range:
            w = {"core": 1.0, "wide": 0.0}
        elif c.core_half == 0:
            w = {"core": 0.0, "wide": 1.0}
        elif c.wide_half == 0:
            w = {"core": 1.0, "wide": 0.0}
        else:
            w = {"core": c.core_weight, "wide": 1.0 - c.core_weight}
        return w

    def _total_equity_oracle(self, s_ref: float, price_o: float) -> float:
        a0, a1 = self.holdings(s_ref)
        return self.m.value(a0, a1, price_o)

    def _recenter(self, names, t: float, s_pool: float, price_o: float) -> bool:
        c = self.cfg
        s_ref = self.m.sqrt_for_usd(price_o)
        before = self._total_equity_oracle(s_ref, price_o)
        snap = (self.idle0, self.idle1, self.fee0, self.fee1, {k: (v.lo, v.hi, v.L, v.center) for k, v in self.layers.items()})
        for n in names:
            self._remove(n, s_pool)
        w = self._targets(price_o)
        half_equity = before / 2.0
        for n in names:
            if w[n] > 0:
                self._deploy(n, s_pool, price_o, w[n] * half_equity, s_ref)
        after = self._total_equity_oracle(s_ref, price_o)
        if after + before * c.loss_bps / 1e4 < before:
            self.idle0, self.idle1, self.fee0, self.fee1 = snap[0], snap[1], snap[2], snap[3]
            for k, (lo, hi, L, ctr) in snap[4].items():
                v = self.layers[k]
                v.lo, v.hi, v.L, v.center = lo, hi, L, ctr
            self.blocked["loss"] += 1
            return False
        for n in names:
            self.layers[n].last = t
            self.layers[n].drift_since = None
            self.recenters[n] += 1
            self.gas += c.gas_usd
            self.recent.append(t)
        self.recenter_log.append((t, tuple(names), price_o))
        return True

    # ---- policy -----------------------------------------------------------------------------------------
    def _idle_matched_usd(self, price_o: float) -> float:
        stock_idle = self.idle0 if self.m.stock_is_token0 else self.idle1
        usdg_idle = self.idle1 if self.m.stock_is_token0 else self.idle0
        return min(stock_idle / 1e18 * price_o, usdg_idle / 1e6)

    def _due(self, t: float, tick_o: float, price_o: float):
        """Layers whose trigger fires. An undeployed layer (nothing could be paired) is due again only when matched
        idle value exists, so a one-sided inventory does not burn gas on empty recenters."""
        c = self.cfg
        due = []
        if c.full_range:
            return due
        core, wide = self.layers["core"], self.layers["wide"]
        matched_ok = self._idle_matched_usd(price_o) >= max(5.0, 0.0005 * c.capital_usd)
        if c.core_half:
            trig = max(c.core_trigger_frac * c.core_half, c.spacing)
            if core.L > 0:
                if abs(tick_o - core.center) >= trig or not (core.lo < tick_o < core.hi):
                    due.append("core")
            elif matched_ok:
                due.append("core")
        if c.wide_half:
            trig = max(c.wide_trigger_frac * c.wide_half, c.spacing)
            if wide.L > 0:
                hit = abs(tick_o - wide.center) >= trig or not (wide.lo < tick_o < wide.hi)
                if not hit and c.core_half and core.L > 0:
                    drift_trig = c.wide_drift_mult * max(c.core_trigger_frac * c.core_half, c.spacing)
                    if abs(core.center - wide.center) > drift_trig:
                        if wide.drift_since is None:
                            wide.drift_since = t
                        elif t - wide.drift_since >= c.wide_drift_dwell:
                            hit = True
                    else:
                        wide.drift_since = None
                if hit:
                    due.append("wide")
            elif matched_ok:
                due.append("wide")
        return due

    def _try_recenter(self, t: float, s_pool: float):
        c = self.cfg
        price_o = self.oracle_fresh(t)
        if price_o is None:
            # distinguish only for reporting; due-evaluation needs a price anyway
            return
        tick_o = self.m.tick_for_usd(price_o)
        due = self._due(t, tick_o, price_o)
        if not due:
            return
        # pool within the allocation gate of the oracle (relative price difference)
        p_pool = self.m.usd_per_stock(s_pool)
        if abs(p_pool / price_o - 1.0) * 1e4 > c.gate_bps:
            self.blocked["gate"] += 1
            return
        ready = [n for n in due if t - self.layers[n].last >= c.cooldown]
        if not ready:
            self.blocked["cooldown"] += 1
            return
        cutoff = t - 86400.0
        self.recent = [x for x in self.recent if x > cutoff]
        if len(self.recent) + len(ready) > c.max_per_day:
            # budget exhausted: take as many as fit, core first
            room = c.max_per_day - len(self.recent)
            if room <= 0:
                self.blocked["budget"] += 1
                return
            ready = ready[:room]
        self._recenter(ready, t, s_pool, price_o)

    # ---- fees -------------------------------------------------------------------------------------------
    def _accrue_swap(self, sa: float, sb: float, L_tp: float, in_tok: int, fee_ppm: float, proto: float):
        """Accrue our share of the swap that moved the price from sa to sb (third-party liquidity L_tp)."""
        if sa == sb or L_tp <= 0:
            return
        lo_s, hi_s = (sa, sb) if sa < sb else (sb, sa)
        layers = [ly for ly in self.layers.values() if ly.L > 0 and tick_sqrt(ly.hi) > lo_s and tick_sqrt(ly.lo) < hi_s]
        if not layers:
            return
        c = self.cfg
        rate = fee_ppm / 1e6 * (1.0 - (proto if c.protocol_share is None else c.protocol_share))
        pts = {lo_s, hi_s}
        for ly in layers:
            for e in (tick_sqrt(ly.lo), tick_sqrt(ly.hi)):
                if lo_s < e < hi_s:
                    pts.add(e)
        pts = sorted(pts)
        total = 0.0
        for a, b in zip(pts, pts[1:]):
            mid = 0.5 * (a + b)
            Lo = sum(ly.L for ly in layers if tick_sqrt(ly.lo) <= mid < tick_sqrt(ly.hi))
            if Lo <= 0:
                continue
            # third-party input volume in this segment, then our share of it
            vol = L_tp * ((1.0 / a - 1.0 / b) if in_tok == 0 else (b - a))
            share = Lo / (L_tp + Lo) if c.dilute else Lo / L_tp
            total += vol * share
        if total:
            fee_raw = total * rate
            if in_tok == 0:
                self.fee0 += fee_raw
                self.fees_usd_cum += self.m.value(fee_raw, 0.0, self.m.usd_per_stock(sb))
            else:
                self.fee1 += fee_raw
                self.fees_usd_cum += self.m.value(0.0, fee_raw, self.m.usd_per_stock(sb))

    # ---- run --------------------------------------------------------------------------------------------
    def run(self):
        ds, c = self.ds, self.cfg
        i0, i1 = self.i0, self.i1
        t0 = ds.ts[i0]
        # initial pool state = the state after the swap before the window start (or the first swap itself)
        s_prev = ds.s_after[i0 - 1] if i0 > 0 else ds.s_after[i0]
        L_prev = ds.L_after[i0 - 1] if i0 > 0 else ds.L_after[i0]
        # find the first fresh oracle moment for the start
        t = t0
        price_o = self.oracle_fresh(t)
        if price_o is None:
            # start at the first swap where the oracle is fresh
            for j in range(i0, i1):
                price_o = self.oracle_fresh(ds.ts[j])
                if price_o is not None:
                    i0 = j
                    t0 = ds.ts[j]
                    s_prev = ds.s_after[j - 1] if j > 0 else ds.s_after[j]
                    L_prev = ds.L_after[j - 1] if j > 0 else ds.L_after[j]
                    break
            else:
                raise RuntimeError("no fresh oracle in window")
        self.start_ts = t0
        p_pool0 = self.m.usd_per_stock(s_prev)
        half = c.capital_usd / 2.0
        self.idle0, self.idle1 = self.m.split_raw(self.m.stock_usd_to_raw(half, p_pool0), self.m.usdg_usd_to_raw(half))
        self.hold0, self.hold1 = self.idle0, self.idle1
        self.equity0 = self.m.value(self.idle0, self.idle1, p_pool0)
        w = self._targets(price_o)
        s_ref = self.m.sqrt_for_usd(price_o)
        for n in ("core", "wide"):
            if w[n] > 0:
                self._deploy(n, s_prev, price_o, w[n] * half, s_ref)
        next_sample = t0
        prev_q = None
        prev_price = p_pool0
        ts, s_after, tick_after, L_after = ds.ts, ds.s_after, ds.tick_after, ds.L_after
        in_tok, fee_ppm = ds.in_tok, ds.fee_ppm
        ram_rate = ds.ram_rate
        o_t = ds.oracle_t
        oi = bisect.bisect_right(o_t, t0)
        t_prev = t0
        for j in range(i0, i1):
            tj = ts[j]
            # oracle updates that happened before this swap re-trigger policy evaluation
            while oi < len(o_t) and o_t[oi] <= tj:
                self._try_recenter(o_t[oi], s_prev)
                oi += 1
            # time between swaps: reward accrual and in-range accounting at the previous price
            dt = tj - t_prev
            if dt > 0:
                tick_prev = sqrt_tick(s_prev)
                tracked = False
                Lo_in = 0.0
                for nm, ly in self.layers.items():
                    if ly.L > 0:
                        tracked = True
                        if ly.lo <= tick_prev < ly.hi:
                            self.in_range_s[nm] += dt
                            Lo_in += ly.L
                if tracked:
                    self.tracked_s += dt
                if Lo_in > 0 and ram_rate is not None:
                    r = ram_rate(t_prev)
                    if r:
                        self.ram += r * dt * Lo_in / (L_prev + Lo_in)
            # samples (equity, hold, delta-matched benchmark) on a fixed grid, at the price BEFORE this swap
            while next_sample <= tj:
                price = self.m.usd_per_stock(s_prev)
                a0, a1 = self.holdings(s_prev)
                q_stock = (a0 if self.m.stock_is_token0 else a1) / 1e18
                if prev_q is not None:
                    self.bench_pnl += prev_q * (price - prev_price)
                prev_q, prev_price = q_stock, price
                hold = self.m.value(self.hold0, self.hold1, price)
                self.series.append((next_sample, self.equity(s_prev, price), hold, self.bench_pnl))
                next_sample += c.sample_s
            # apply the swap
            sb = s_after[j]
            self._accrue_swap(s_prev, sb, L_after[j], in_tok[j], fee_ppm[j], ds.proto[j] if ds.proto is not None else 0.5)
            s_prev, L_prev, t_prev = sb, L_after[j], tj
            self._try_recenter(tj, s_prev)
        self.end_ts = t_prev
        self.s_end = s_prev
        return self

    # ---- results ----------------------------------------------------------------------------------------
    def result(self, price_end_oracle: float | None = None):
        c, m = self.cfg, self.m
        price = m.usd_per_stock(self.s_end)
        a0, a1 = self.holdings(self.s_end)
        fees_usd = self.fees_usd_cum
        eq = self.equity(self.s_end, price)
        hold = m.value(self.hold0, self.hold1, price)
        days = (self.end_ts - self.start_ts) / 86400.0
        # fees accrued but already moved to idle by collects are not tracked separately; keep a running total instead
        res = {
            "name": c.name, "capital": c.capital_usd, "days": days,
            "equity_end": eq, "hold_end": hold, "pnl": eq - self.equity0, "pnl_vs_hold": eq - hold,
            "ret_pct": (eq / self.equity0 - 1) * 100, "hold_ret_pct": (hold / self.equity0 - 1) * 100,
            "excess_pct": (eq - hold) / self.equity0 * 100,
            "excess_apr_pct": (eq - hold) / self.equity0 * 100 * 365.0 / days,
            "recenters_core": self.recenters["core"], "recenters_wide": self.recenters["wide"],
            "recenters_per_day": (self.recenters["core"] + self.recenters["wide"]) / days,
            "fees_usd": fees_usd, "gas_usd": self.gas, "ram_tokens": self.ram, "blocked": dict(self.blocked),
            "in_range_core": self.in_range_s["core"] / self.tracked_s if self.tracked_s else 0.0,
            "in_range_wide": self.in_range_s["wide"] / self.tracked_s if self.tracked_s else 0.0,
        }
        if price_end_oracle:
            res["equity_end_oracle"] = m.value(a0, a1, price_end_oracle) + self.ram * c.ram_usd - self.gas
        return res
