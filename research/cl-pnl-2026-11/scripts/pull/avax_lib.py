"""Shared helpers for the Avalanche C-Chain / external-price data pull (read-only, Python 3 stdlib only).

Nothing here signs or sends a transaction: the only JSON-RPC methods used are eth_blockNumber, eth_getBlockByNumber,
eth_getLogs, eth_call and eth_getCode. Endpoints are keyless public ones; requests carry nothing but public contract
addresses, topics and block numbers, with a generic User-Agent.

Writes go only under research/cl-pnl-2026-11/data/ (datasets in data/avalanche and data/external, caches in
data/.cache/avalanche and data/.cache/external).
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # research/cl-pnl-2026-11
DATA_AVAX = ROOT / "data" / "avalanche"
DATA_EXT = ROOT / "data" / "external"
CACHE = ROOT / "data" / ".cache" / "avalanche"
CACHE_EXT = ROOT / "data" / ".cache" / "external"
for _d in (DATA_AVAX, DATA_EXT, CACHE, CACHE_EXT):
    _d.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (compatible; data-pull/1.0)"

# Keyless public endpoints (tested 2026-11-10):
#   tenderly   : eth_getLogs over >= 50,000 blocks, archive eth_call; rejects JSON-RPC batches (429); the keyless
#                gateway has a daily egress allowance (response header x-tdly-egress-limit: 1,000,000,000 bytes) which
#                this pull exhausted after ~8.1M blocks of pool logs: after that every call returns HTTP 429 until the
#                reset time in x-tdly-egress-reset-timestamp
#   publicnode : eth_getLogs over >= 50,000 blocks, JSON-RPC batches of 1,000 headers; historical eth_call times out
#   avax       : official API node, archive eth_call, eth_getLogs limited to 100 blocks (state reads only)
RPC = {
    "tenderly": "https://avalanche-mainnet.gateway.tenderly.co",
    "publicnode": "https://avalanche-c-chain-rpc.publicnode.com",
    "avax": "https://api.avax.network/ext/bc/C/rpc",
}
ROUTESCAN = "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api"

# ---- addresses (all verified on chain by avax_00_pin_state.py; see data/avalanche/state_at_end.json) ----
POOL = "0xf01449C0bA930B6e2CaCA3DEF3CCBd7a3E589534"        # Pharaoh (RamsesV3Pool) WAVAX/USDC, tick spacing 10
WAVAX = "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"
USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
PHAR = "0x13A466998Ce03Db73aBc2d4DF3bBD845Ed1f28E7"
XPHAR = "0xE8164Ea89665DAb7a553e667F81F30CfDA736B9A"
VOTER = "0x922b9Ca8e2207bfB850B6FF647c054d4b58a2Aa7"
CL_FACTORY = "0xAE6E5c62328ade73ceefD42228528b70c8157D0d"
NFPM = "0x0B4478e810D48B5882D4019D435A2f864Bab4F39"
AVAX_USD_PROXY = "0x0A77230d17318075983913bC2145DB16C7366156"
USDC_VAULT = "0x855bF832f26a294d28500db59eE941dE3d654129"
WAVAX_VAULT = "0xe9a53f0077f9cf767a95Ce75Da483E906eE190E8"

START_TS = 1785542400                        # 2026-08-01 00:00:00 UTC

# ---- 4-byte selectors (keccak256 of the signature, computed offline) ----
SEL = {
    "token0()": "0x0dfe1681", "token1()": "0xd21220a7", "tickSpacing()": "0xd0c93a7c", "fee()": "0xddca3f43",
    "liquidity()": "0x1a686502", "slot0()": "0x3850c7bd", "factory()": "0xc45a0155",
    "feeGrowthGlobal0X128()": "0xf3058399", "feeGrowthGlobal1X128()": "0x46141319",
    "grossFeeGrowthGlobal0X128()": "0x461dfd23", "grossFeeGrowthGlobal1X128()": "0xe8169d4b",
    "protocolFees()": "0x1ad8b03b", "lastPeriod()": "0xd340ef8a",
    "symbol()": "0x95d89b41", "decimals()": "0x313ce567", "name()": "0x06fdde03", "balanceOf(address)": "0x70a08231",
    "totalSupply()": "0x18160ddd",
    "gaugeForPool(address)": "0x2045be90", "poolForGauge(address)": "0x06d6a1b2", "xRatio()": "0x986e471d",
    "xRam()": "0x11233634", "ram()": "0x8f33b5e5", "getPeriod()": "0x1ed24195", "isAlive(address)": "0x1703e5f9",
    "timeThresholdForGauge(address)": "0x0ce11312", "tickSpacingsForPair(address,address)": "0x270a1b57",
    "gaugeRewardsPerPeriod(address,uint256)": "0x77b325c1", "legacyFactory()": "0x2da5347b", "clFactory()": "0xe74f6166",
    "pool()": "0x16f0115b", "getRewardTokens()": "0xc4f59f9b", "rewardRate(address)": "0x221ca18c",
    "left(address)": "0x99bcc052", "tokenTotalSupplyByPeriod(uint256,address)": "0xe92a9fa9",
    "firstPeriod()": "0xc4e3a63b", "voter()": "0x46c96aac", "nfpManager()": "0x98bbc3c7", "feeCollector()": "0xc415b95c",
    "r33()": "0x0a2e3e27", "implementation()": "0x5c60da1b",
    "aggregator()": "0x245a7bfc", "latestRoundData()": "0xfeaf968c", "description()": "0x7284e416",
    "SLASHING_PENALTY()": "0x25d64f4b", "BASIS()": "0x528cfa98", "PHAR()": "0xc450a9d9", "paused()": "0x5c975abb",
    "isExempt(address)": "0xad5dff73",
    "getPool(address,address,int24)": "0x28af8d0b", "getPair(address,address,bool)": "0x6801cc30",
    "poolFeeProtocol(address)": "0xebb0d9f7", "feeProtocol()": "0x527eb4bc",
    "getReserves()": "0x0902f1ac", "stable()": "0x22be3de1",
}

# ---- event topics (keccak256 of the signature; signatures read from the verified sources on Sourcify) ----
TOPIC = {
    # RamsesV3Pool (IRamsesV3PoolEvents + ProtocolActions library)
    "Swap": "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",   # Swap(address indexed sender,address indexed recipient,int256 amount0,int256 amount1,uint160 sqrtPriceX96,uint128 liquidity,int24 tick)
    "Mint": "0xd78218c0d304e8893cb3200abe394bbc8d5b7804d9c51f236df9fdcf481d02d3",   # Mint(address sender,address indexed owner,uint256 index,int24 indexed tickLower,int24 indexed tickUpper,uint128 amount,uint256 amount0,uint256 amount1)
    "Burn": "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c",   # Burn(address indexed owner,int24 indexed tickLower,int24 indexed tickUpper,uint128 amount,uint256 amount0,uint256 amount1)
    "Collect": "0x70935338e69775456a85ddef226c395fb668b63fa0115f5f20610b388e6ca9c0",  # Collect(address indexed owner,address recipient,int24 indexed tickLower,int24 indexed tickUpper,uint128 amount0,uint128 amount1)
    "CollectProtocol": "0x596b573906218d3411850b26a6b437d6c4522fdb43d2d2386263f86d50b8b151",  # CollectProtocol(address indexed sender,address indexed recipient,uint128 amount0,uint128 amount1)
    "FeeAdjustment": "0x0cba87189055d3b5ab05c96fbd641bc766576c9e7cf0d195bdfb58a0c6a6df24",    # FeeAdjustment(uint24 oldFee,uint24 newFee)   (ProtocolActions.sol)
    "SetFeeProtocol": "0x67a069e4d951485f3e494a1edfa67d7334e991e8514ba748fd1636270acd1c97",   # SetFeeProtocol(uint24 feeProtocolOld,uint24 feeProtocolNew)   (ProtocolActions.sol)
    "SetFeeProtocolLegacy": "0x973d8d92bb299f4af6ce49b52a8adb85ae46b9f214c4c4fc06ac77401237b133",  # SetFeeProtocol(uint8,uint8,uint8,uint8)  (declared in the ABI, v3 legacy)
    "Flash": "0xbdbdb71d7860376ba52b25a5028beea23581364a40522f6bcfb86bb1f2dca633",
    "IncreaseObservationCardinalityNext": "0xac49e518f90a358f652e4400164f05a5d8f7e35e7747279bc3a93dbf584e125a",
    "Initialize": "0x98636036cb66a9c19a37435efc1e90142190214e8abeb821bdba3f2990dd4c95",
    # GaugeV3
    "NotifyReward": "0x52977ea98a2220a03ee9ba5cb003ada08d394ea10155483c95dc2dc77a7eb24b",     # NotifyReward(address indexed from,address indexed reward,uint256 amount,uint256 period)
    "Bribe": "0x9a53e9fd97eac7418eb1371afa658791fdc6cd9eb5d1d8f3efca309484ddfaeb",            # Bribe(address indexed from,address indexed reward,uint256 amount,uint256 period)
    "ClaimRewards": "0xc8c7ebd754a625a8677ab2031c7674259be1e8c1a7f3521cbf5edbca8f48099c",     # ClaimRewards(uint256 period,bytes32 _positionHash,address receiver,address reward,uint256 amount)
    "Forfeited": "0x664facd930da6a01b5a6aa33775fc2cc77117d7de31622f4d083197d8ef824ed",
    "RewardAdded": "0xb13fd610fe4e1b384966826794a9b2f6100ad031f352cc5ec6f22667f6074980",
    "RewardRemoved": "0x755c47ac85b75fe2251607db5a480aac818b88bb535814bf1e3c4784ae4f6baa",
    # Chainlink aggregator
    "AnswerUpdated": "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f",    # AnswerUpdated(int256 indexed current,uint256 indexed roundId,uint256 updatedAt)
    # Solidly-style legacy pair
    "SwapV2": "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",           # Swap(address indexed sender,uint256 amount0In,uint256 amount1In,uint256 amount0Out,uint256 amount1Out,address indexed to)
    "Sync": "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1",
    # legacy PairFactory (event list from its verified ABI on Sourcify)
    "SetPairFee": "0xc792b1e9d2b63c63a75f8146a0b5bd7f568bdd6a0b97b9c31d585398718d4c46",        # SetPairFee(address indexed pair,uint256 indexed fee)
    "SetPairFeeSplit": "0x40c433a8082166fcd8218fba9d4247bb08e03016d0056fc857d6363673ded031",   # SetPairFeeSplit(address indexed pair,uint256 indexed _feeSplit)
    "PairCreated": "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",       # PairCreated(address indexed token0,address indexed token1,address pair,uint256)
    "SetFeeRecipient": "0xd9d6b85b6d670cd443496fc6d03390f739bbff47f96a8e33fb0cdd52ad26f5c2",   # SetFeeRecipient(address indexed pair,address indexed feeRecipient)
}
TOPIC_NAME = {v: k for k, v in TOPIC.items()}


class RpcError(Exception):
    pass


_last_call: dict[str, float] = {}


def _pace(key: str, min_gap: float) -> None:
    now = time.time()
    wait = _last_call.get(key, 0.0) + min_gap - now
    if wait > 0:
        time.sleep(wait)
    _last_call[key] = time.time()


def http_json(url: str, body=None, timeout: float = 60.0, headers: dict | None = None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_bytes(url: str, timeout: float = 60.0, headers: dict | None = None) -> bytes:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def rpc(source: str, method: str, params: list, timeout: float = 60.0, tries: int = 6, min_gap: float = 0.25):
    """One JSON-RPC call with pacing and exponential backoff. Raises RpcError on a JSON-RPC error object."""
    url = RPC[source]
    delay = 1.5
    last = None
    for attempt in range(tries):
        _pace(source, min_gap)
        try:
            d = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            time.sleep(delay * (3 if e.code in (429, 403) else 1))
            delay *= 2
            continue
        except Exception as e:  # timeouts, resets
            last = repr(e)[:200]
            time.sleep(delay)
            delay *= 2
            continue
        if "error" in d:
            msg = json.dumps(d["error"])[:300]
            # range/size complaints must reach the caller at once so it can split; transient ones are retried
            low = msg.lower()
            if any(k in low for k in ("too large", "exceed", "limit", "range", "more than", "response size")) and "rate" not in low and "rps" not in low:
                raise RpcError(msg)
            last = msg
            time.sleep(delay)
            delay *= 2
            continue
        return d["result"]
    raise RpcError(f"{source} {method}: gave up after {tries} tries: {last}")


def rpc_batch(source: str, calls: list[tuple[str, list]], timeout: float = 90.0, tries: int = 6, min_gap: float = 0.3) -> list:
    url = RPC[source]
    body = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    delay = 1.5
    last = None
    for attempt in range(tries):
        _pace(source, min_gap)
        try:
            d = http_json(url, body, timeout=timeout)
            if not isinstance(d, list) or len(d) != len(calls):
                raise RpcError(f"batch reply shape: {str(d)[:200]}")
            out = [None] * len(calls)
            for x in d:
                if "error" in x or x.get("result") is None:
                    raise RpcError(f"batch item error: {str(x)[:200]}")
                out[x["id"]] = x["result"]
            return out
        except Exception as e:
            last = repr(e)[:200]
            time.sleep(delay)
            delay *= 2
    raise RpcError(f"{source} batch: gave up: {last}")


def eth_call(source: str, to: str, data: str, block: int | str = "latest") -> str:
    tag = hex(block) if isinstance(block, int) else block
    return rpc(source, "eth_call", [{"to": to, "data": data}, tag])


# ---- ABI helpers ----
def words(hexdata: str) -> list[int]:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def s256(x: int) -> int:
    return x - (1 << 256) if x >> 255 else x


def s24(x: int) -> int:
    x &= (1 << 24) - 1
    return x - (1 << 24) if x >> 23 else x


def addr_arg(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def uint_arg(n: int) -> str:
    return hex(n & ((1 << 256) - 1))[2:].rjust(64, "0")


def word_addr(w: int) -> str:
    return "0x" + hex(w & ((1 << 160) - 1))[2:].rjust(40, "0")


def topic_addr(t: str) -> str:
    return "0x" + t[-40:]


def dec_string(hexdata: str) -> str:
    w = words(hexdata)
    if len(w) < 2:
        return ""
    h = hexdata[2:]
    off = w[0] * 2
    n = int(h[off:off + 64], 16)
    return bytes.fromhex(h[off + 64: off + 64 + 2 * n]).decode("utf-8", "replace")


def dec_addr_array(hexdata: str) -> list[str]:
    w = words(hexdata)
    n = w[1]
    return [word_addr(x) for x in w[2:2 + n]]


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- files ----
def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=False) + "\n")
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json_gz(path: Path, obj) -> None:
    """Deterministic gzip (mtime 0) so re-runs on the same data give the same sha256."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as g:
            g.write(json.dumps(obj, separators=(",", ":")).encode())
    os.replace(tmp, path)


def read_json_gz(path: Path):
    with gzip.open(path, "rb") as g:
        return json.loads(g.read())


def write_csv_gz(path: Path, header: list[str], rows) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as g:
            t = io.TextIOWrapper(g, encoding="utf-8", newline="")
            w = csv.writer(t, lineterminator="\n")
            w.writerow(header)
            for r in rows:
                w.writerow(r)
                n += 1
            t.flush()
            t.detach()
    os.replace(tmp, path)
    return n


def read_csv_gz(path: Path) -> tuple[list[str], list[list[str]]]:
    with gzip.open(path, "rt", newline="") as f:
        r = csv.reader(f)
        head = next(r)
        return head, [row for row in r]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---- chunked, cached, resumable eth_getLogs ----
def fetch_logs(name: str, address: str, start: int, end: int, source: str = "tenderly", chunk: int = 20000,
               topics: list | None = None, cap_suspect: int = 9000, log=print) -> list[dict]:
    """All logs of `address` in [start, end], fetched in `chunk`-block windows on a fixed grid from `start`.

    Every window is cached as data/.cache/avalanche/logs/<name>/<from>-<to>.json.gz with the source that served it; a
    window that errors on range/size, or returns a suspiciously round or large count (possible silent cap), is split in
    two and re-fetched. The ledger (range, source, count) is written to data/.cache/avalanche/logs/<name>/ledger.json.
    """
    d = CACHE / "logs" / name
    d.mkdir(parents=True, exist_ok=True)
    ledger_path = d / "ledger.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {}
    out: list[dict] = []

    def get(a: int, b: int) -> list[dict]:
        key = f"{a}-{b}"
        f = d / f"{key}.json.gz"
        if f.exists() and key in ledger:
            return read_json_gz(f)
        flt = {"address": address, "fromBlock": hex(a), "toBlock": hex(b)}
        if topics:
            flt["topics"] = topics
        try:
            res = rpc(source, "eth_getLogs", [flt], timeout=120)
        except RpcError as e:
            if b > a:
                m = (a + b) // 2
                log(f"  split {key}: {str(e)[:120]}")
                return get(a, m) + get(m + 1, b)
            raise
        n = len(res)
        if b > a and (n >= cap_suspect or (n >= 1000 and n % 1000 == 0)):
            m = (a + b) // 2
            log(f"  split {key}: count {n} looks capped")
            return get(a, m) + get(m + 1, b)
        slim = [{"b": int(x["blockNumber"], 16), "i": int(x["logIndex"], 16), "t": x["topics"], "d": x["data"],
                 "h": x["blockHash"], "tx": x["transactionHash"]} for x in res]
        for x in res:
            if x.get("removed"):
                raise RpcError(f"removed log in {key}")
        write_json_gz(f, slim)
        ledger[key] = {"from": a, "to": b, "source": source, "host": RPC[source].split("/")[2], "count": n,
                       "fetched_at": int(time.time())}
        write_json(ledger_path, ledger)
        return slim

    a = start
    k = 0
    while a <= end:
        b = min(a + chunk - 1, end)
        got = get(a, b)
        out.extend(got)
        k += 1
        if k % 25 == 0:
            log(f"  {name}: {a}-{b} ({len(out)} logs so far)")
        a = b + 1
    return out


def ledger_rows(name: str) -> list[dict]:
    p = CACHE / "logs" / name / "ledger.json"
    led = read_json(p)
    return sorted(led.values(), key=lambda r: r["from"])


# ---- block timestamps (cached) ----
def block_timestamps(blocks, source: str = "publicnode", batch: int = 500, log=print) -> dict[int, int]:
    """Real header timestamps for the given block numbers, by batched eth_getBlockByNumber; cached on disk in shards of
    100,000 blocks (data/.cache/avalanche/headers/<shard>.json). Nothing is interpolated."""
    d = CACHE / "headers"
    d.mkdir(parents=True, exist_ok=True)
    need = sorted(set(int(b) for b in blocks))
    out: dict[int, int] = {}
    by_shard: dict[int, list[int]] = {}
    for b in need:
        by_shard.setdefault(b // 100000, []).append(b)
    done = 0
    for shard, bl in sorted(by_shard.items()):
        f = d / f"{shard}.json"
        have = {int(k): v for k, v in read_json(f).items()} if f.exists() else {}
        miss = [b for b in bl if b not in have]
        for i in range(0, len(miss), batch):
            part = miss[i:i + batch]
            res = rpc_batch(source, [("eth_getBlockByNumber", [hex(b), False]) for b in part])
            for b, r in zip(part, res):
                if int(r["number"], 16) != b:
                    raise RpcError("header number mismatch")
                have[b] = [int(r["timestamp"], 16), r["hash"]]
            if (i // batch) % 10 == 9:
                write_json(f, {str(k): v for k, v in sorted(have.items())})
        if miss:
            write_json(f, {str(k): v for k, v in sorted(have.items())})
        for b in bl:
            out[b] = have[b]
        done += len(bl)
        if miss:
            log(f"  headers: shard {shard} +{len(miss)} ({done}/{len(need)})")
    return out


# ---- exact v3 math (ports of TickMath / SqrtPriceMath, integers only) ----
Q96 = 1 << 96
MIN_TICK, MAX_TICK = -887272, 887272


def sqrt_ratio_at_tick(tick: int) -> int:
    """TickMath.getSqrtRatioAtTick (Uniswap v3 / RamsesV3), exact."""
    a = abs(tick)
    assert a <= MAX_TICK
    r = 0xfffcb933bd6fad37aa2d162d1a594001 if a & 0x1 else 0x100000000000000000000000000000000
    for bit, mul in ((0x2, 0xfff97272373d413259a46990580e213a), (0x4, 0xfff2e50f5f656932ef12357cf3c7fdcc),
                     (0x8, 0xffe5caca7e10e4e61c3624eaa0941cd0), (0x10, 0xffcb9843d60f6159c9db58835c926644),
                     (0x20, 0xff973b41fa98c081472e6896dfb254c0), (0x40, 0xff2ea16466c96a3843ec78b326b52861),
                     (0x80, 0xfe5dee046a99a2a811c461f1969c3053), (0x100, 0xfcbe86c7900a88aedcffc83b479aa3a4),
                     (0x200, 0xf987a7253ac413176f2b074cf7815e54), (0x400, 0xf3392b0822b70005940c7a398e4b70f3),
                     (0x800, 0xe7159475a2c29b7443b29c7fa6e889d9), (0x1000, 0xd097f3bdfd2022b8845ad8f792aa5825),
                     (0x2000, 0xa9f746462d870fdf8a65dc1f90e061e5), (0x4000, 0x70d869a156d2a1b890bb3df62baf32f7),
                     (0x8000, 0x31be135f97d08fd981231505542fcfa6), (0x10000, 0x9aa508b5b7a84e1c677de54f3e99bc9),
                     (0x20000, 0x5d6af8dedb81196699c329225ee604), (0x40000, 0x2216e584f5fa1ea926041bedfe98),
                     (0x80000, 0x48a170391f7dc42444e8fa2)):
        if a & bit:
            r = (r * mul) >> 128
    if tick > 0:
        r = ((1 << 256) - 1) // r
    return (r >> 32) + (1 if r % (1 << 32) else 0)


def tick_at_sqrt_ratio(sq: int) -> int:
    """The greatest tick whose sqrt ratio is <= sq (same result as TickMath.getTickAtSqrtRatio), by bisection."""
    lo, hi = MIN_TICK, MAX_TICK
    while lo < hi:
        mid = (lo + hi + 1) >> 1
        if sqrt_ratio_at_tick(mid) <= sq:
            lo = mid
        else:
            hi = mid - 1
    return lo


def isqrt(n: int) -> int:
    import math
    return math.isqrt(n)


def amount0_delta_up(sq_a: int, sq_b: int, liq: int) -> int:
    """SqrtPriceMath.getAmount0Delta(..., roundUp = true)."""
    if sq_a > sq_b:
        sq_a, sq_b = sq_b, sq_a
    num1, num2 = liq << 96, sq_b - sq_a
    x = -((-num1 * num2) // sq_b)          # mulDivRoundingUp
    return -((-x) // sq_a)                 # divRoundingUp


def amount1_delta_up(sq_a: int, sq_b: int, liq: int) -> int:
    """SqrtPriceMath.getAmount1Delta(..., roundUp = true)."""
    if sq_a > sq_b:
        sq_a, sq_b = sq_b, sq_a
    return -((-liq * (sq_b - sq_a)) // Q96)
