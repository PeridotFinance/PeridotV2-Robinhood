"""Shared read-only JSON-RPC helper (python3 stdlib only). Every call is logged with its exact request and response."""
import json, time, urllib.request, urllib.error, datetime, subprocess, hashlib, socket
_orig = socket.getaddrinfo
# Python 3.9 on this host stalls ~30 s per request on the IPv6 attempt for this endpoint; curl does not. Prefer IPv4 only.
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]
RPC = "https://rpc.mainnet.chain.robinhood.com"
UA = "curl/8.4.0"
LOG = []
_n = 0
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
def rpc(method, params, label=None, sleep=0.35, retries=4):
    """Serial JSON-RPC call. Returns the parsed response dict (may contain 'error'). Failures are logged, never swallowed."""
    global _n
    _n += 1
    req = {"jsonrpc": "2.0", "id": _n, "method": method, "params": params}
    body = json.dumps(req).encode()
    last = None
    for attempt in range(retries):
        try:
            r = urllib.request.Request(RPC, data=body, headers={"content-type": "application/json", "user-agent": UA})
            with urllib.request.urlopen(r, timeout=60) as resp:
                out = json.loads(resp.read())
            last = None
            break
        except urllib.error.HTTPError as e:
            last = {"transportError": f"HTTP {e.code}", "body": e.read()[:300].decode("utf8", "replace")}
            if e.code == 429: time.sleep(2 ** (attempt + 1))
            else: time.sleep(1)
        except Exception as e:
            last = {"transportError": repr(e)}; time.sleep(1)
    if last is not None: out = last
    LOG.append({"label": label, "atUtc": now(), "request": req, "response": out})
    time.sleep(sleep)
    return out
def result(o):
    if "result" not in o: raise RuntimeError(f"RPC failure (reported, not skipped): {json.dumps(o)[:300]}")
    return o["result"]
def keccak_hex(hexdata):
    return subprocess.check_output(["cast", "keccak", hexdata], text=True).strip()
def sha256_hex(hexdata):
    return hashlib.sha256(bytes.fromhex(hexdata[2:])).hexdigest()
def selector(sig): return subprocess.check_output(["cast", "sig", sig], text=True).strip()
def eth_call(to, data, block, label):
    return rpc("eth_call", [{"to": to, "data": data}, block], label=label)
def dump(path):
    json.dump(LOG, open(path, "w"), indent=1)
