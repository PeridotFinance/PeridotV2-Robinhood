"""READ-ONLY JSON-RPC helpers for the pinned balance sheet. stdlib + curl + `cast` (pure hashing only).
Only read methods are permitted: nothing is signed or broadcast. eth_call / eth_simulateV1 are local simulations on the node."""
import json, subprocess, time, os, re
from pathlib import Path
HERE = Path(__file__).parent
URL = "https://rpc.mainnet.chain.robinhood.com"
ALLOWED = {"eth_call", "eth_simulateV1", "eth_getCode", "eth_getLogs", "eth_getBlockByNumber", "eth_getStorageAt", "eth_blockNumber",
           "eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_chainId", "eth_getBalance", "eth_getTransactionCount", "eth_gasPrice"}
MISSING = re.compile(r"missing trie|header not found|state .*not available|pruned|unknown block|historical state|required historical", re.I)


class Repin(Exception):
    pass


class RpcErr(Exception):
    def __init__(self, e):
        self.err = e
        super().__init__(str(e))


_id = [0]
STATS = {"requests": 0, "items": 0}


def _post(body):
    for attempt in range(6):
        p = subprocess.run(["curl", "-s", "-4", "-m", "60", "-A", "curl/8.0", "-H", "content-type: application/json", "--data-binary", "@-", URL],
                           input=body, capture_output=True, text=True)
        STATS["requests"] += 1
        try:
            return json.loads(p.stdout)
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError("rpc: no parsable response " + p.stdout[:200])


def batch(items, chunk=1):
    """Serial, paced (public endpoint rate-limits per item, batches included). items: [(method, params)] -> [{"r":..}|{"e":..}].
    Raises Repin when pinned state is gone."""
    out = []
    for m, pr in items:
        assert m in ALLOWED, m
        _id[0] += 1
        body = json.dumps({"jsonrpc": "2.0", "id": _id[0], "method": m, "params": pr})
        for attempt in range(400):
            x = _post(body)
            if "error" in x:
                msg = json.dumps(x["error"])
                if MISSING.search(msg):
                    raise Repin(msg[:300])
                if re.search(r"429|rate limit|too many requests|timeout", msg, re.I):
                    STATS["r429"] = STATS.get("r429", 0) + 1
                    time.sleep(0.4)
                    continue
                out.append({"e": x["error"]})
            else:
                out.append({"r": x["result"]})
            break
        else:
            raise RuntimeError("rpc: repeatedly rate limited")
    STATS["items"] += len(items)
    return out


def call(method, params):
    r = batch([(method, params)])[0]
    if "e" in r:
        raise RpcErr(r["e"])
    return r["r"]


_cache_path = HERE / "selectors-cache.json"
_cache = json.loads(_cache_path.read_text()) if _cache_path.exists() else {}


def _save():
    _cache_path.write_text(json.dumps(_cache, indent=0, sort_keys=True))


def sel(sig):
    if sig not in _cache:
        _cache[sig] = subprocess.run(["cast", "sig", sig], capture_output=True, text=True).stdout.strip()
        _save()
    return _cache[sig]


def topic(sig):
    k = "T:" + sig
    if k not in _cache:
        _cache[k] = subprocess.run(["cast", "keccak", sig], capture_output=True, text=True).stdout.strip()
        _save()
    return _cache[k]


def keccak_hex(h):
    k = "K:" + h
    if k not in _cache:
        _cache[k] = subprocess.run(["cast", "keccak", h], capture_output=True, text=True).stdout.strip()
        _save()
    return _cache[k]


def words(h):
    h = h[2:] if h.startswith("0x") else h
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def sword(v):
    return v - (1 << 256) if v >= (1 << 255) else v


def addr(v):
    return "0x" + (v if isinstance(v, int) else int(v, 16)).to_bytes(32, "big")[12:].hex()


def pa(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def pi(i):
    return hex(i & ((1 << 256) - 1))[2:].rjust(64, "0")


def isqrt(n):
    import math
    return math.isqrt(n)


MC3 = "0xca11bde05977b3631167028862be2a173976ca11"
MC3_SEL = "0x82ad56cb"  # aggregate3((address,bool,bytes)[])


def mc_encode(calls):
    """calls: [(to, hexdata)] with allowFailure=true."""
    heads, bodies, off = [], [], 32 * len(calls)
    for to, data in calls:
        d = data[2:]
        n = len(d) // 2
        pad = d + "0" * ((64 - len(d) % 64) % 64)
        body = pa(to) + pi(1) + pi(0x60) + pi(n) + pad
        heads.append(pi(off))
        bodies.append(body)
        off += len(body) // 2
    return MC3_SEL + pi(0x20) + pi(len(calls)) + "".join(heads) + "".join(bodies)


def mc_decode(h):
    b = bytes.fromhex(h[2:])
    def u(o):
        return int.from_bytes(b[o:o + 32], "big")
    base = u(0)
    n = u(base)
    out = []
    for i in range(n):
        o = base + 32 + u(base + 32 + 32 * i)
        ok = bool(u(o))
        do = o + u(o + 32)
        ln = u(do)
        out.append((ok, "0x" + b[do + 32:do + 32 + ln].hex()))
    return out
