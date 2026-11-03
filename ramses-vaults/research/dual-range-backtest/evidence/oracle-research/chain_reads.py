#!/usr/bin/env python3
"""Read-only Pyth / Chainlink reads on Robinhood Chain (4663). python3 stdlib + `cast` (selectors/keccak only). No signing, no sends.
usage: python3 chain_reads.py static      -> pyth-onchain-static.json   (code, slots, feed existence, prices at one pinned block)
       python3 chain_reads.py sample      -> appends one freshness sample to pyth-freshness-samples.jsonl
       python3 chain_reads.py logs        -> pyth-logs-scan.json        (event scan, range limits)
       python3 chain_reads.py chainlink   -> chainlink-feed-reads.json  (our Chainlink feeds + token multipliers)"""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpc_lib import *
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = "0x8250f4aF4B972684F7b336503E2D6dFeDeB1487a"   # "Pyth Core" row, Robinhood Chain, docs.pyth.network/pro/contract-addresses
PRO = "0xACeA761c27A909d4D3895128EBe6370FDE2dF481"    # "Pyth Pro" row
FEEDS = {  # hermes ids from pyth.dourolabs.app/v1/symbols (raw/pyth-symbols.json); control = BTC/USD
    "Equity.US.SPY/USD": "19e09bb805456ada3979a7d1cbb4b6d63babc3a0f8e8a9509f68afa5c4c11cd5",
    "Equity.US.NVDA/USD": "b1073854ed24cbc755dc527418f52b7d271f6cc967bbf8d8129112b18860a593",
    "Equity.Index.SPY/USD(24/7)": "f32c3fbdd20b846bb53395bf175c42955e00b1bffb5a778bb624982b318f5461",
    "Equity.Index.NVDA/USD(24/7)": "a470c4ac46f44b547b2cba52338f311fb642b79375ce5f0cfd5cb5b99227b852",
    "Equity.US.NVDA/USD.EXT(deprecated)": "e3d244e4ccf1a047bae83d54bf2327a7ce939a5acfe85727dc223c5cedffb5cc",
    "Crypto.BTC/USD(control)": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "Crypto.ETH/USD(control)": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
}
SLOTS = {"impl": "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
         "admin": "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103",
         "beacon": "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"}
def s32(x): return x[2:].rjust(64, "0")
def pin():
    b = result(rpc("eth_getBlockByNumber", ["latest", False], "latest block"))
    return b["number"], b["hash"], int(b["timestamp"], 16), b
def decode_price(hexres):
    h = hexres[2:]
    if len(h) < 256: return None
    w = [int(h[i:i + 64], 16) for i in range(0, 256, 64)]
    sx = lambda v, bits: v - (1 << bits) if v >= 1 << (bits - 1) else v
    low = lambda v, bits: v & ((1 << bits) - 1)   # ABI words are sign-extended to 256 bits: take the low bits, then sign-interpret
    return {"price": sx(low(w[0], 64), 64), "conf": low(w[1], 64), "expo": sx(low(w[2], 32), 32), "publishTime": low(w[3], 256)}
def call_price(sel_sig, fid, block, label, extra=""):
    sel = selector(sel_sig)
    return eth_call(CORE, sel + s32("0x" + fid) + extra, block, label)
def static():
    num, bh, ts, blk = pin()
    out = {"chainId": result(rpc("eth_chainId", [])), "pinnedBlock": {"number": num, "numberDec": int(num, 16), "hash": bh, "timestamp": ts, "l1BlockNumber": blk.get("l1BlockNumber")},
           "contracts": {}, "pythCoreFeeds": {}, "pythProProbe": {}, "notes": []}
    for name, addr in (("PythCore", CORE), ("PythPro", PRO)):
        code = result(rpc("eth_getCode", [addr, num], f"{name} code"))
        c = {"address": addr, "codeBytes": (len(code) - 2) // 2, "codeSha256": sha256_hex(code), "codeKeccak": keccak_hex(code), "slots": {}}
        for k, sl in SLOTS.items():
            c["slots"][k] = result(rpc("eth_getStorageAt", [addr, sl, num], f"{name} eip1967 {k}"))
        impl = "0x" + c["slots"]["impl"][-40:]
        if int(impl, 16):
            icode = result(rpc("eth_getCode", [impl, num], f"{name} implementation code"))
            c["implementation"] = {"address": impl, "codeBytes": (len(icode) - 2) // 2, "codeSha256": sha256_hex(icode), "codeKeccak": keccak_hex(icode)}
        out["contracts"][name] = c
    for name, fid in FEEDS.items():
        e = {"feedId": "0x" + fid}
        r = call_price("priceFeedExists(bytes32)", fid, num, f"{name} priceFeedExists")
        e["priceFeedExists"] = r.get("result", r.get("error"))
        r = call_price("getPriceUnsafe(bytes32)", fid, num, f"{name} getPriceUnsafe")
        e["getPriceUnsafe_raw"] = r.get("result", r.get("error"))
        if "result" in r: e["getPriceUnsafe"] = decode_price(r["result"])
        if e.get("getPriceUnsafe"): e["ageSecondsAtBlockTimestamp"] = ts - e["getPriceUnsafe"]["publishTime"]
        r = call_price("getPriceNoOlderThan(bytes32,uint256)", fid, num, f"{name} getPriceNoOlderThan(60)", s32(hex(60)))
        e["getPriceNoOlderThan_60"] = r.get("result", r.get("error"))
        r = call_price("getPriceNoOlderThan(bytes32,uint256)", fid, num, f"{name} getPriceNoOlderThan(43200)", s32(hex(43200)))
        e["getPriceNoOlderThan_43200"] = r.get("result", r.get("error"))
        r = call_price("getEmaPriceUnsafe(bytes32)", fid, num, f"{name} getEmaPriceUnsafe")
        e["getEmaPriceUnsafe_raw"] = r.get("result", r.get("error"))
        r = call_price("latestPriceInfoPublishTime(bytes32)", fid, num, f"{name} latestPriceInfoPublishTime")
        e["latestPriceInfoPublishTime"] = r.get("result", r.get("error"))
        out["pythCoreFeeds"][name] = e
    g = {}
    for sig in ("getValidTimePeriod()", "singleUpdateFeeInWei()", "owner()", "chainId()", "pythUpgradableMagic()", "governanceDataSource()"):
        r = eth_call(CORE, selector(sig), num, f"Core {sig}"); g[sig] = r.get("result", r.get("error"))
    # getUpdateFee(bytes[]) with an empty array: offset 0x20, length 0
    r = eth_call(CORE, selector("getUpdateFee(bytes[])") + s32("0x20") + s32("0x0"), num, "Core getUpdateFee([])"); g["getUpdateFee([])"] = r.get("result", r.get("error"))
    out["pythCoreGlobals"] = g
    p = {}
    for sig in ("verification_fee()", "version()", "owner()", "UPGRADE_INTERFACE_VERSION()", "trustedSigners(uint256)", "getTrustedSigner(uint256)"):
        data = selector(sig) + (s32("0x0") if "uint256" in sig else "")
        r = eth_call(PRO, data, num, f"Pro {sig}"); p[sig] = r.get("result", r.get("error"))
    out["pythProProbe"] = p
    json.dump(out, open(os.path.join(HERE, "pyth-onchain-static.json"), "w"), indent=1)
    dump(os.path.join(HERE, "pyth-onchain-static.requests.json"))
    print(json.dumps({k: out[k] for k in ("pinnedBlock", "pythCoreGlobals", "pythProProbe")}, indent=1))
    for k, v in out["pythCoreFeeds"].items(): print(k, v.get("priceFeedExists"), v.get("getPriceUnsafe") or v.get("getPriceUnsafe_raw"), v.get("ageSecondsAtBlockTimestamp"))
    for k, v in out["contracts"].items(): print(k, v["codeBytes"], v["slots"]["impl"], v.get("implementation"))
TOPIC_PRICE_UPDATE = "0xd06a6b7f4918494b3719217d1802786c1f5112a6c1d88fe2cfec00b4584f6aec"   # PriceFeedUpdate(bytes32,uint64,int64,uint64)
TOPIC_BATCH = "0x943f0e8a16c19895fb87cbeb1a349ed86d7f31923089dd36c1a1ed5e300f267b"          # BatchPriceFeedUpdate(uint16,uint64)
def blk_ts(n):
    b = result(rpc("eth_getBlockByNumber", [hex(n), False], f"ts of block {n}"))
    return int(b["timestamp"], 16)
def logs():
    num, bh, ts, _ = pin(); latest = int(num, 16)
    out = {"pinned": {"block": latest, "hash": bh, "timestamp": ts}, "rangeLimit": "RPC rejects spans > 10,000,000 blocks (error -32602 seen with address filter)", "scans": {}}
    # chain age: genesis timestamp for context
    out["block1Timestamp"] = blk_ts(1); out["secondsPerBlockAverage"] = (ts - out["block1Timestamp"]) / latest
    for name, addr in (("PythCore", CORE), ("PythPro", PRO)):
        chunks, total, first, last = [], 0, None, None
        lo = 0
        while lo <= latest:
            hi = min(lo + 9_999_999, latest)
            r = rpc("eth_getLogs", [{"address": addr, "fromBlock": hex(lo), "toBlock": hex(hi)}], f"{name} logs {lo}-{hi}")
            if "result" not in r:
                chunks.append({"from": lo, "to": hi, "error": r}); lo = hi + 1; continue
            n = len(r["result"]); total += n
            if n:
                bl = [int(x["blockNumber"], 16) for x in r["result"]]
                first = min(bl) if first is None else min(first, min(bl)); last = max(bl) if last is None else max(last, max(bl))
            chunks.append({"from": lo, "to": hi, "logs": n, "topic0Counts": {t: sum(1 for x in r["result"] if x["topics"][0] == t) for t in {x["topics"][0] for x in r["result"]}} if n else {}})
            lo = hi + 1
        out["scans"][name] = {"address": addr, "totalLogs": total, "firstLogBlock": first, "lastLogBlock": last, "chunks": chunks}
        if last is not None: out["scans"][name]["lastLogTimestamp"] = blk_ts(last)
        if first is not None: out["scans"][name]["firstLogTimestamp"] = blk_ts(first)
    json.dump(out, open(os.path.join(HERE, "pyth-logs-scan.json"), "w"), indent=1)
    dump(os.path.join(HERE, "pyth-logs-scan.requests.json"))
    print(json.dumps({k: (v if k != "scans" else {n: {kk: vv for kk, vv in s.items() if kk != "chunks"} for n, s in v.items()}) for k, v in out.items()}, indent=1))
def events():
    """Decode Pyth Core PriceFeedUpdate logs, list which feed ids ever updated on-chain, who sent the transactions, and the Pro contract logs."""
    scan = json.load(open(os.path.join(HERE, "pyth-logs-scan.json")))
    out = {"basedOn": "pyth-logs-scan.json", "core": {}, "pro": {}}
    for name, addr, key in (("PythCore", CORE, "core"), ("PythPro", PRO, "pro")):
        allogs = []
        for c in scan["scans"][name]["chunks"]:
            if c.get("logs"):
                r = rpc("eth_getLogs", [{"address": addr, "fromBlock": hex(c["from"]), "toBlock": hex(c["to"])}], f"{name} events {c['from']}-{c['to']}")
                allogs += result(r)
        rec = []
        for x in allogs:
            e = {"block": int(x["blockNumber"], 16), "tx": x["transactionHash"], "topic0": x["topics"][0], "topics": x["topics"], "data": x["data"]}
            if x["topics"][0] == TOPIC_PRICE_UPDATE:
                h = x["data"][2:]
                low = lambda v, b: v & ((1 << b) - 1)
                pt, p, cf = (int(h[i:i + 64], 16) for i in (0, 64, 128))
                p = low(p, 64); p = p - (1 << 64) if p >= 1 << 63 else p
                e.update({"feedId": x["topics"][1], "publishTime": low(pt, 64), "price": p, "conf": low(cf, 64)})
            rec.append(e)
        out[key]["logs"] = rec
        out[key]["count"] = len(rec)
    ups = [e for e in out["core"]["logs"] if "feedId" in e]
    by = {}
    for e in ups: by.setdefault(e["feedId"], []).append(e)
    out["core"]["byFeed"] = {f: {"updates": len(v), "firstPublishTime": min(e["publishTime"] for e in v), "lastPublishTime": max(e["publishTime"] for e in v), "lastBlock": max(e["block"] for e in v)} for f, v in by.items()}
    txs = sorted({e["tx"] for e in ups}); out["core"]["distinctTxs"] = len(txs)
    senders = {}
    sample = txs if len(txs) <= 120 else txs[:40] + txs[-40:] + txs[len(txs) // 2 - 20:len(txs) // 2 + 20]
    for t in sample:
        tx = result(rpc("eth_getTransactionByHash", [t], f"tx {t[:10]}"))
        senders.setdefault(tx["from"], []).append(t)
    out["core"]["senderSampleOfTxs"] = len(sample)
    out["core"]["senders"] = {k: len(v) for k, v in senders.items()}
    ptxs = sorted({e["tx"] for e in out["pro"]["logs"]}); psend = {}
    for t in ptxs:
        tx = result(rpc("eth_getTransactionByHash", [t], f"pro tx {t[:10]}")); psend.setdefault(tx["from"], []).append(t)
    out["pro"]["senders"] = {k: len(v) for k, v in psend.items()}
    json.dump(out, open(os.path.join(HERE, "pyth-events-decoded.json"), "w"), indent=1)
    dump(os.path.join(HERE, "pyth-events-decoded.requests.json"))
    print(json.dumps({"coreByFeed": out["core"]["byFeed"], "distinctTxs": out["core"]["distinctTxs"], "coreSenders": out["core"]["senders"], "proSenders": out["pro"]["senders"]}, indent=1))
STORK = "0xacC0a0cF13571d30B4b8637996F5D6D774d4fd62"          # docs.stork.network/resources/contract-addresses/evm (Robinhood row, Mainnet)
STREAMS_VERIFIER = "0xcE73c8ad08CBDEaCa6078BF0627C8fe0a9a536E7"  # docs.robinhood.com/chain/data-streams
STORK_IDS = {"NVDA_24_5": "0xf6ca4c26ee2527432d22bf6ef9429db0d082b9a0da6ee461283dd01f55f7b61b", "NVDA_24_5_USDC": "0x7d68b8e55387605f562563ee6d95e2d9cdb93dbe01690cd15d231ac132bb2889",
             "SPY_24_5": "0x01ef2ebdefa4a9fbd6f75da48007552e7a56dfd99247197a38205efb3bae634e", "SPY_24_5_USDC": "0x5760aee7a0ebc6c24df00064ea13b085f81c0ea2e97224847d52855dd798f9b3",
             "BTCUSD(control)": "0x7404e3d104ea7841c3d9e6fd20adfe99b4ad586bc08d8f3bd3afef894cf184de", "ETHUSD(control)": "0x59102b37de83bdda9f38ac8254e596f0d9ac61d2035c07936675e87342817160"}
def others():
    num, bh, ts, _ = pin(); out = {"pinnedBlock": {"number": num, "hash": bh, "timestamp": ts}, "stork": {}, "dataStreamsVerifierProxy": {}}
    code = result(rpc("eth_getCode", [STORK, num], "Stork code"))
    out["stork"] = {"address": STORK, "codeBytes": (len(code) - 2) // 2, "codeSha256": sha256_hex(code) if len(code) > 2 else None, "ids": {}, "slots": {}}
    for k, sl in SLOTS.items(): out["stork"]["slots"][k] = result(rpc("eth_getStorageAt", [STORK, sl, num], f"Stork slot {k}"))
    for sig in ("version()", "owner()"):
        r = eth_call(STORK, selector(sig), num, f"Stork {sig}"); out["stork"][sig] = r.get("result", r.get("error"))
    for n, i in STORK_IDS.items():
        r = eth_call(STORK, selector("getTemporalNumericValueUnsafeV1(bytes32)") + s32(i), num, f"Stork unsafe {n}")
        e = {"id": i, "raw": r.get("result", r.get("error"))}
        if "result" in r and len(r["result"]) >= 130:
            h = r["result"][2:]; t = int(h[0:64], 16) & ((1 << 64) - 1); v = int(h[64:128], 16) & ((1 << 192) - 1)
            if v >= 1 << 191: v -= 1 << 192
            e["decoded"] = {"timestampNs": t, "timestampS": t // 10**9, "quantizedValue": v, "valueIf1e18": v / 1e18, "ageSeconds": ts - t // 10**9}
        out["stork"]["ids"][n] = e
    code = result(rpc("eth_getCode", [STREAMS_VERIFIER, num], "Streams verifier code"))
    out["dataStreamsVerifierProxy"] = {"address": STREAMS_VERIFIER, "codeBytes": (len(code) - 2) // 2, "codeSha256": sha256_hex(code) if len(code) > 2 else None, "slots": {k: result(rpc("eth_getStorageAt", [STREAMS_VERIFIER, sl, num], f"verifier slot {k}")) for k, sl in SLOTS.items()}}
    r = eth_call(STREAMS_VERIFIER, selector("typeAndVersion()"), num, "verifier typeAndVersion"); out["dataStreamsVerifierProxy"]["typeAndVersion"] = r.get("result", r.get("error"))
    # Pyth Pro owner address: what is it on this chain?
    po = "0x0708325268df9f66270f1401206434524814508b"
    code = result(rpc("eth_getCode", [po, num], "Pyth Pro owner() address code")); out["pythProOwnerAddressCodeBytes"] = (len(code) - 2) // 2
    json.dump(out, open(os.path.join(HERE, "other-providers-onchain.json"), "w"), indent=1); dump(os.path.join(HERE, "other-providers-onchain.requests.json"))
    print(json.dumps(out, indent=1)[:4000])
TOK = {"SPY": {"token": "0x117cc2133c37B721F49dE2A7a74833232B3B4C0C", "feed": "0x319724394D3A0e3669269846abE664Cd621f9f6A"},
       "NVDA": {"token": "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec", "feed": "0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15"}}   # addresses from ramses-vaults/test/fork + live-config-readback.json
def chainlink():
    num, bh, ts, _ = pin(); out = {"pinnedBlock": {"number": num, "hash": bh, "timestamp": ts}, "assets": {}}
    for a, d in TOK.items():
        e = {"token": d["token"], "feed": d["feed"]}
        def c(to, sig, lab):
            r = eth_call(to, selector(sig), num, f"{a} {lab}"); return r.get("result", r.get("error"))
        e["feed.description"] = c(d["feed"], "description()", "feed description"); e["feed.decimals"] = c(d["feed"], "decimals()", "feed decimals")
        e["feed.latestRoundData_raw"] = c(d["feed"], "latestRoundData()", "latestRoundData")
        if isinstance(e["feed.latestRoundData_raw"], str):
            h = e["feed.latestRoundData_raw"][2:]; w = [int(h[i:i + 64], 16) for i in range(0, 320, 64)]
            e["feed.latestRoundData"] = {"answer": w[1], "updatedAt": w[3], "ageSeconds": ts - w[3]}
        for sig in ("uiMultiplier()", "newUIMultiplier()", "effectiveAt()", "oraclePaused()", "paused()", "decimals()"):
            e["token." + sig] = c(d["token"], sig, "token " + sig)
        out["assets"][a] = e
    json.dump(out, open(os.path.join(HERE, "chainlink-feed-reads.json"), "w"), indent=1); dump(os.path.join(HERE, "chainlink-feed-reads.requests.json"))
    print(json.dumps(out, indent=1))
def sample():
    num, bh, ts, _ = pin()
    rec = {"atUtc": now(), "block": int(num, 16), "blockTimestamp": ts, "feeds": {}}
    for name in ("Crypto.BTC/USD(control)", "Equity.US.SPY/USD", "Equity.US.NVDA/USD", "Equity.Index.SPY/USD(24/7)", "Equity.Index.NVDA/USD(24/7)"):
        r = call_price("getPriceUnsafe(bytes32)", FEEDS[name], num, f"sample {name}")
        e = {"error": r.get("error", {}).get("data")} if "error" in r else decode_price(r["result"])
        if "error" not in r: e["ageSeconds"] = ts - e["publishTime"]
        rec["feeds"][name] = e
    open(os.path.join(HERE, "pyth-freshness-samples.jsonl"), "a").write(json.dumps(rec) + "\n")
    print(json.dumps(rec))
if __name__ == "__main__":
    {"static": static, "logs": logs, "events": events, "others": others, "chainlink": chainlink, "sample": sample}[sys.argv[1]]()
