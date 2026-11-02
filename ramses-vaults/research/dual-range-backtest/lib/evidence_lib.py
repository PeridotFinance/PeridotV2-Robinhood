"""Read-only chain access, event decoding and CL math for the strategy evidence tools.

Design rules (handoff 08, section 3):
  * Read only. This module never signs and never sends a transaction.
  * A failed read is a failed run. Every RPC error that is not an explicit "split this window" case raises
    ``EvidenceError``; nothing is skipped or defaulted.
  * Historical logs are immutable, so complete windows ending at or before a pinned block are cached on disk.
    The public Robinhood RPC keeps only minutes of historical *state*, so the tools reconstruct history from
    logs (which it serves in full) and check the reconstruction against the live state at the chain head.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Sequence

from keccak import keccak_hex

PUBLIC_RPC = "https://rpc.mainnet.chain.robinhood.com"
CHAIN_ID = 4663
MAX_LOG_SPAN = 9_000_000  # the public RPC allows 10,000,000 blocks per eth_getLogs
Q96 = 1 << 96
TWO256 = 1 << 256

# --------------------------------------------------------------------------------------------- errors


class EvidenceError(RuntimeError):
    """A read failed. The run fails; nothing is skipped."""


class SplitWindow(Exception):
    pass


# ------------------------------------------------------------------------------------------ rpc client


READ_METHODS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getCode", "eth_call", "eth_getLogs",
    "eth_getTransactionReceipt", "eth_getTransactionByHash", "eth_getStorageAt", "eth_getBalance", "eth_getTransactionCount",
})


class Rpc:
    def __init__(self, url: str = PUBLIC_RPC, cache_dir: Path | None = None, min_interval: float = 0.22,
                 timeout: float = 90.0):
        self.url = url
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self.calls = 0
        self.cache_hits = 0
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- raw JSON-RPC with pacing and backoff on transport-level trouble ------------------------------
    def call(self, method: str, params: list, retries: int = 7):
        if method not in READ_METHODS:
            raise EvidenceError(f"RPC method is not on the read-only allowlist: {method}")
        self._id = getattr(self, "_id", 0) + 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        delay = 1.0
        last = "unknown"
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.calls += 1
            req = urllib.request.Request(self.url, data=body, headers={
                "content-type": "application/json", "user-agent": "curl/8.4.0"})
            try:
                payload = json.load(urllib.request.urlopen(req, timeout=self.timeout))
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code in (429, 502, 503, 504) and attempt < retries:
                    retry_after = e.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                    delay = min(delay * 2, 30)
                    continue
                raise EvidenceError(f"{method}: {last}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = type(e).__name__ + ":" + str(e)[:80]
                if attempt < retries:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise EvidenceError(f"{method}: {last}") from e
            if not isinstance(payload, dict) or payload.get("id") != self._id:
                raise EvidenceError(f"{method}: mismatched JSON-RPC response")
            if "error" in payload:
                raise _rpc_error(method, payload["error"])
            if "result" not in payload:
                raise EvidenceError(f"{method}: response has neither result nor error")
            return payload["result"]
        raise EvidenceError(f"{method}: gave up after retries ({last})")

    # -- convenience reads ----------------------------------------------------------------------------
    def head(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def block(self, number: int | str = "latest") -> dict:
        tag = number if isinstance(number, str) else hex(number)
        res = self.call("eth_getBlockByNumber", [tag, False])
        if res is None:
            raise EvidenceError(f"block {number} not found")
        return res

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = block if isinstance(block, str) else hex(block)
        return self.call("eth_call", [{"to": to, "data": data}, tag])

    def receipt(self, tx_hash: str) -> dict:
        res = self.call("eth_getTransactionReceipt", [tx_hash])
        if res is None:
            raise EvidenceError(f"receipt {tx_hash} not found")
        return res

    def tx(self, tx_hash: str) -> dict:
        res = self.call("eth_getTransactionByHash", [tx_hash])
        if res is None:
            raise EvidenceError(f"tx {tx_hash} not found")
        return res

    # -- logs -----------------------------------------------------------------------------------------
    def logs(self, address: str | None, topics: Sequence, frm: int, to: int, final_upto: int | None = None) -> list[dict]:
        """All matching logs in [frm, to], oldest first. Windows are split automatically.

        ``final_upto``: logs in blocks at or before this number are immutable and may be cached."""
        out: list[dict] = []
        cur = frm
        while cur <= to:
            hi = min(cur + MAX_LOG_SPAN - 1, to)
            out.extend(self._logs_window(address, topics, cur, hi, final_upto))
            cur = hi + 1
        out.sort(key=lambda l: (l["blockNumber"], l["logIndex"]))
        return out

    def _logs_window(self, address, topics, lo, hi, final_upto):
        key = None
        cacheable = self.cache_dir is not None and final_upto is not None and hi <= final_upto
        if cacheable:
            key = hashlib.sha1(json.dumps([address and address.lower(), topics, lo, hi]).encode()).hexdigest()
            path = self.cache_dir / f"logs-{key}.json"
            if path.exists():
                self.cache_hits += 1
                return json.loads(path.read_text())
        try:
            raw = self._logs_raw(address, topics, lo, hi)
        except SplitWindow:
            if hi == lo:
                raise EvidenceError(f"single block {lo} returned too many logs for {address}")
            mid = (lo + hi) // 2
            raw = self._logs_window(address, topics, lo, mid, final_upto) + \
                self._logs_window(address, topics, mid + 1, hi, final_upto)
            if cacheable:
                (self.cache_dir / f"logs-{key}.json").write_text(json.dumps(raw))
            return raw
        if cacheable:
            (self.cache_dir / f"logs-{key}.json").write_text(json.dumps(raw))
        return raw

    def _logs_raw(self, address, topics, lo, hi):
        flt: dict = {"fromBlock": hex(lo), "toBlock": hex(hi), "topics": list(topics)}
        if address:
            flt["address"] = address
        try:
            res = self.call("eth_getLogs", [flt])
        except EvidenceError as e:
            msg = str(e).lower()
            if any(s in msg for s in ("more than", "limit", "exceed", "too many", "range", "response size", "timeout", "timed out")):
                raise SplitWindow() from e
            raise
        return [normalize_log(l) for l in res]


def _rpc_error(method: str, err: dict) -> EvidenceError:
    return EvidenceError(f"{method}: rpc error {err.get('code')}: {str(err.get('message'))[:200]}")


def normalize_log(l: dict) -> dict:
    return {
        "address": l["address"].lower(),
        "blockNumber": int(l["blockNumber"], 16),
        "ts": int(l["blockTimestamp"], 16) if l.get("blockTimestamp") else None,
        "logIndex": int(l["logIndex"], 16),
        "tx": l["transactionHash"],
        "topics": l["topics"],
        "data": l["data"],
    }


# ---------------------------------------------------------------------------------------- abi helpers


def words(data: str) -> list[int]:
    h = data[2:] if data.startswith("0x") else data
    if len(h) % 64:
        raise EvidenceError(f"log data length {len(h)} is not a multiple of 32 bytes")
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def s256(x: int) -> int:
    return x - TWO256 if x >= 1 << 255 else x


def sN(x: int, bits: int) -> int:
    """Interpret the low ``bits`` bits of a 256-bit word as a signed integer."""
    x &= (1 << bits) - 1
    return x - (1 << bits) if x >= 1 << (bits - 1) else x


def topic_addr(t: str) -> str:
    return "0x" + t[-40:].lower()


def topic_int(t: str, bits: int = 256) -> int:
    return sN(int(t, 16), bits)


def addr_topic(a: str) -> str:
    return "0x" + a.lower().replace("0x", "").rjust(64, "0")


def keccak_topic(signature: str) -> str:
    """Event topic0 / function selector source: keccak256 of the canonical signature."""
    return keccak_hex(signature)


def call_data(signature: str, *args: int) -> str:
    """Selector plus uint256/int256-encoded static args (enough for the getters used here)."""
    sel = keccak_topic(signature)[:10]
    return sel + "".join((a % TWO256).to_bytes(32, "big").hex() for a in args)


def decode_words(res: str) -> list[int]:
    return words(res)


# ------------------------------------------------------------------------------------------ time index


class TimeIndex:
    """Block number -> unix time by linear interpolation between real headers on a regular grid.

    The node reports ``blockTimestamp = 0`` on logs and rejects batched requests, so timestamps come from
    headers. ``step`` blocks apart (default 10,000, about seventeen minutes) keeps the interpolation error to a few
    seconds; ``measure_error`` quantifies it on random exact headers and the figure goes into the report."""

    def __init__(self, rpc: Rpc, step: int = 10_000, path: Path | None = None):
        self.rpc, self.step = rpc, step
        self.path = Path(path) if path else None
        self.grid: dict[int, tuple[int, int]] = {}  # block -> (timestamp, l1BlockNumber)
        if self.path and self.path.exists():
            self.grid = {int(k): tuple(v) for k, v in json.loads(self.path.read_text()).items()}
        self._keys: list[int] | None = None

    def _save(self):
        if self.path:
            self.path.write_text(json.dumps({str(k): list(v) for k, v in self.grid.items()}))

    def header(self, n: int) -> tuple[int, int]:
        b = self.rpc.block(n)
        return int(b["timestamp"], 16), int(b.get("l1BlockNumber", "0x0"), 16)

    def ensure(self, lo: int, hi: int, progress=None):
        first = (lo // self.step) * self.step
        last = ((hi // self.step) + 1) * self.step
        todo = [n for n in range(first, last + 1, self.step) if n not in self.grid]
        for i, n in enumerate(todo):
            self.grid[n] = self.header(n)
            if i % 200 == 199:
                self._save()
                if progress:
                    progress(f"headers {i + 1}/{len(todo)}")
        if todo:
            self._save()
            self._keys = None

    def ts(self, n: int) -> float:
        import bisect
        if self._keys is None:
            self._keys = sorted(self.grid)
        i = bisect.bisect_right(self._keys, n) - 1
        if i < 0 or i + 1 >= len(self._keys):
            raise EvidenceError(f"block {n} outside the header grid; call ensure() first")
        n0, n1 = self._keys[i], self._keys[i + 1]
        t0, t1 = self.grid[n0][0], self.grid[n1][0]
        return t0 + (t1 - t0) * (n - n0) / (n1 - n0)

    def measure_error(self, lo: int, hi: int, samples: int = 30, seed: int = 7) -> dict:
        import random
        rnd = random.Random(seed)
        errs = sorted(abs(self.ts(n) - self.header(n)[0]) for n in (rnd.randrange(lo, hi) for _ in range(samples)))
        return {"samples": samples, "median_s": errs[len(errs) // 2], "p95_s": errs[int(len(errs) * 0.95)], "max_s": errs[-1]}


# -------------------------------------------------------------------------------------- CL / tick math


def tick_to_sqrt(tick: int) -> float:
    return math.pow(1.0001, tick / 2.0)


def sqrt_q96_to_float(sqrt_price_x96: int) -> float:
    return sqrt_price_x96 / Q96


def tick_from_sqrt(s: float) -> int:
    return math.floor(2.0 * math.log(s) / math.log(1.0001))


def amounts_for_liquidity(liq: float, s: float, tick_lower: int, tick_upper: int) -> tuple[float, float]:
    """Raw token0/token1 amounts held by liquidity ``liq`` in [lower, upper) at sqrt price ``s``."""
    sa, sb = tick_to_sqrt(tick_lower), tick_to_sqrt(tick_upper)
    sc = min(max(s, sa), sb)
    return liq * (1.0 / sc - 1.0 / sb), liq * (sc - sa)


def liquidity_for_amounts(amount0: float, amount1: float, s: float, tick_lower: int, tick_upper: int) -> float:
    """Largest liquidity whose required amounts do not exceed (amount0, amount1) at sqrt price ``s``."""
    sa, sb = tick_to_sqrt(tick_lower), tick_to_sqrt(tick_upper)
    if s <= sa:
        return amount0 / (1.0 / sa - 1.0 / sb)
    if s >= sb:
        return amount1 / (sb - sa)
    l0 = amount0 / (1.0 / s - 1.0 / sb)
    l1 = amount1 / (s - sa)
    return min(l0, l1)


def usd_per_stock(sqrt_price_x96: int, stock_is_token0: bool, stock_dec: int = 18, usd_dec: int = 6) -> float:
    """Pool price of the stock in USD (USDG = $1), from sqrtPriceX96 of a v3/v4-style pool."""
    p = (sqrt_price_x96 / Q96) ** 2  # token1 raw per token0 raw
    if stock_is_token0:
        return p * 10 ** (stock_dec - usd_dec)
    return 10 ** (stock_dec - usd_dec) / p


def tick_for_usd_price(price: float, stock_is_token0: bool, stock_dec: int = 18, usd_dec: int = 6) -> int:
    """Pool tick whose price equals ``price`` USD per stock (floating, not snapped to spacing)."""
    scale = 10 ** (stock_dec - usd_dec)
    p = price / scale if stock_is_token0 else scale / price
    return math.floor(math.log(p) / math.log(1.0001))


def snap_down(tick: int, spacing: int) -> int:
    return (tick // spacing) * spacing


# --------------------------------------------------------------------------------------- time helpers

try:  # US equity sessions are defined in Eastern Time
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

# NYSE full-day closures that fall inside plausible evidence windows (extend as needed)
# local copy: Labor Day 2026-09-07 added (the original list started at Thanksgiving) so the Aug-Nov backtest window is classified
US_HOLIDAYS = {"2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15"}


def session_of(ts: int) -> str:
    """'regular' (09:30-16:00 ET), 'overnight' (rest of the 24/5 week), 'weekend', or 'holiday'."""
    if _ET is None:
        raise EvidenceError("zoneinfo with America/New_York is required for session classification")
    import datetime as dt
    t = dt.datetime.fromtimestamp(ts, tz=_ET)
    wd, hm = t.weekday(), t.hour * 60 + t.minute
    if t.strftime("%Y-%m-%d") in US_HOLIDAYS:
        return "holiday"
    # Robinhood 24/5: Sunday 20:00 ET to Friday 20:00 ET
    if wd == 5 or (wd == 6 and hm < 20 * 60) or (wd == 4 and hm >= 20 * 60):
        return "weekend"
    if wd <= 4 and 9 * 60 + 30 <= hm < 16 * 60:
        return "regular"
    return "overnight"


def iso(ts: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def chunked(seq: Sequence, n: int) -> Iterable[Sequence]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def default_cache_dir() -> Path:
    return Path(os.environ.get("STRATEGY_EVIDENCE_CACHE", Path(__file__).resolve().parents[1] / "evidence" / ".strategy-cache"))
