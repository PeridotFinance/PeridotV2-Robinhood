"""Research extensions of the vendored range-policy engine (vendor/engine.py is not edited).

Adds, by subclassing:
  * a start split other than 50/50 (to replay a live pair from its real deposit mix);
  * the position's stock holding at every sample (its delta), which the hedged-mode study needs;
  * a session plan: a different half-width and trigger by time of week (session-aware allocation);
  * loading the committed swap tables (data/*.csv.gz) into the engine's Dataset.

The ledger is the engine's: one equity number in USD built from idle inventory, the position's tokens at the pool
price, accrued fees net of the protocol share and the reserve cut, and gas; hold = the starting tokens left untouched;
LVR is reported only as the shortfall against the delta-matched rebalancing portfolio, never subtracted again.
"""
from __future__ import annotations

import bisect
import csv
import dataclasses
import datetime as dt
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
import engine as E  # noqa: E402

from lib_cl import DATA, ET, VENUES, glitch_mask, session_of  # noqa: E402

Q96 = 2 ** 96


def load_dataset(key: str, feed_series=None, ram_rate=None, max_jump_ticks: int = 1500) -> E.Dataset:
    """Swap table -> engine Dataset (raw pool units, as the engine expects). ``feed_series`` = (times, prices)."""
    v = VENUES[key]
    cols = {k: [] for k in ("ts", "s", "tick", "L", "in_tok", "in_amt", "fee", "proto")}
    with gzip.open(DATA / v.file, "rt") as f:
        r = csv.reader(f)
        ix = {n: i for i, n in enumerate(next(r))}
        has_proto = "protocol_share" in ix
        for row in r:
            a0, a1 = int(row[ix["amount0"]]), int(row[ix["amount1"]])
            tok = 0 if a0 > 0 else 1
            cols["ts"].append(float(row[ix["ts"]]))
            cols["s"].append(int(row[ix["sqrt_price_x96"]]) / Q96)
            cols["tick"].append(int(row[ix["tick"]]))
            cols["L"].append(float(int(row[ix["liquidity"]])))
            cols["in_tok"].append(tok)
            cols["in_amt"].append(float(a0 if tok == 0 else a1))
            fee_ = float(row[ix["fee_ppm"]])
            if v.lp_fee_ppm is not None:
                cols["proto"].append(1.0 - min(v.lp_fee_ppm / max(fee_, 1e-9), 1.0))
            elif has_proto and v.protocol_share is None:
                cols["proto"].append(float(row[ix["protocol_share"]]))
            else:
                cols["proto"].append(v.protocol_share or 0.0)
            cols["fee"].append(fee_)
    # dust prints into an empty range are not tradable prices (same rule as lib_cl.load_swaps)
    keep = glitch_mask(cols["tick"], max_jump_ticks)
    cols = {k: [x for x, m in zip(c, keep) if m] for k, c in cols.items()}
    # timestamps are interpolated per block; several swaps in one block share a time, keep the order of the table
    ts = cols["ts"]
    for i in range(1, len(ts)):
        if ts[i] < ts[i - 1]:
            ts[i] = ts[i - 1]
    if feed_series is None:
        from lib_cl import load_feed
        fs = load_feed(v.feed, v.feed_decimals)
        feed_series = (list(fs.t), list(fs.v))
    return E.Dataset(ts, cols["s"], cols["tick"], cols["L"], cols["in_tok"], cols["in_amt"], cols["fee"], list(feed_series[0]),
                     list(feed_series[1]), E.Market(v.base_is_token0), ram_rate=ram_rate, proto=cols["proto"])


def engine_session(ts: int, equity: bool = True) -> str:
    return session_of(ts, equity)


def weekend_plan(narrow: tuple[int, int], wide: tuple[int, int], pre_close_min: int = 30, post_open_min: int = 30):
    """(half, trigger) by time: ``wide`` from ``pre_close_min`` before Friday's 16:00 ET close until ``post_open_min`` after
    Monday's 09:30 ET open, ``narrow`` otherwise. The switch itself is an ordinary recentre and obeys every gate."""
    def plan(t: float) -> tuple[int, int]:
        d = dt.datetime.fromtimestamp(t, tz=ET)
        wd, hm = d.weekday(), d.hour * 60 + d.minute
        if (wd == 4 and hm >= 16 * 60 - pre_close_min) or wd in (5, 6) or (wd == 0 and hm < 9 * 60 + 30 + post_open_min):
            return wide
        return narrow
    return plan


def session_plan(by_session: dict[str, tuple[int, int]], equity: bool = True):
    """(half, trigger) by session label (regular / extended / overnight / weekend)."""
    def plan(t: float) -> tuple[int, int]:
        return by_session[session_of(t, equity)]
    return plan


class Sim(E.Sim):
    def __init__(self, ds, cfg, session_fn=engine_session, start_index=0, end_index=None, start_stock_frac: float = 0.5, plan=None):
        super().__init__(ds, dataclasses.replace(cfg), session_fn, start_index, end_index)
        self.start_stock_frac = start_stock_frac
        self.plan = plan
        self._first_targets = True
        self._force = False
        self.q_series: list = []      # stock tokens held (position + idle + accrued fees) at each sample
        self.ram_series: list = []    # reward tokens accrued so far at each sample
        self.dep_series: list = []    # stock tokens inside the ranged position at each sample (the part that changes with price)
        self.plan_switches = 0
        if plan is not None:
            half, trig = plan(ds.ts[start_index])
            self.cfg.core_half, self.cfg.trigger_ticks = half, trig

    # -- start split: run() calls _targets() right after it has set the 50/50 start and before the first deploy
    def _targets(self, price_o):
        if self._first_targets:
            self._first_targets = False
            if abs(self.start_stock_frac - 0.5) > 1e-12:
                m = self.m
                stock_raw = (self.idle0 if m.stock_is_token0 else self.idle1)
                usdg_raw = (self.idle1 if m.stock_is_token0 else self.idle0)
                f = self.start_stock_frac
                stock_raw, usdg_raw = stock_raw * (f / 0.5), usdg_raw * ((1 - f) / 0.5)
                self.idle0, self.idle1 = m.split_raw(stock_raw, usdg_raw)
                self.hold0, self.hold1 = self.idle0, self.idle1
        return super()._targets(price_o)

    # -- delta recording: run() calls equity() exactly once per sample, in order
    def equity(self, s, price):
        a0, a1 = self.holdings(s)
        self.q_series.append((a0 if self.m.stock_is_token0 else a1) / 1e18)
        self.ram_series.append(self.ram)
        d0 = d1 = 0.0
        for ly in self.layers.values():
            if ly.L > 0:
                x0, x1 = E.amounts(ly.L, s, ly.lo, ly.hi)
                d0 += x0; d1 += x1
        self.dep_series.append((d0 if self.m.stock_is_token0 else d1) / 1e18)
        return self.m.value(a0, a1, price) + self.ram * self.cfg.ram_usd - self.gas

    # -- session plan: a change of the planned width is a due recentre like any other
    def _try_recenter(self, t, s_pool):
        if self.plan is not None:
            half, trig = self.plan(t)
            if half != self.cfg.core_half or trig != self.cfg.trigger_ticks:
                # only switch when the keeper could act now; otherwise keep the old plan in force and try again later
                self._pending = (half, trig)
                self._force = True
            if self._force:
                old = (self.cfg.core_half, self.cfg.trigger_ticks)
                self.cfg.core_half, self.cfg.trigger_ticks = self._pending
                n0 = self.recenters["core"]
                super()._try_recenter(t, s_pool)
                if self.recenters["core"] > n0 or self.layers["core"].L == 0:
                    self._force = False
                    self.plan_switches += 1
                else:
                    self.cfg.core_half, self.cfg.trigger_ticks = old
                return
        super()._try_recenter(t, s_pool)

    def _due(self, t, tick_o, price_o):
        due = super()._due(t, tick_o, price_o)
        if self._force and "core" not in due and self.layers["core"].L > 0:
            due = ["core"] + due
        return due


def single_range_config(half: int, trigger: int, capital: float, spacing: int, **kw) -> E.Config:
    """One oracle-centred range under the contract's rules (no swap at recentre)."""
    base = dict(name=f"h{half}_t{trigger}", capital_usd=capital, core_half=half, wide_half=0, core_weight=1.0, trigger_ticks=trigger,
                cooldown=3600.0, max_per_day=24, max_stale=12 * 3600.0, gate_bps=150.0, loss_bps=10.0, gas_usd=0.0663, protocol_share=None,
                ram_usd=0.0, sample_s=300.0, spacing=spacing, block_sessions=("weekend",), rebalance=False, dilute=True, poll_s=300.0,
                reserve_bps=2000)
    base.update(kw)
    return E.Config(**base)


def run_window(ds: E.Dataset, cfg: E.Config, t_from: float | None = None, t_to: float | None = None, equity_sessions: bool = True,
               start_stock_frac: float = 0.5, plan=None, keep_series: bool = False, reward_price=None, reward_sale_cost: float = 0.0) -> dict:
    i0 = 0 if t_from is None else bisect.bisect_left(ds.ts, t_from)
    i1 = len(ds.ts) if t_to is None else bisect.bisect_right(ds.ts, t_to)
    sim = Sim(ds, cfg, (lambda t: session_of(t, equity_sessions)), i0, i1, start_stock_frac, plan)
    sim.run()
    # mark the end at the oracle when it is fresh there, else at the pool (both legs of every comparison use the same price)
    p_o, age = sim.oracle_at(sim.end_ts)
    fresh = p_o is not None and age <= cfg.max_stale
    r = sim.result(p_o if fresh else None)
    m = sim.m
    price_end = p_o if fresh else m.usd_per_stock(sim.s_end)
    a0, a1 = sim.holdings(sim.s_end)
    eq = m.value(a0, a1, price_end) - sim.gas
    hold = m.value(sim.hold0, sim.hold1, price_end)
    r.update({"end_mark": "oracle" if fresh else "pool", "price_end_mark": price_end, "equity_end_mark": eq, "hold_end_mark": hold,
              "excess_mark_pct": (eq - hold) / sim.equity0 * 100, "plan_switches": sim.plan_switches,
              "fee_over_lvr": (r["fees_usd"] / r["lvr_usd"]) if r["lvr_usd"] > 0 else None})
    # identity check of the decomposition: (equity - start) = bench - LVR + fees - gas (+ RAM), to rounding
    resid = (r["equity_end"] - sim.equity0) - (r["bench_pnl"] - r["lvr_usd"] + r["fees_usd"] - r["gas_usd"] + sim.ram * cfg.ram_usd)
    r["decomposition_residual_usd"] = resid
    assert abs(resid) < 1e-6 * max(1.0, cfg.capital_usd), resid
    if ds.ram_rate is not None:
        from rewards import usd_if_sold
        n = len(sim.series)
        r["reward_tokens"] = sim.ram
        r["reward_usd_if_sold"] = usd_if_sold([x[0] for x in sim.series], sim.ram_series[:n], reward_price, reward_sale_cost) if reward_price is not None else None
        r["reward_pct_if_sold"] = None if r["reward_usd_if_sold"] is None else 100 * r["reward_usd_if_sold"] / sim.equity0
    if keep_series:
        r["series"] = sim.series
        r["q_series"] = sim.q_series[:len(sim.series)]
        r["ram_series"] = sim.ram_series[:len(sim.series)]
        r["dep_series"] = sim.dep_series[:len(sim.series)]
        r["equity0"] = sim.equity0
        r["hold_tokens"] = ((sim.hold0 if m.stock_is_token0 else sim.hold1) / 1e18, (sim.hold1 if m.stock_is_token0 else sim.hold0) / 1e6)
        r["recenter_log"] = sim.recenter_log
    return r
