"""Shared setup for the Robinhood-chain data pull (read-only; Python 3 stdlib only).

Reuses the repo's read-only JSON-RPC client (``remediation/tools/evidence_lib.py``: method allowlist, adaptive
eth_getLogs windows, on-disk cache). Nothing here signs or sends a transaction. All writes go under
``research/cl-pnl-2026-11/data/`` (datasets in ``data/robinhood``, caches in ``data/.cache``).
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # no __pycache__ in the shared tools directory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # research/cl-pnl-2026-11
REPO = ROOT.parents[1]                      # the worktree
TOOLS = REPO / "remediation" / "tools"
sys.path.insert(0, str(TOOLS))

from evidence_lib import (EvidenceError, Rpc, TimeIndex, addr_topic, call_data, keccak_topic, s256, sN,  # noqa: E402
                          topic_addr, topic_int, words)

DATA = ROOT / "data" / "robinhood"
CACHE = ROOT / "data" / ".cache" / "robinhood"
RAW = CACHE / "raw"                          # raw logs per dataset (JSON), re-usable between scripts
STATE = DATA / "state"                       # small JSON facts that cannot be re-read later (state at END)
for _d in (DATA, CACHE, RAW, STATE):
    _d.mkdir(parents=True, exist_ok=True)

HEADERS_CACHE = CACHE / "headers.json"       # our own copy of the 10,000-block header grid
END_FILE = STATE / "END.json"
DISCOVERY_FILE = STATE / "discovery.json"
END_STATE_FILE = STATE / "end_state.json"

# read-only inputs that already exist on this machine (never modified)
PERIDOT = Path("/Users/joshua/Peridot")
EXISTING_RAW = {"NVDA": PERIDOT / "ramses-group-pool-NVDA.json", "SPY": PERIDOT / "ramses-group-pool-SPY.json"}
EXISTING_HEADERS = PERIDOT / "evidence-cache" / "headers.json"

GRID = 10_000
GRID_START = 27_500_000

# ------------------------------------------------------------------------------------------- addresses
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
NVDA = "0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC"
SPY = "0x117cc2133c37b721f49de2a7a74833232b3b4c0c"
RAM = "0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"

RAMSES_POOLS = {
    "nvda": {"pool": "0xdac1904d823f7d6bcaaf431ceee40c934fed321b", "raw": "NVDA", "token0": "USDG", "dec0": 6, "token1": "NVDA", "dec1": 18, "spacing": 10},
    "spy": {"pool": "0x38453c115607463ac284820ce959831042f3df4e", "raw": "SPY", "token0": "SPY", "dec0": 18, "token1": "USDG", "dec1": 6, "spacing": 1},
}
RAM_POOL = "0x5860dd468b4fc1740eaf80a3b7f22e78d70a076a"   # WETH/RAM CL pool, top TVL among RAM pools in the gateway snapshot of 2026-11-02
GAUGES = {"nvda": "0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce", "spy": "0x739e31798efe8cff3ef4c613d8ae751e179c7337"}

V4_POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
V4_STATE_VIEW = "0xf3334192D15450CdD385c8B70e03f9A6bD9E673b"
V4_NVDA_POOL_ID = "0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1"

UNIV3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"   # Uniswap docs, Robinhood Chain (4663) v3 deployments; code checked on chain

FEEDS = {  # name -> proxy (None: only the aggregator was given), aggregator named in the task
    "nvda": {"proxy": "0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15", "aggregator": "0xc9d16e4f2569b9e3ea0468fd85844953713dc2a2"},
    "spy": {"proxy": "0x319724394D3A0e3669269846abE664Cd621f9f6A", "aggregator": "0x78bcb218fa04b9b3a278ebc865ed320bf8defbac"},
    "usdg": {"proxy": "0x61B7e5650328764B076A108EFF5fa7282a1B9aD2", "aggregator": "0x8beee3503f6860d5dac4ce26b5eee92982951c2e"},
    "eth_usd": {"proxy": "0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9", "aggregator": "0x6091E64eb7138EEF066a80FD3A0d7427B91f2721"},
    "nvda_247": {"proxy": "0x321A36e441DfBadA01Aa6f9884F14a344E133D1F", "aggregator": None},
}
FEED_FROM_BLOCK = 27_000_000

# ------------------------------------------------------------------------------------------ signatures
_T: dict = {}


def T(sig: str) -> str:
    """Event topic0 (keccak256 of the signature); memoised because the repo's keccak is pure Python."""
    if sig not in _T:
        _T[sig] = keccak_topic(sig)
    return _T[sig]


SIG = {
    "v3_swap": "Swap(address,address,int256,int256,uint160,uint128,int24)",
    "v3_mint": "Mint(address,address,uint256,int24,int24,uint128,uint256,uint256)",
    "v3_mint_std": "Mint(address,address,int24,int24,uint128,uint256,uint256)",
    "v3_burn": "Burn(address,int24,int24,uint128,uint256,uint256)",
    "v3_init": "Initialize(uint160,int24)",
    "feeadj": "FeeAdjustment(uint24,uint24)",
    "setfee": "SetFee(uint24,uint24)",
    "proto_a": "SetFeeProtocol(uint8,uint8,uint8,uint8)",
    "proto_b": "SetFeeProtocol(uint24,uint24)",
    "proto_c": "SetFeeProtocol(uint24)",
    "v3_pool_created": "PoolCreated(address,address,uint24,int24,address)",
    "v4_init": "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)",
    "v4_swap": "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)",
    "v4_modify": "ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)",
    "answer_updated": "AnswerUpdated(int256,uint256,uint256)",
    "notify_reward": "NotifyReward(address,address,uint256,uint256)",
}


# ------------------------------------------------------------------------------------------------ rpc
def rpc(cached: bool = True) -> Rpc:
    return Rpc(cache_dir=(CACHE / "logs") if cached else None, min_interval=0.35)


def seed_headers() -> dict:
    """Our own header-grid file: on-grid entries copied from the existing cache (read only), never the shared file itself."""
    if HEADERS_CACHE.exists():
        return json.loads(HEADERS_CACHE.read_text())
    src = json.loads(EXISTING_HEADERS.read_text()) if EXISTING_HEADERS.exists() else {}
    grid = {k: v for k, v in src.items() if int(k) % GRID == 0}
    HEADERS_CACHE.write_text(json.dumps(grid))
    return grid


def time_index(r: Rpc) -> TimeIndex:
    seed_headers()
    ti = TimeIndex(r, step=GRID, path=HEADERS_CACHE)
    ti.grid = {k: v for k, v in ti.grid.items() if k % GRID == 0}
    return ti


def load_end() -> dict:
    if not END_FILE.exists():
        raise SystemExit("END is not fixed yet: run rh_02_fix_end.py first")
    return json.loads(END_FILE.read_text())


def jdump(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=False))
    os.replace(tmp, path)


def jload(path: Path):
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------------------------------------ csv output
def write_csv_gz(name: str, header: list[str], rows) -> dict:
    """Deterministic gzip (mtime 0, no embedded file name) so the sha256 is stable across re-runs."""
    path = DATA / name
    n = 0
    first = last = None
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz:
            with io.TextIOWrapper(gz, encoding="ascii", newline="") as tx:
                w = csv.writer(tx, lineterminator="\n")
                w.writerow(header)
                for row in rows:
                    w.writerow(row)
                    if first is None:
                        first = row
                    last = row
                    n += 1
    os.replace(tmp, path)
    return {"path": name, "rows": n, "first_row": list(map(str, first)) if first else None, "last_row": list(map(str, last)) if last else None}


def read_csv_gz(name: str):
    with gzip.open(DATA / name, "rt", encoding="ascii", newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        return header, list(rd)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_fmt(x: float) -> str:
    return f"{x:.1f}"


# ------------------------------------------------------------------------------------- step functions
class Step:
    """Value that changes at (block, logIndex); ``at`` gives the value in force at a position (events at or before it apply)."""

    def __init__(self, initial, changes):
        self.initial = initial
        self.changes = sorted(changes)
        self.keys = [(c[0], c[1]) for c in self.changes]

    def at(self, block: int, idx: int):
        i = bisect.bisect_right(self.keys, (block, idx)) - 1
        return self.initial if i < 0 else self.changes[i][2]

    @property
    def last(self):
        return self.changes[-1][2] if self.changes else self.initial


def log_key(l: dict):
    return (l["blockNumber"], l["logIndex"])


def dedup_sorted(logs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for l in sorted(logs, key=log_key):
        k = log_key(l)
        if k in seen:
            raise EvidenceError(f"duplicate log position {k}")
        seen.add(k)
        out.append(l)
    return out


def say(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------- v4: all pools in one scan
V4_SCAN_STEP = 100_000   # the node allows 100,000 blocks per eth_getLogs when a topic position holds several values


def v4_enumerate(r: Rpc, upto: int) -> list[dict]:
    """PoolManager Initialize events whose (currency0, currency1) is USDG x NVDA or USDG x SPY, in either order."""
    pools = []
    for sym, stock in (("NVDA", NVDA), ("SPY", SPY)):
        for c0, c1 in ((USDG, stock), (stock, USDG)):
            for l in r.logs(V4_POOL_MANAGER, [T(SIG["v4_init"]), None, addr_topic(c0), addr_topic(c1)], 0, upto, upto):
                w = words(l["data"])
                pools.append({"symbol": sym, "pool_id": l["topics"][1], "currency0": topic_addr(l["topics"][2]), "currency1": topic_addr(l["topics"][3]),
                              "fee": w[0], "tick_spacing": sN(w[1], 24), "hooks": "0x" + format(w[2], "040x"),
                              "init_sqrt_price_x96": str(w[3]), "init_tick": sN(w[4], 24), "init_block": l["blockNumber"],
                              "init_log_index": l["logIndex"], "init_tx": l["tx"]})
    pools.sort(key=lambda p: (p["init_block"], p["init_log_index"]))
    if len({p["pool_id"] for p in pools}) != len(pools):
        raise EvidenceError("duplicate v4 pool id in Initialize events")
    return pools


def v4_scan_swaps(r: Rpc, ids: list[str], lo: int, hi: int, progress=None):
    """Generator over 100,000-block windows of [lo, hi]: yields each window's v4 Swaps of the given pool ids as a list of
    [pool_id, block, log_index, data], oldest first (a generator, because the pairs have millions of swaps in total).

    One eth_getLogs per window with all ids in topic position 1 (OR). Complete windows are cached (gzip) under
    data/.cache/robinhood/v4scan/<hash of the id list>/; a window the node refuses (more than 10,000 logs) is halved."""
    key = hashlib.sha1(json.dumps(sorted(x.lower() for x in ids)).encode()).hexdigest()[:16]
    d = CACHE / "v4scan" / key
    d.mkdir(parents=True, exist_ok=True)
    topic = T(SIG["v4_swap"])
    cur, n, total = lo, 0, 0
    while cur <= hi:
        top = min((cur // V4_SCAN_STEP + 1) * V4_SCAN_STEP - 1, hi)
        f = d / f"{cur}-{top}.json.gz"
        plain = d / f"{cur}-{top}.json"
        if f.exists():
            with gzip.open(f, "rt") as fh:
                rows = json.load(fh)
        elif plain.exists():
            rows = json.loads(plain.read_text())
        else:
            rows = []
            stack = [(cur, top)]
            while stack:
                a, b = stack.pop()
                try:
                    logs = r._logs_raw(V4_POOL_MANAGER, [topic, ids], a, b)
                except Exception as e:  # SplitWindow from evidence_lib: too many results / range refused
                    if type(e).__name__ != "SplitWindow" or a == b:
                        raise
                    m = (a + b) // 2
                    stack += [(m + 1, b), (a, m)]
                    continue
                rows += [[l["topics"][1], l["blockNumber"], l["logIndex"], l["data"]] for l in logs]
            rows.sort(key=lambda x: (x[1], x[2]))
            tmp = f.with_suffix(f".tmp{os.getpid()}")
            with gzip.open(tmp, "wt", compresslevel=5) as fh:
                json.dump(rows, fh)
            os.replace(tmp, f)
        n += 1
        total += len(rows)
        if progress and n % 50 == 0:
            progress(f"v4 scan window {cur}-{top}: {total} swaps so far")
        yield rows
        cur = top + 1


# ------------------------------------------------------------- compact, streaming scan of one contract's logs
def scan_compact(r: Rpc, address: str, topics: list, lo: int, hi: int, progress=None):
    """The pieces of ``scan_pieces`` without their block ranges."""
    for _, _, rows in scan_pieces(r, address, topics, lo, hi, progress):
        yield rows


def scan_pieces(r: Rpc, address: str, topics: list, lo: int, hi: int, progress=None, start_step: int = 2_000_000):
    """Generator: yields (from, to, logs) for one contract over [lo, hi] piece by piece, logs being a list of
    [block, log_index, topics[1:], data], oldest first. For contracts with millions of events: only one response
    (at most 10,000 logs) is held in memory, and every response is cached gzip-compressed as
    data/.cache/robinhood/scan/<address>-<topic0[:10]>-<hash>/<from>-<to>.json.gz.

    The request width adapts to the density: halved when the node refuses (more than 10,000 logs), doubled after a
    response with fewer than 3,000 logs (up to 9,000,000 blocks). A re-run reads the cached pieces while they are
    contiguous from ``lo`` and fetches from where they end."""
    key = hashlib.sha1(json.dumps([address.lower(), topics]).encode()).hexdigest()[:10]
    d = CACHE / "scan" / f"{address.lower()}-{str(topics[0])[:10]}-{key}"
    d.mkdir(parents=True, exist_ok=True)
    have = {}
    for f in d.glob("*.json.gz"):
        a, b = f.name[:-8].split("-")
        have[int(a)] = (int(b), f)
    cur, n, total, step = lo, 0, 0, start_step
    while cur <= hi:
        if cur in have and have[cur][0] <= hi:
            top, f = have[cur]
            with gzip.open(f, "rt") as fh:
                rows = json.load(fh)
        else:
            while True:
                top = min(cur + step - 1, hi)
                try:
                    logs = r._logs_raw(address, topics, cur, top)
                    break
                except Exception as e:  # SplitWindow from evidence_lib
                    if type(e).__name__ != "SplitWindow" or top == cur:
                        raise
                    step = max((top - cur + 1) // 2, 1)
            rows = sorted(([l["blockNumber"], l["logIndex"], l["topics"][1:], l["data"]] for l in logs), key=lambda x: (x[0], x[1]))
            for x, y in zip(rows, rows[1:]):
                if (x[0], x[1]) == (y[0], y[1]):
                    raise EvidenceError(f"duplicate log position {(x[0], x[1])} from {address}")
            f = d / f"{cur}-{top}.json.gz"
            tmp = f.with_suffix(f".tmp{os.getpid()}")
            with gzip.open(tmp, "wt", compresslevel=5) as fh:
                json.dump(rows, fh)
            os.replace(tmp, f)
            if len(logs) < 3000:
                step = min(step * 2, 9_000_000)
        n += 1
        total += len(rows)
        if progress and n % 25 == 0:
            progress(f"scan {address[:10]} {str(topics[0])[:10]} piece {cur}-{top}: {total} logs so far")
        yield cur, top, rows
        cur = top + 1


def scan_all(r: Rpc, address: str, topics: list, lo: int, hi: int, progress=None) -> list:
    out = []
    for rows in scan_compact(r, address, topics, lo, hi, progress):
        out += rows
    return out
