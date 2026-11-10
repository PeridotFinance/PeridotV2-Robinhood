"""Shared loaders and maths for the concentrated-liquidity P&L research (read-only, offline).

Units used everywhere below ("human units"):
  price P      USD (quote token, 6 dp) per one stock/base token (18 dp), from the pool's sqrtPriceX96
  liquidity L  raw liquidity / 1e12, so that for a range [Pa, Pb] and Pa <= P <= Pb
                   base  = L * (1/sqrt(P) - 1/sqrt(Pb))        quote = L * (sqrt(P) - sqrt(Pa))
               whichever token is token0 in the pool.
  flows        signed from the POOL's side (positive = the pool, i.e. its LPs, received the token).

The swap tables in data/ are the evidence; nothing here touches a network.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import gzip
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("CL_DATA", ROOT / "data"))
OUT = ROOT / "out"
CHARTS = ROOT / "charts"
Q96 = 2 ** 96
YEAR = 365.0 * 86400.0
ET = ZoneInfo("America/New_York")

# NYSE full-day closures inside the data (2026). Early closes (13:00 ET) are treated as normal days and said so in the report.
US_HOLIDAYS_2026 = {"2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"}


@dataclass(frozen=True)
class Venue:
    key: str
    label: str
    file: str              # swap table, relative to DATA
    base_is_token0: bool   # is the stock/base token the pool's token0?
    block_time_s: float    # mean block interval of the chain (for the fee-adjusted forecast)
    equity: bool           # True: US-equity sessions apply; False: 24/7 asset
    protocol_share: float | None  # None = per-swap column in the table
    feed: str | None = None       # oracle table, relative to DATA
    feed_decimals: int = 8
    lp_fee_ppm: float | None = None  # set when the table's fee column is LP fee + protocol fee (Uniswap v4): LP share = lp_fee / fee
    spacing: int = 1
    quote_decimals: int = 6          # base tokens are 18 dp everywhere in this study


VENUES = {
    "v4_nvda": Venue("v4_nvda", "Uniswap v4 NVDA/USDG (0.30%)", "robinhood/v4_nvda_swaps.csv.gz", False, 0.10, True, None,
                     "robinhood/feed_nvda.csv.gz", lp_fee_ppm=3000.0, spacing=60),
    "ramses_nvda": Venue("ramses_nvda", "Ramses NVDA/USDG (CL10, dynamic fee)", "robinhood/ramses_nvda_swaps.csv.gz", False, 0.10, True, None,
                         "robinhood/feed_nvda.csv.gz", spacing=10),
    "ramses_spy": Venue("ramses_spy", "Ramses SPY/USDG (CL1, dynamic fee)", "robinhood/ramses_spy_swaps.csv.gz", True, 0.10, True, None,
                        "robinhood/feed_spy.csv.gz", spacing=1),
    "v4_spy": Venue("v4_spy", "Uniswap v4 SPY/USDG (0.30%)", "robinhood/v4_spy_swaps.csv.gz", True, 0.10, True, None,
                    "robinhood/feed_spy.csv.gz", lp_fee_ppm=3000.0, spacing=60),
    "univ3_nvda": Venue("univ3_nvda", "Uniswap V3 NVDA/USDG (0.30%)", "robinhood/univ3_nvda_swaps.csv.gz", False, 0.10, True, None,
                        "robinhood/feed_nvda.csv.gz", spacing=60),
    "univ3_spy": Venue("univ3_spy", "Uniswap V3 SPY/USDG (0.30%)", "robinhood/univ3_spy_swaps.csv.gz", True, 0.10, True, None,
                       "robinhood/feed_spy.csv.gz", spacing=60),
    "univ3_nvda_005": Venue("univ3_nvda_005", "Uniswap V3 NVDA/USDG (0.05%)", "robinhood/univ3_nvda_005_swaps.csv.gz", False, 0.10, True, None,
                            "robinhood/feed_nvda.csv.gz", spacing=10),
    "univ3_spy_005": Venue("univ3_spy_005", "Uniswap V3 SPY/USDG (0.05%)", "robinhood/univ3_spy_005_swaps.csv.gz", True, 0.10, True, None,
                           "robinhood/feed_spy.csv.gz", spacing=10),
    # Pharaoh: the pool's protocol share is 1,000,000 ppm (data/facts/pharaoh-wavax-usdc-lp-fee-check.json): LPs earn no swap fee.
    "pharaoh_wavax": Venue("pharaoh_wavax", "Pharaoh WAVAX/USDC (CL10, dynamic fee)", "avalanche/pharaoh_wavax_usdc_swaps.csv.gz", True, 1.0, False, 1.0,
                           "avalanche/feed_avax_usd.csv.gz", spacing=10),
    # reward-token price pools (quote is not USD: converted with the quote's USD series where used)
    "ram_weth": Venue("ram_weth", "Ramses RAM/WETH (CL100)", "robinhood/ram_pool_swaps.csv.gz", False, 0.10, False, None, None, spacing=100,
                      quote_decimals=18),
    "phar_wavax": Venue("phar_wavax", "Pharaoh PHAR/WAVAX (CL5)", "avalanche/phar_wavax_cl_swaps.csv.gz", True, 1.0, False, 1.0, None, spacing=5,
                        quote_decimals=18),
}


# --------------------------------------------------------------------------------------------- sessions
def session_of(ts: float, equity: bool = True) -> str:
    """US-equity session of a unix time: regular 09:30-16:00 ET; extended 04:00-09:30 and 16:00-20:00 ET;
    overnight 20:00-04:00 ET on trading nights (Sunday 20:00 to Friday 04:00); weekend from Friday 20:00 to Sunday 20:00 ET.
    A full-day exchange holiday counts as weekend (the primary market is shut). For a 24/7 asset the same clock is
    reported so the columns line up, but nothing closes."""
    t = dt.datetime.fromtimestamp(ts, tz=ET)
    wd, hm = t.weekday(), t.hour * 60 + t.minute
    day = t.strftime("%Y-%m-%d")
    if wd == 5 or (wd == 6 and hm < 20 * 60) or (wd == 4 and hm >= 20 * 60):
        return "weekend"
    if equity and day in US_HOLIDAYS_2026 and 4 * 60 <= hm < 20 * 60:
        return "weekend"
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "regular"
    if 4 * 60 <= hm < 9 * 60 + 30 or 16 * 60 <= hm < 20 * 60:
        return "extended"
    return "overnight"


SESSIONS = ("regular", "extended", "overnight", "weekend")
SESSION_HOURS_PER_WEEK = {"regular": 32.5, "extended": 47.5, "overnight": 40.0, "weekend": 48.0}


def session_codes(ts: np.ndarray, equity: bool = True) -> np.ndarray:
    """Vectorised session code (index into SESSIONS) per timestamp; classification is constant within a minute."""
    minute = (ts // 60).astype(np.int64)
    uniq, inv = np.unique(minute, return_inverse=True)
    codes = np.array([SESSIONS.index(session_of(float(m) * 60 + 30, equity)) for m in uniq], dtype=np.int8)
    return codes[inv]


# ---------------------------------------------------------------------------------------------- loaders
@dataclass
class Swaps:
    venue: Venue
    block: np.ndarray
    ts: np.ndarray        # unix seconds
    p: np.ndarray         # pool price after the swap, USD per base
    p_prev: np.ndarray    # pool price before the swap (= previous swap's price after)
    L_prev: np.ndarray    # active liquidity the swap traded against (previous swap's post-swap liquidity), human units
    L: np.ndarray         # active liquidity after the swap, human units
    base_in: np.ndarray   # base tokens INTO the pool (signed), gross of the fee
    usd_in: np.ndarray    # quote INTO the pool (signed), gross of the fee
    fee_ppm: np.ndarray
    proto: np.ndarray     # protocol share of the fee in force
    tick: np.ndarray

    def __len__(self):
        return len(self.ts)

    # fee taken from the input leg, in USD at the swap's own execution price
    @property
    def fee_usd(self) -> np.ndarray:
        base_leg = np.where(self.base_in > 0, self.base_in, 0.0) * self.exec_price
        usd_leg = np.where(self.usd_in > 0, self.usd_in, 0.0)
        return (base_leg + usd_leg) * self.fee_ppm / 1e6

    @property
    def lp_fee_usd(self) -> np.ndarray:
        return self.fee_usd * (1.0 - self.proto)

    @property
    def exec_price(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            px = np.abs(self.usd_in / self.base_in)
        return np.where(np.isfinite(px) & (px > 0), px, self.p)

    @property
    def notional(self) -> np.ndarray:
        return np.abs(self.usd_in)

    def net_flows(self) -> tuple[np.ndarray, np.ndarray]:
        """(base_in, usd_in) with the WHOLE swap fee removed from the input leg: what the liquidity itself traded.
        The fee is reported separately (LP part and protocol part), so it is never counted twice."""
        f = self.fee_ppm / 1e6
        b = np.where(self.base_in > 0, self.base_in * (1 - f), self.base_in)
        u = np.where(self.usd_in > 0, self.usd_in * (1 - f), self.usd_in)
        return b, u


def load_swaps(key: str, t_from: float | None = None, t_to: float | None = None, max_jump_ticks: int = 1500) -> Swaps:
    v = VENUES[key]
    cols: dict[str, list] = {k: [] for k in ("block", "ts", "a0", "a1", "sq", "L", "tick", "fee", "proto")}
    with gzip.open(DATA / v.file, "rt") as f:
        r = csv.reader(f)
        head = next(r)
        ix = {n: i for i, n in enumerate(head)}
        has_proto = "protocol_share" in ix
        for row in r:
            cols["block"].append(int(row[ix["block"]]))
            cols["ts"].append(float(row[ix["ts"]]))
            cols["a0"].append(float(int(row[ix["amount0"]])))
            cols["a1"].append(float(int(row[ix["amount1"]])))
            cols["sq"].append(int(row[ix["sqrt_price_x96"]]) / Q96)
            cols["L"].append(int(row[ix["liquidity"]]) / 1e12)
            cols["tick"].append(int(row[ix["tick"]]))
            cols["fee"].append(float(row[ix["fee_ppm"]]))
            cols["proto"].append(float(row[ix["protocol_share"]]) if has_proto else float("nan"))
    a = {k: np.array(x) for k, x in cols.items()}
    raw_price = a["sq"] ** 2                       # token1 raw per token0 raw
    scale = 10.0 ** (18 - v.quote_decimals)
    p = raw_price * scale if v.base_is_token0 else scale / raw_price
    base = (a["a0"] if v.base_is_token0 else a["a1"]) / 1e18
    usd = (a["a1"] if v.base_is_token0 else a["a0"]) / 10.0 ** v.quote_decimals
    a["L"] = a["L"] * 1e12 / 10.0 ** ((18 + v.quote_decimals) / 2)   # loaded as raw / 1e12; correct for the quote's decimals
    if v.lp_fee_ppm is not None:      # fee column = what the swapper paid (LP fee + protocol fee on top)
        proto = 1.0 - np.minimum(v.lp_fee_ppm / np.maximum(a["fee"], 1e-9), 1.0)
    elif v.protocol_share is None:
        proto = a["proto"]
    else:
        proto = np.full(len(p), v.protocol_share)
    # A dust swap can push a pool with no liquidity in range to an extreme tick for a while (seen on thin pools). Such
    # jumps are not prices anyone could trade size at; drop swaps more than max_jump_ticks from the last accepted tick.
    ok = glitch_mask(a["tick"], max_jump_ticks)
    p, base, usd, proto = p[ok], base[ok], usd[ok], proto[ok]
    a = {k: x[ok] for k, x in a.items()}
    # the state a swap traded against is the state the previous ACCEPTED swap left behind
    p_prev = np.concatenate([[p[0]], p[:-1]])
    L_prev = np.concatenate([[a["L"][0]], a["L"][:-1]])
    keep = np.ones(len(p), dtype=bool)
    if t_from is not None:
        keep &= a["ts"] >= t_from
    if t_to is not None:
        keep &= a["ts"] <= t_to
    s = Swaps(v, a["block"][keep], a["ts"][keep], p[keep], p_prev[keep], L_prev[keep], a["L"][keep], base[keep], usd[keep], a["fee"][keep],
              proto[keep], a["tick"][keep])
    s.dropped_glitches = int((~ok).sum())
    return s


def glitch_mask(ticks, max_jump_ticks: int = 1500, confirm: int = 5) -> np.ndarray:
    """True for swaps to keep. A swap whose tick is more than ``max_jump_ticks`` from the last accepted tick is dropped as
    a dust print into an empty range, unless ``confirm`` consecutive swaps agree on the new level (then the level is real:
    the pool moved, and the run is accepted from its first swap)."""
    n = len(ticks)
    ok = np.ones(n, dtype=bool)
    last = ticks[0]
    run: list[int] = []
    for i in range(n):
        t = ticks[i]
        if abs(t - last) <= max_jump_ticks:
            last = t
            run = []
            continue
        ok[i] = False
        if run and abs(t - ticks[run[-1]]) > max_jump_ticks:
            run = []
        run.append(i)
        if len(run) >= confirm:
            ok[run] = True
            last = t
            run = []
    return ok


class Series:
    """Step series (time -> value), value in force from its timestamp until the next."""

    def __init__(self, t, v):
        self.t = np.asarray(t, dtype=float)
        self.v = np.asarray(v, dtype=float)

    def at(self, ts):
        i = np.searchsorted(self.t, ts, side="right") - 1
        return np.where(i >= 0, self.v[np.clip(i, 0, len(self.v) - 1)], np.nan)

    def age(self, ts):
        i = np.searchsorted(self.t, ts, side="right") - 1
        return np.where(i >= 0, np.asarray(ts) - self.t[np.clip(i, 0, len(self.t) - 1)], np.inf)


def load_feed(rel: str, decimals: int = 8, min_price: float = 1.0) -> Series:
    t, v = [], []
    files = [DATA / rel]
    pre = DATA / rel.replace(".csv.gz", "_pre27m.csv.gz")   # rounds before block 27,000,000 were pulled into their own file
    if pre.exists():
        files.insert(0, pre)
    for path in files:
        with gzip.open(path, "rt") as f:
            for row in csv.DictReader(f):
                px = int(row["answer"]) / 10 ** decimals
                if px > min_price and px < 1e7:  # the first rounds of a feed can be seed values
                    t.append(float(row["updated_at"]))
                    v.append(px)
    o = np.argsort(t, kind="stable")
    return Series(np.array(t)[o], np.array(v)[o])


def load_bars(rel: str) -> Series:
    """External reference bars (ts = bar open, close used as the price in force from bar close)."""
    t, v = [], []
    with gzip.open(DATA / rel, "rt") as f:
        r = csv.DictReader(f)
        tk = "ts" if "ts" in r.fieldnames else "open_time"
        for row in r:
            if row["close"] in ("", "null", "None"):
                continue
            tt = float(row[tk])
            if tt > 1e12:
                tt /= 1000.0
            t.append(tt)
            v.append(float(row["close"]))
    o = np.argsort(t, kind="stable")
    return Series(np.array(t)[o], np.array(v)[o])


# ------------------------------------------------------------------------------------------- pool maths
def pool_price_at(s: Swaps, ts: np.ndarray) -> np.ndarray:
    """Pool mid price in force at each time: the price after the last swap at or before it."""
    i = np.searchsorted(s.ts, ts, side="right") - 1
    return s.p[np.clip(i, 0, len(s.p) - 1)]


def liquidity_per_usd(p: float | np.ndarray, half_width_ticks: float) -> float | np.ndarray:
    """Liquidity (human units) of a $1 position centred on price p with a symmetric range of +/- half_width_ticks."""
    r = 1.0001 ** half_width_ticks
    return 1.0 / (2.0 * np.sqrt(p) * (1.0 - r ** -0.5))


def markout(s: Swaps, mark: np.ndarray) -> np.ndarray:
    """Per-swap P&L of the liquidity (fee excluded) when the tokens it received and paid are valued at ``mark``."""
    b, u = s.net_flows()
    return b * mark + u


def realised_vol(ts: np.ndarray, p: np.ndarray, t0: float, t1: float, step: float = 300.0) -> float:
    """Annualised realised volatility of the price sampled every ``step`` seconds on [t0, t1]."""
    grid = np.arange(t0, t1 + 1e-9, step)
    if len(grid) < 3:
        return float("nan")
    i = np.searchsorted(ts, grid, side="right") - 1
    if i.min() < 0:
        i = np.clip(i, 0, None)
    x = np.log(p[i])
    r = np.diff(x)
    return float(np.sqrt(np.sum(r * r) / (grid[-1] - grid[0]) * YEAR))


def p_trade(sigma_annual: float, fee_fraction: float, block_time_s: float) -> float:
    """Probability that an arbitrageur trades in a block (Milionis, Moallemi, Roughgarden 2023, fast-block regime):
    P_trade ~ 1 / (1 + gamma / (sigma * sqrt(dt / 2))), gamma the fee (log units), sigma per sqrt-second, dt the block time."""
    if not sigma_annual or sigma_annual != sigma_annual:
        return float("nan")
    sig = sigma_annual / math.sqrt(YEAR)
    return 1.0 / (1.0 + fee_fraction / (sig * math.sqrt(block_time_s / 2.0)))


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def day_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def dump(obj, name: str) -> Path:
    OUT.mkdir(exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, indent=1, default=float))
    return p
