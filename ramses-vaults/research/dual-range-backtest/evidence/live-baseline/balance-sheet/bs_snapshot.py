"""READ-ONLY pinned balance-sheet snapshot of the live Peridot NVDA/USDG vault + lending markets (chain 4663).
Every read and every simulation uses ONE block number. Simulations are eth_call / eth_simulateV1: local to the node, nothing is signed or sent.
State overrides exist only inside those simulations. On any missing-state error the WHOLE state/sim pass reruns at a new pin.
Output: snapshot.json (reads), sims.json (simulations), logs-*.json (extended Transfer/event history)."""
import json, sys, time
from bs_lib import *

V = "0x280825b2d856706ff7e0d6351ccb2e935e1a9a2f"; A = "0xada73211711e4790bc83b5d6b39f47fe04d276f3"; G = "0xae4d4ddb8dd646951d54fe9b13be23dcb61c6741"
R = "0x806b182b050f7ecf908758dd6bbf91db8b2212af"; PN = "0xa155cccb986774ae818b3f10f07d01d1b7a47b26"; PU = "0x55aed0569c8f0d166d71face57b57c2f2624a563"
C = "0x6148183676e304dbe63a85c350c208da3ceac39c"; NVDA = "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"; USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
PID = "e2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06"; TOKENID = 4148743
PM = "0x8366a39cc670b4001a1121b8f6a443a643e40951"; TL = "0x6797fb8ce049b42c5bc2b42bf76c6d15c7b12498"; GOV = "0x94696d767e65a75581145646960fa0ec886ce5d2"
KEEPER = "0x978e2adf18da8b846552932c0ab2b1193da1fac4"; ETHFEED = "0x78f3556b67e17df817d51ef5a990cdaf09e8d3a9"
AD = {k: v.lower() for k, v in json.load(open("/Users/joshua/Peridot/RobinhoodVaults/deployments/margin-mainnet-live/addresses.json")).items() if isinstance(v, str) and v.startswith("0x") and len(v) == 42}
MV, INS, FLASH, RE, FEE = AD["marginVault"], AD["insuranceFund"], AD["flashVault"], AD["riskEngine"], AD["feeDistributor"]
RAW = HERE.parent / "raw"
T_TRANSFER = topic("Transfer(address,address,uint256)")
Q96 = 1 << 96


# ---------- logs (not pruned): extend the cached history up to the pin ----------
def get_logs(flt, a, b):
    try:
        return call("eth_getLogs", [{**flt, "fromBlock": hex(a), "toBlock": hex(b)}])
    except RpcErr:
        if b <= a:
            raise
        m = (a + b) // 2
        return get_logs(flt, a, m) + get_logs(flt, m + 1, b)


def scan(flt, a, b, step=8_000_000):
    out = []
    while a <= b:
        e = min(a + step - 1, b)
        out += get_logs(flt, a, e)
        a = e + 1
    return out


def load_market_logs():
    d = {}
    for n, m in (("pNVDA", PN), ("pUSDG", PU)):
        src = json.load(open(RAW / f"logs-{n}.json"))
        d[n] = {"logs": src["logs"], "scannedTo": src["scannedTo"], "cacheScannedTo": src["scannedTo"], "address": m}
    return d


def extend(d, to):
    for n, x in d.items():
        if x["scannedTo"] < to:
            x["logs"] = x["logs"] + scan({"address": x["address"]}, x["scannedTo"] + 1, to)
            x["scannedTo"] = to


def holders_from(logs):
    bal = {}
    for l in logs:
        if l["topics"][0] != T_TRANSFER:
            continue
        f, t, v = addr(int(l["topics"][1], 16)), addr(int(l["topics"][2], 16)), int(l["data"], 16)
        bal[f] = bal.get(f, 0) - v
        bal[t] = bal.get(t, 0) + v
    return bal


def borrowers_from(logs):
    tb = topic("Borrow(address,uint256,uint256,uint256)")
    tr = topic("RepayBorrow(address,address,uint256,uint256,uint256)")
    out = set()
    for l in logs:
        if l["topics"][0] == tb:
            out.add(addr(words(l["data"])[0]))
        if l["topics"][0] == tr:
            out.add(addr(words(l["data"])[1]))
    return out


# ---------- pinned pass ----------
class Pin:
    def __init__(self, p):
        self.p, self.B = p, hex(p)
        self.reads, self.res = [], {}

    def q(self, name, to, sig, args="", frm=None):
        tx = {"to": to, "data": sel(sig) + args}
        if frm:
            tx["from"] = frm
        self.reads.append((name, ("eth_call", [tx, self.B])))

    def st(self, name, to, slot):
        self.reads.append((name, ("eth_getStorageAt", [to, slot if isinstance(slot, str) else hex(slot), self.B])))

    def ext(self, name, slot):
        s = slot if isinstance(slot, str) else "0x" + hex(slot)[2:].rjust(64, "0")
        self.q(name, PM, "extsload(bytes32)", s[2:].rjust(64, "0"))

    def raw(self, name, method, params):
        self.reads.append((name, (method, params)))

    def flush(self):
        """Plain reads go through Multicall3 in one eth_call per chunk at the pin. *Current reads (which accrue inside the call) are grouped apart
        so that stored values are never read after a simulated accrual. Non eth_call reads and reads with an explicit from are serial."""
        plain, cur, other = [], [], []
        for n, (m, pr) in self.reads:
            if m == "eth_call" and "from" not in pr[0]:
                (cur if ("Current" in n or ".current." in n) else plain).append((n, pr[0]["to"], pr[0]["data"]))
            else:
                other.append((n, m, pr))
        for grp in (plain, cur):
            for i in range(0, len(grp), 70):
                part = grp[i:i + 70]
                r = call("eth_call", [{"to": MC3, "data": mc_encode([(t, d) for _, t, d in part])}, self.B])
                for (n, _, _), (ok, ret) in zip(part, mc_decode(r)):
                    self.res[n] = ret if ok else {"error": ret}
        for n, m, pr in other:
            r = batch([(m, pr)])[0]
            self.res[n] = r.get("r") if "r" in r else {"error": r["e"]}
        self.reads = []

    def w(self, n, i=0):
        v = self.res[n]
        if isinstance(v, dict):
            raise RuntimeError(f"read failed {n}: {v}")
        return words(v)[i] if len(v) > 2 else 0

    def ws(self, n):
        v = self.res[n]
        if isinstance(v, dict):
            raise RuntimeError(f"read failed {n}: {v}")
        return words(v)


def pinned_pass(mlogs):
    head = int(call("eth_blockNumber", []), 16)
    P = head - 2
    x = Pin(P)
    t0 = time.time()
    blk = call("eth_getBlockByNumber", [x.B, False])
    ts = int(blk["timestamp"], 16)
    extend(mlogs, P)
    S = {"pin": {"l2": P, "hash": blk["hash"], "timestamp": ts, "timestampUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)), "native_l1BlockNumber": int(blk["l1BlockNumber"], 16),
                 "baseFeePerGas": int(blk["baseFeePerGas"], 16), "headAtStart": head}}
    hold = {n: holders_from(mlogs[n]["logs"]) for n in mlogs}
    bor = set()
    for n in mlogs:
        bor |= borrowers_from(mlogs[n]["logs"])
    # isolated accounts known from earlier state read; re-verified below against the risk engine
    iso = [a.lower() for a in json.load(open(HERE.parent / "raw" / "borrowers.json"))["isolatedMarginAccountsRegistered"]]
    mv_logs = scan({"address": MV}, 60_000_000, P)
    T_DEP, T_WD = topic("Deposited(address,address,uint256)"), topic("Withdrawn(address,address,uint256)")
    T_LOCK = topic("PositionLocked(uint256,address,address,uint256)")
    mv_users = sorted({addr(int(l["topics"][1], 16)) for l in mv_logs if l["topics"][0] in (T_DEP, T_WD)})
    S["marginVaultLogs"] = {"count": len(mv_logs), "users": mv_users}
    json.dump(mv_logs, open(HERE / "logs-marginVault.json", "w"))

    q = x.q
    # -- vault
    q("v.pairConfig", V, "pairConfig(bytes32)", PID); q("v.ledger", V, "ledger(bytes32)", PID)
    for tn, t in (("stock", NVDA), ("usdg", USDG)):
        for f in ("accountedAssets", "liquidAssets", "withdrawableAssets"):
            q(f"v.{f}.{tn}", V, f"{f}(bytes32,address)", PID + pa(t))
    q("v.totalPairAssets", V, "totalPairAssets(bytes32)", PID)
    q("v.rangePolicy", V, "rangePolicy(bytes32)", PID); q("v.rangeState", V, "rangeState(bytes32)", PID)
    q("v.aggPrincipal", V, "aggregateUsdgPrincipal(address)", pa(USDG)); q("v.aggCap", V, "aggregateUsdgDepositCap(address)", pa(USDG))
    for r_ in ("KEEPER_ROLE", "GUARDIAN_ROLE", "CONFIG_ROLE"):
        q(f"v.{r_}", V, f"{r_}()")
    # -- adapter/pool
    q("a.positionState", A, "positionState(bytes32)", PID); q("a.positionTicks", A, "positionTicks(bytes32)", PID); q("a.poolKey", A, "poolKey(bytes32)", PID)
    q("a.removalTolerance", A, "removalTolerance(bytes32)", PID); q("a.positionManager", A, "positionManager()"); q("a.universalRouter", A, "universalRouter()")
    # -- guard
    q("g.feedConfig", G, "feedConfig(bytes32)", PID); q("g.prices", G, "pricesUSD18(bytes32)", PID)
    q("g.maxDev", G, "maxPriceDeviationBps(bytes32)", PID); q("g.maxRemDev", G, "maxRemovalDeviationBps(bytes32)", PID)
    # -- reserve
    q("r.config", R, "reserveConfig(bytes32)", PID); q("r.daily", R, "dailyUsage(bytes32)", PID)
    for tn, t in (("NVDA", NVDA), ("USDG", USDG)):
        q(f"r.available.{tn}", R, "available(bytes32,address)", PID + pa(t)); q(f"r.accounted.{tn}", R, "accountedBalance(address)", pa(t))
    # -- token balances
    balance_addrs = {"vault": V, "adapter": A, "reserve": R, "pNVDA": PN, "pUSDG": PU, "timelock": TL, "governor": GOV, "keeper": KEEPER, "marginVault": MV, "insurance": INS, "flashVault": FLASH,
                     "feeDistributor": FEE, "guard": G, "settlementLib": "0x813abfec0de50f8674798cbab72ed7b5d8ccb9cb", "swapModule": AD["swapModule"], "executor": AD["executor"], "liquidator": AD["liquidator"], "riskEngine": RE, "PoolManager": PM}
    for h, a_ in balance_addrs.items():
        for tn, t in (("NVDA", NVDA), ("USDG", USDG)):
            q(f"bal.{h}.{tn}", t, "balanceOf(address)", pa(a_))
    q("tok.NVDA.decimals", NVDA, "decimals()"); q("tok.USDG.decimals", USDG, "decimals()"); q("tok.NVDA.oraclePaused", NVDA, "oraclePaused()")
    q("ethfeed", ETHFEED, "latestRoundData()")
    # -- markets
    for mn, m in (("pNVDA", PN), ("pUSDG", PU)):
        for f in ("totalSupply", "exchangeRateStored", "exchangeRateCurrent", "totalBorrows", "totalBorrowsCurrent", "totalReserves", "getCash", "reserveFactorMantissa", "accrualBlockNumber", "borrowIndex",
                  "vaultAccountedAssets", "vaultLiquidAssets", "vaultWithdrawableAssets", "totalManagedAssets", "vaultBufferMantissa", "vaultPaused", "vaultOperator", "cumulativeVaultLoss", "borrowRatePerBlock", "supplyRatePerBlock", "decimals", "initialExchangeRateMantissa", "totalBorrowShares"):
            q(f"m.{mn}.{f}", m, f + "()")
        q(f"c.{mn}.markets", C, "markets(address)", pa(m)); q(f"c.{mn}.mintPaused", C, "mintGuardianPaused(address)", pa(m)); q(f"c.{mn}.borrowPaused", C, "borrowGuardianPaused(address)", pa(m))
        allh = sorted(a for a, b in hold[mn].items() if a != addr(0) and a != m) + [m]
        S.setdefault("holderList", {})[mn] = allh
        for h in allh:
            q(f"hb.{mn}.{h}", m, "balanceOf(address)", pa(h))
    q("c.oracle", C, "oracle()")
    allaccts = sorted(bor | set(iso) | set(hold["pNVDA"]) | set(hold["pUSDG"]) - {addr(0), PN, PU})
    S["accountList"] = allaccts
    for a_ in allaccts:
        for mn, m in (("pNVDA", PN), ("pUSDG", PU)):
            q(f"d.{mn}.stored.{a_}", m, "borrowBalanceStored(address)", pa(a_)); q(f"d.{mn}.current.{a_}", m, "borrowBalanceCurrent(address)", pa(a_))
        q(f"liq.{a_}", C, "getAccountLiquidity(address)", pa(a_)); q(f"assetsIn.{a_}", C, "getAssetsIn(address)", pa(a_))
        x.raw(f"code.{a_}", "eth_getCode", [a_, x.B]); x.raw(f"nonce.{a_}", "eth_getTransactionCount", [a_, x.B]); x.raw(f"eth.{a_}", "eth_getBalance", [a_, x.B])
        q(f"re.accounts.{a_}", RE, "accounts(address)", pa(a_)); q(f"re.metrics.{a_}", RE, "getMetrics(address)", pa(a_))
        q(f"mv.free.{a_}", MV, "freeBalance(address,address)", pa(a_) + pa(PU)); q(f"mv.locked.{a_}", MV, "lockedBalance(address,address)", pa(a_) + pa(PU))
    for u in mv_users:
        q(f"mv.free.{u}", MV, "freeBalance(address,address)", pa(u) + pa(PU)); q(f"mv.locked.{u}", MV, "lockedBalance(address,address)", pa(u) + pa(PU))
    q("mv.totalFree", MV, "totalFreeBalance(address)", pa(PU)); q("mv.totalLocked", MV, "totalLockedBalance(address)", pa(PU))
    q("mv.totalFree.pNVDA", MV, "totalFreeBalance(address)", pa(PN)); q("mv.totalLocked.pNVDA", MV, "totalLockedBalance(address)", pa(PN))
    for i_ in range(1, 8):
        q(f"mv.lock.{i_}", MV, "positionLocks(uint256)", pi(i_))
    x.flush()
    # -- second round: values depending on the first
    pk = x.ws("a.poolKey"); pos = x.ws("a.positionState"); ticks = x.ws("a.positionTicks")
    tl, tu = sword(ticks[0]), sword(ticks[1])
    pkenc = pa(addr(pk[0])) + pa(addr(pk[1])) + pi(pk[2]) + pi(pk[3]) + pa(addr(pk[4]))
    poolId = keccak_hex("0x" + "".join((pa(addr(pk[i])) if i in (0, 1, 4) else pi(pk[i])) for i in range(5)))
    S["poolId"] = poolId
    fc = x.ws("g.feedConfig")
    posmgr = addr(x.w("a.positionManager"))
    q("g.validatePool", G, "validatePoolPrice(bytes32,(address,address,uint24,int24,address))", PID + pkenc)
    q("g.validateRemoval", G, "validateRemovalPrice(bytes32,(address,address,uint24,int24,address))", PID + pkenc)
    q("feed.latest", addr(fc[2]), "latestRoundData()"); q("feed.decimals", addr(fc[2]), "decimals()")
    q("pm.ownerOf", posmgr, "ownerOf(uint256)", pi(TOKENID)); q("pm.liq", posmgr, "getPositionLiquidity(uint256)", pi(TOKENID))
    orc = addr(x.w("c.oracle"))
    for mn, m in (("pNVDA", PN), ("pUSDG", PU)):
        q(f"lo.{mn}", orc, "getUnderlyingPrice(address)", pa(m))
    for r_ in ("KEEPER_ROLE", "GUARDIAN_ROLE", "CONFIG_ROLE"):
        role = "%064x" % x.w(f"v.{r_}")
        for an, a_ in (("keeper", KEEPER), ("governor", GOV), ("timelock", TL)):
            q(f"v.hasRole.{r_}.{an}", V, "hasRole(bytes32,address)", role + pa(a_))
    # PoolManager storage (v4 StateLibrary layout: pools mapping at slot 6)
    st = int(keccak_hex("0x" + poolId[2:] + pi(6)), 16)
    x.ext("pool.slot0", st); x.ext("pool.fg0", st + 1); x.ext("pool.fg1", st + 2); x.ext("pool.liq", st + 3)
    ticks_map = st + 4; bitmap_map = st + 5; pos_map = st + 6
    def tick_slot(t):
        return int(keccak_hex("0x" + pi(t) + pi(ticks_map)), 16)
    for nm, t in (("lower", tl), ("upper", tu)):
        sl = tick_slot(t)
        x.ext(f"tick.{nm}.w0", sl); x.ext(f"tick.{nm}.fo0", sl + 1); x.ext(f"tick.{nm}.fo1", sl + 2)
    def int24_hex(t):
        return (t & 0xffffff).to_bytes(3, "big").hex()
    pkey = keccak_hex("0x" + posmgr[2:] + int24_hex(tl) + int24_hex(tu) + pi(TOKENID))
    sl = int(keccak_hex("0x" + pkey[2:] + pi(pos_map)), 16)
    x.ext("position.liq", sl); x.ext("position.fgi0", sl + 1); x.ext("position.fgi1", sl + 2)
    x.flush()
    s0 = x.w("pool.slot0"); sqrtp = s0 & ((1 << 160) - 1); tick = (s0 >> 160) & 0xffffff; tick = tick - (1 << 24) if tick >= (1 << 23) else tick
    S["pool"] = {"sqrtPriceX96": sqrtp, "tick": tick, "protocolFee": (s0 >> 184) & 0xffffff, "lpFee": (s0 >> 208) & 0xffffff, "liquidity": x.w("pool.liq"), "tickSpacing": sword(pk[3]), "fee": pk[2], "currency0": addr(pk[0]), "currency1": addr(pk[1])}
    spacing = sword(pk[3])
    # bitmap words around the tick (swap path: NVDA(token1) in moves tick up; USDG in moves tick down)
    comp = tick // spacing
    wp = comp >> 8
    for w_ in range(wp - 2, wp + 4):
        x.ext(f"bitmap.{w_}", int(keccak_hex("0x" + pi(w_) + pi(bitmap_map)), 16))
    # -- scenario prices for sensitivity and tick endpoints, at the on-chain position via positionStateAt
    vr = x.ws("g.validateRemoval") if not isinstance(x.res["g.validateRemoval"], dict) else None
    S["guardRemovalOk"] = vr is not None
    ref = vr[2] if vr else None
    prices = x.ws("g.prices") if not isinstance(x.res["g.prices"], dict) else None
    if ref is None:
        raise RuntimeError("guard reverted at pin: " + json.dumps(x.res["g.validateRemoval"]))
    q("a.stateAtRef", A, "positionStateAt(bytes32,uint160)", PID + pi(ref))
    def scen_sqrt(px_wad):
        # same as guard._referenceSqrtPriceX96, USDG=currency0, so raw token1/token0 = 1e18*(1e18/px)/1e6 wad
        inverse = (10**18 * 10**18) // px_wad
        raw = inverse * 10**18 // 10**6
        return isqrt((raw << 128) // 10**18) << 32
    scen = {}
    for lbl, f in (("-30%", 700), ("-20%", 800), ("-10%", 900), ("-5%", 950), ("0% (recomputed)", 1000), ("+5%", 1050), ("+10%", 1100), ("+20%", 1200), ("+26%", 1260), ("+50%", 1500)):
        px = prices[0] * f // 1000
        scen[lbl] = {"priceWad": px, "sqrtX96": scen_sqrt(px)}
        q(f"scen.{lbl}", A, "positionStateAt(bytes32,uint160)", PID + pi(scen[lbl]["sqrtX96"]))
    def tick_sqrt(t):
        # TickMath via python big ints
        r = pow_tick(t)
        return r
    for nm, t in (("tickLower", tl), ("tickUpper", tu)):
        q(f"tickstate.{nm}", A, "positionStateAt(bytes32,uint160)", PID + pi(tick_sqrt(t)))
    S["scenarios"] = scen
    S["tickSqrt"] = {"tickLower": tick_sqrt(tl), "tickUpper": tick_sqrt(tu)}
    # solve USDG-in-position == usdgPrincipal by bisection over positionStateAt is done offline from L and ticks.
    x.flush()
    # initialised ticks found in bitmap words
    initt = []
    for w_ in range(wp - 2, wp + 4):
        bm = x.w(f"bitmap.{w_}")
        for b in range(256):
            if bm >> b & 1:
                initt.append(((w_ << 8) + b) * spacing)
    S["initialisedTicksNearby"] = initt
    for t in initt:
        x.ext(f"tickinfo.{t}", tick_slot(t))
    x.flush()
    # storage slots for overrides: find mapping bases by matching known values
    for s_ in range(0, 80):
        x.st(f"probe.pc.{s_}", V, keccak_hex("0x" + PID + pi(s_)))
    x.flush()
    pc_slot = next(s_ for s_ in range(0, 80) if int(x.res[f"probe.pc.{s_}"], 16) == int(NVDA, 16))
    base = int(keccak_hex("0x" + PID + pi(pc_slot)), 16)
    x.st("pc.flags", V, hex(base + 5))
    rangeslots = {}
    for s_ in range(0, 120):
        x.st(f"probe.rs.{s_}", V, keccak_hex("0x" + PID + pi(s_)))
    x.flush()
    centre = x.ws("v.rangeState")[0] & 0xffffff
    rs_slot = next((s_ for s_ in range(0, 120) if s_ != pc_slot and (int(x.res[f"probe.rs.{s_}"], 16) & 0xffffff) == centre and ((int(x.res[f"probe.rs.{s_}"], 16) >> 24) & 0xff) == 1), None)
    S["slots"] = {"pairConfigMapping": pc_slot, "pairConfigFlagsSlot": hex(base + 5), "pairConfigFlagsWord": x.res["pc.flags"], "rangeStateMapping": rs_slot,
                  "rangeStateWord0Slot": keccak_hex("0x" + PID + pi(rs_slot)) if rs_slot is not None else None, "rangeStateWord0": x.res.get(f"probe.rs.{rs_slot}")}
    raw = {k: v for k, v in x.res.items() if not k.startswith("probe.")}
    S["reads"] = raw
    S["holders_fromLogs"] = {k: {a: b for a, b in v.items() if b != 0} for k, v in hold.items()}
    S["logsScannedTo"] = {k: v["scannedTo"] for k, v in mlogs.items()}
    S["timings"] = {"readsSec": time.time() - t0}
    sims = run_sims(x, S, prices, ref)
    S["simsAborted"] = sims.get("_aborted")
    S["timings"]["totalSec"] = time.time() - t0
    blk2 = call("eth_getBlockByNumber", [x.B, False])
    S["pin"]["hashRecheckedEqual"] = blk2["hash"] == blk["hash"]
    S["pin"]["headAtEnd"] = int(call("eth_blockNumber", []), 16)
    return S, sims


def pow_tick(tick):
    """Uniswap TickMath.getSqrtPriceAtTick."""
    a = abs(tick)
    r = 0xfffcb933bd6fad37aa2d162d1a594001 if a & 1 else 1 << 128
    for bit, m in ((2, 0xfff97272373d413259a46990580e213a), (4, 0xfff2e50f5f656932ef12357cf3c7fdcc), (8, 0xffe5caca7e10e4e61c3624eaa0941cd0), (16, 0xffcb9843d60f6159c9db58835c926644),
                   (32, 0xff973b41fa98c081472e6896dfb254c0), (64, 0xff2ea16466c96a3843ec78b326b52861), (128, 0xfe5dee046a99a2a811c461f1969c3053), (256, 0xfcbe86c7900a88aedcffc83b479aa3a4),
                   (512, 0xf987a7253ac413176f2b074cf7815e54), (1024, 0xf3392b0822b70005940c7a398e4b70f3), (2048, 0xe7159475a2c29b7443b29c7fa6e889d9), (4096, 0xd097f3bdfd2022b8845ad8f792aa5825),
                   (8192, 0xa9f746462d870fdf8a65dc1f90e061e5), (16384, 0x70d869a156d2a1b890bb3df62baf32f7), (32768, 0x31be135f97d08fd981231505542fcfa6), (65536, 0x9aa508b5b7a84e1c677de54f3e99bc9),
                   (131072, 0x5d6af8dedb81196699c329225ee604), (262144, 0x2216e584f5fa1ea926041bedfe98), (524288, 0x48a170391f7dc42444e8fa2)):
        if a & bit:
            r = (r * m) >> 128
    if tick > 0:
        r = ((1 << 256) - 1) // r
    return (r + (1 << 32) - 1) >> 32


# ---------- simulations ----------
EVT = {}
for s in ("PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)", "FeesProcessed(bytes32,uint256,uint256,uint256,uint256)", "Withdrawal(bytes32,address,address,uint256,uint256,uint256)",
          "SettlementSwap(bytes32,address,uint256,uint256)", "ReserveCovered(bytes32,address,uint256,uint256,uint256,uint256)", "ReserveFunded(bytes32,address,address,uint256)", "VaultWithdrawal(uint256,uint256,uint256)", "VaultWithdrawalFailed(uint256)",
          "VaultDeposit(uint256,uint256)", "VaultDepositFailed(uint256)", "Redeem(address,uint256,uint256)", "EmergencyLiquidityDecreased(bytes32,uint128,uint256,uint256)", "Recentered(bytes32,int24,int24,int24,int24,uint128,int24)", "LiquidityRebalanced(bytes32,uint128,uint256,uint256)", "PairPauseUpdated(bytes32,bool,bool,bool)"):
    EVT[topic(s)] = s.split("(")[0]


def tx(frm, to, sig, args=""):
    return {"from": frm, "to": to, "data": sel(sig) + args}


def post_calls():
    return [("ledger", tx(GOV, V, "ledger(bytes32)", PID)), ("position", tx(GOV, A, "positionState(bytes32)", PID)), ("ticks", tx(GOV, A, "positionTicks(bytes32)", PID)),
            ("pUSDG.cash", tx(GOV, USDG, "balanceOf(address)", pa(PU))), ("pNVDA.cash", tx(GOV, NVDA, "balanceOf(address)", pa(PN))),
            ("pUSDG.ts", tx(GOV, PU, "totalSupply()")), ("pNVDA.ts", tx(GOV, PN, "totalSupply()")), ("pUSDG.er", tx(GOV, PU, "exchangeRateStored()")), ("pNVDA.er", tx(GOV, PN, "exchangeRateStored()")),
            ("reserve.USDG", tx(GOV, R, "available(bytes32,address)", PID + pa(USDG))), ("reserve.NVDA", tx(GOV, R, "available(bytes32,address)", PID + pa(NVDA))), ("reserve.daily", tx(GOV, R, "dailyUsage(bytes32)", PID)),
            ("v.USDG", tx(GOV, USDG, "balanceOf(address)", pa(V))), ("v.NVDA", tx(GOV, NVDA, "balanceOf(address)", pa(V))), ("wd.USDG", tx(GOV, V, "withdrawableAssets(bytes32,address)", PID + pa(USDG))), ("wd.NVDA", tx(GOV, V, "withdrawableAssets(bytes32,address)", PID + pa(NVDA)))]


def run_sims(x, S, prices, ref):
    B = x.B
    ts = S["pin"]["timestamp"]
    deadline = pi(ts + 120)
    bal = lambda n: x.w(n)

    def sims(reqs):
        """reqs: list of (calls, overrides). Each is an independent simulation at the pin."""
        items = []
        for calls, ov in reqs:
            bsc = {"calls": calls}
            if ov:
                bsc["stateOverrides"] = ov
            items.append(("eth_simulateV1", [{"blockStateCalls": [bsc], "validation": False}, B]))
        rs = batch(items, chunk=12)
        out = []
        for r in rs:
            if "e" in r:
                out.append({"rpcError": r["e"]})
            else:
                blk = r["r"][0]
                out.append({"number": blk["number"], "l1": blk.get("l1BlockNumber"), "timestamp": blk.get("timestamp"), "calls": blk["calls"]})
        return out

    def trim(c):
        d = {"status": int(c["status"], 16), "gasUsed": int(c["gasUsed"], 16)}
        if c.get("returnData") not in (None, "0x"):
            d["ret"] = c["returnData"]
        if c.get("error"):
            d["error"] = c["error"]
        lg = []
        for l in c.get("logs", []):
            n = EVT.get(l["topics"][0])
            if n:
                lg.append({"event": n, "addr": l["address"], "topics": l["topics"][1:], "data": words(l["data"]) if len(l["data"]) > 2 else []})
        if lg:
            d["logs"] = lg
        return d

    def scenario(name, steps, ov=None, reads=True):
        """steps: [(label, call)]. Post-state reads go in one Multicall3 call appended to the same simulation."""
        calls = [c for _, c in steps]
        pcs = post_calls() if reads else []
        tail = [{"from": GOV, "to": MC3, "data": mc_encode([(c["to"], c["data"]) for _, c in pcs])}] if pcs else []
        r = sims([(calls + tail, ov)])[0]
        if "rpcError" in r:
            return {"name": name, "rpcError": r["rpcError"]}
        tr = {"simBlock": {"number": r["number"], "l1": r["l1"], "timestamp": r["timestamp"]}, "steps": []}
        for (lbl, _), c in zip(steps, r["calls"]):
            t = trim(c)
            t["label"] = lbl
            tr["steps"].append(t)
        if pcs:
            rets = mc_decode(r["calls"][len(steps)]["returnData"])
            tr["post"] = {lbl: (words(ret) if ok and len(ret) > 2 else []) for (lbl, _), (ok, ret) in zip(pcs, rets)}
        return tr

    class Dump(dict):
        def __setitem__(self, k, v):
            dict.__setitem__(self, k, v)
            print(f"[{time.time() - x_t0[0]:.0f}s] {k}", flush=True)
            json.dump(self, open(HERE / "sims.partial.json", "w"), indent=0, default=str)
    OUT = Dump()
    # 0. probe: override and from at a hex block
    fl = int(S["slots"]["pairConfigFlagsWord"], 16)
    unp = None
    # flags word layout: u64 age|u32|u32|u16|u16|u16|u8|u8|bool alloc|bool swaps|bool emerg|bool exists -> swapsPaused byte offset = 8+4+4+2+2+2+1+1+1 = 25
    OFFSET_SWAPS = 25
    assert (fl >> (8 * OFFSET_SWAPS)) & 0xff == 1, "swapsPaused byte not where expected"
    unp = fl & ~(0xff << (8 * OFFSET_SWAPS))
    OV_SWAPS = {V: {"stateDiff": {S["slots"]["pairConfigFlagsSlot"]: "0x" + pi(unp)}}}
    probe = batch([("eth_call", [{"to": V, "data": sel("pairConfig(bytes32)") + PID}, B, OV_SWAPS]), ("eth_call", [{"to": A, "from": V, "data": sel("collectFees(bytes32,uint256)") + PID + deadline}, B])])
    OUT["probe.pairConfigUnderSwapsOverride"] = probe[0]
    OUT["adapter.collectFees(from=vault,eth_call)"] = probe[1]
    # 1. keeper simulations
    k = lambda sig, args="": tx(KEEPER, V, sig, args)
    OUT["vault.collectFees"] = scenario("collect", [("collectFees", k("collectFees(bytes32,uint256)", PID + deadline))])
    OUT["vault.checkpoint"] = scenario("checkpoint", [("checkpoint", k("checkpoint(bytes32,uint256)", PID + deadline))])
    OUT["vault.rebalance"] = scenario("rebalance", [("checkpoint", k("checkpoint(bytes32,uint256)", PID + deadline)), ("rebalance", k("rebalance(bytes32,uint256)", PID + deadline))])
    OUT["vault.recenter.asIs"] = scenario("recenter", [("recenter", k("recenter(bytes32,uint256)", PID + deadline))])
    # recenter forced eligible by overriding the stored centre tick inside the simulation only
    if S["slots"]["rangeStateWord0Slot"]:
        w0 = int(S["slots"]["rangeStateWord0"], 16)
        cen = w0 & 0xffffff
        cen2 = (cen - 1000) & 0xffffff
        ov = {V: {"stateDiff": {S["slots"]["rangeStateWord0Slot"]: "0x" + pi((w0 & ~0xffffff) | cen2)}}}
        OUT["vault.recenter.forcedByOverride"] = scenario("recenter-f", [("recenter", k("recenter(bytes32,uint256)", PID + deadline))], ov)
    # 2. direct vault withdrawal from each side account (vault-level truth)
    ledger = x.ws("v.ledger")
    sp, up, si, ui = ledger[:4]
    posst = x.ws("a.stateAtRef")
    w_us = [1_000_000, 3_000_000, 6_000_000, posst[3], up // 2, up]
    OUT["vault.withdraw.pUSDG"] = {str(a): scenario("w", [("withdrawForSide", tx(PU, V, "withdrawForSide(bytes32,address,uint256,address,uint256)", PID + pa(USDG) + pi(a) + pa(PU) + deadline))]) for a in w_us}
    OUT["vault.withdraw.pNVDA"] = {str(a): scenario("w", [("withdrawForSide", tx(PN, V, "withdrawForSide(bytes32,address,uint256,address,uint256)", PID + pa(NVDA) + pi(a) + pa(PN) + deadline))]) for a in (si // 2, min(si, sp), sp)}

    # 3. redeem search. Holder redeem capacity found with parallel k-ary search.
    LAST = {"err": None}
    def ru(holder, m, amt):
        return tx(holder, m, "redeemUnderlying(uint256)", pi(amt))
    def test(prefix, holder, m, under, amts, ov):
        calls = [c for _, c in prefix]
        reqs = []
        for a in amts:
            reqs.append((calls + [ru(holder, m, a)], ov))
        rs = sims(reqs)
        ok = []
        for a, r in zip(amts, rs):
            if "rpcError" in r:
                raise RuntimeError(json.dumps(r["rpcError"]))
            c = r["calls"][len(calls)]
            ok.append(int(c["status"], 16) == 1)
            if not ok[-1]:
                LAST["err"] = c.get("error")
        return ok
    def max_redeem(prefix, holder, m, under, hi, res, ov=None, cap=None):
        """Largest redeemUnderlying that succeeds after `prefix` (resolution res). Full claim first, then a bracket around the capacity hint."""
        if hi <= 0:
            return 0
        if test(prefix, holder, m, under, [hi], ov)[0]:
            return hi
        lo = 0
        if cap is not None and 0 < cap < hi:
            if test(prefix, holder, m, under, [cap], ov)[0]:
                lo, hi = cap, min(hi, cap + cap // 10 + res)
            else:
                hi = cap
                low = cap * 9 // 10
                if low > res and test(prefix, holder, m, under, [low], ov)[0]:
                    lo = low
        if lo == 0:
            if not test(prefix, holder, m, under, [res], ov)[0]:
                return 0
            lo = res
        while hi - lo > res:
            mid = (lo + hi) // 2
            if test(prefix, holder, m, under, [mid], ov)[0]:
                lo = mid
            else:
                hi = mid
        return lo
    er_c = {mn: x.w(f"m.{mn}.exchangeRateCurrent") for mn in ("pNVDA", "pUSDG")}
    def claim(mn, h):
        return x.w(f"hb.{mn}.{h}") * er_c[mn] // 10**18
    RES = {"pUSDG": 1000, "pNVDA": 10**12}
    UND = {"pUSDG": USDG, "pNVDA": NVDA}
    MK = {"pUSDG": PU, "pNVDA": PN}
    # single-holder capacity for every holder
    OUT["redeem.singleHolderMax"] = {}
    for mn in ("pUSDG", "pNVDA"):
        for h in S["holderList"][mn]:
            bal_h = x.w(f"hb.{mn}.{h}")
            if bal_h == 0 or h == MK[mn]:
                continue
            cl = claim(mn, h)
            # the holder cannot burn more than its shares: largest provable try is slightly under the claim
            hi = cl - max(1, cl // 10**6 if mn == "pNVDA" else 1)
            mx = max_redeem([], h, MK[mn], UND[mn], hi, RES[mn])
            OUT["redeem.singleHolderMax"][f"{mn}.{h}"] = {"shares": bal_h, "claimAtERcurrent": cl, "maxRedeemUnderlyingFound": mx, "fullClaimTried": hi, "lastRevert": LAST["err"] if mx < hi else None}
    # sizes grid from the biggest pUSDG holder, with post state to see where the money comes from
    biggest = max((h for h in S["holderList"]["pUSDG"] if h != PU), key=lambda h: x.w(f"hb.pUSDG.{h}"))
    OUT["redeem.pUSDG.grid"] = {"holder": biggest, "results": {}}
    cash = bal("bal.pUSDG.USDG")
    for a in (1_000_000, 5_000_000, cash, cash + 1, cash + 1_000_000, cash + 3_000_000, cash + 5_000_000):
        OUT["redeem.pUSDG.grid"]["results"][str(a)] = scenario("r", [("redeemUnderlying", ru(biggest, PU, a))])
    bigN = max((h for h in S["holderList"]["pNVDA"] if h != PN), key=lambda h: x.w(f"hb.pNVDA.{h}"))
    OUT["redeem.pNVDA.grid"] = {"holder": bigN, "results": {}}
    cashN = bal("bal.pNVDA.NVDA")
    for a in (10**15, 5 * 10**15, 10**16, cashN, cashN + 10**15, claim("pNVDA", bigN) - 10**12):
        OUT["redeem.pNVDA.grid"]["results"][str(a)] = scenario("r", [("redeemUnderlying", ru(bigN, PN, a))])

    # 4. bank runs: sequential max redemption by every holder, both orderings, as-is / swaps on / reserve funded.
    def bank_run(order, ov=None, prefix0=None, hint=False):
        prefix = list(prefix0 or [])
        rows = []
        for mn, h in order:
            bal_h = x.w(f"hb.{mn}.{h}")
            if bal_h == 0:
                continue
            cl = claim(mn, h)
            hi = cl - max(1, cl // 10**6 if mn == "pNVDA" else 1)
            st_ = scenario("hint", prefix, ov)
            if "post" not in st_:
                rows.append({"market": mn, "holder": h, "claim": cl, "error": st_})
                continue
            p_ = st_["post"]
            cap = (p_["pUSDG.cash"][0] + p_["ledger"][3] + p_["position"][3] + p_["reserve.USDG"][0]) if mn == "pUSDG" else (p_["pNVDA.cash"][0] + p_["ledger"][2] + p_["position"][2] + p_["reserve.NVDA"][0])
            mx = max_redeem(prefix, h, MK[mn], UND[mn], hi, RES[mn], ov, cap if hint else None)
            rows.append({"market": mn, "holder": h, "claim": cl, "redeemed": mx, "capHint": cap, "lastRevert": LAST["err"] if mx < hi else None})
            if mx > 0:
                prefix.append((f"{mn}.{h}", ru(h, MK[mn], mx)))
        fin = scenario("final", prefix, ov)
        return {"rows": rows, "final": fin}
    sortd = lambda mn: sorted((h for h in S["holderList"][mn] if h != MK[mn]), key=lambda h: -x.w(f"hb.{mn}.{h}"))
    U_ord = [("pUSDG", h) for h in sortd("pUSDG")]
    N_ord = [("pNVDA", h) for h in sortd("pNVDA")]
    try:
        OUT["bankrun.USDGfirst"] = bank_run(U_ord + N_ord, hint=True)
        OUT["bankrun.NVDAfirst"] = bank_run(N_ord + U_ord, hint=True)
        S["timings"]["afterBankrunSec"] = time.time() - x_t0[0]

        # 5. route ii: settlement swap (override swapsPaused=false, simulation only)
        us_w = tx(PU, V, "withdrawForSide(bytes32,address,uint256,address,uint256)", PID + pa(USDG) + pi(up) + pa(PU) + deadline)
        OUT["route.swap.fullSideWithdraw"] = scenario("swap", [("withdrawForSide(principal)", us_w)], OV_SWAPS)
        OUT["route.swap.bankrun.USDGfirst"] = bank_run(U_ord + N_ord, OV_SWAPS)
        # 6. route iii: reserve funding through the real permissionless deposit path; simulated donor is the PoolManager's USDG balance
        def donate(x_):
            return [("approve", tx(PM, USDG, "approve(address,uint256)", pa(R) + pi(x_))), ("reserve.deposit", tx(PM, R, "deposit(bytes32,address,uint256)", PID + pa(USDG) + pi(x_)))]
        # shortage at oracle ref
        short = up - (ui + posst[3])
        X = short + 1_000_000
        OUT["route.reserve.params"] = {"shortage": short, "deposit": X}
        steps = donate(X)
        OUT["route.reserve.deposit+withdraw1"] = scenario("res1", steps + [("withdraw.1", us_w)])
        # repeated side withdrawals: remaining principal shrinks; request the whole principal each time. Requested amount is re-encoded after reading principal from a prior run.
        seq = list(steps)
        trace = []
        cur_up = up
        for i in range(1, 7):
            call_ = ("withdraw.%d" % i, tx(PU, V, "withdrawForSide(bytes32,address,uint256,address,uint256)", PID + pa(USDG) + pi(cur_up) + pa(PU) + deadline))
            r = scenario("resN", seq + [call_])
            last = r["steps"][-1]
            post = r["post"]
            ev = {e["event"]: e for e in last.get("logs", [])}
            row = {"i": i, "requested": cur_up, "status": last["status"], "gas": last["gasUsed"], "usdgPrincipalAfter": post["ledger"][1], "usdgIdleAfter": post["ledger"][3], "stockPrincipalAfter": post["ledger"][0], "stockIdleAfter": post["ledger"][2],
                   "cumLoss": post["ledger"][4], "reserveAvail": post["reserve.USDG"][0], "daily": post["reserve.daily"], "covered": ev.get("ReserveCovered", {}).get("data"), "withdrawal": ev.get("Withdrawal", {}).get("data"), "pUSDGcash": post["pUSDG.cash"][0], "liquidity": post["position"][1]}
            trace.append(row)
            if last["status"] != 1:
                break
            seq.append(call_)
            if post["ledger"][1] == cur_up or post["ledger"][1] == 0:
                break
            cur_up = post["ledger"][1]
        OUT["route.reserve.sequentialFullSide"] = trace
        # market level with funded reserve: bank run
        OUT["route.reserve.bankrun.USDGfirst"] = bank_run(U_ord + N_ord, None, steps)
        # the reserve pays 50% of a deficit per call, so an exact redeem cannot finish on it: the only path that delivers it to cash is the operator's syncVault
        sync = ("syncVault", tx(GOV, PU, "syncVault(uint256)", pi((1 << 256) - 1)))
        OUT["route.swap.syncVault"] = scenario("swapsync", [sync], OV_SWAPS)
        OUT["route.sync.noFunding"] = scenario("sync0", [sync])
        for n_ in (1, 2, 4, 10, 24):
            OUT[f"route.reserve.sync.{n_}"] = scenario("sync", steps + [sync] * n_)
        # redeem by share count: an exact amount priced at the pre-sync rate can exceed shares, because more cash means less accrued interest
        red_all = [(f"pUSDG.{h}", tx(h, PU, "redeem(uint256)", pi(x.w(f"hb.pUSDG.{h}")))) for h in sortd("pUSDG") if x.w(f"hb.pUSDG.{h}") and h != "0x12c1e2c33f63f897e0e4ac1969c29493136f2481"]
        OUT["route.reserve.sync.redeemShares.noFundingNoSync"] = scenario("r0", red_all)
        OUT["route.reserve.sync.redeemShares.funded_sync4"] = scenario("r4", steps + [sync] * 4 + red_all)
        OUT["route.reserve.sync.redeemShares.funded_sync10"] = scenario("r10", steps + [sync] * 10 + red_all)
        OUT["route.swap.sync.redeemShares"] = scenario("rs", [sync] + red_all, OV_SWAPS)
        OUT["route.reserve.sync24.thenCheckpoint"] = scenario("synccp", steps + [sync] * 24 + [("checkpoint", k("checkpoint(bytes32,uint256)", PID + deadline))])
        # 7. route v: emergency decrease from the guardian (full liquidity), then whether anything can leave
        L = x.ws("a.positionState")[1]
        gd = GOV
        em = [("emergencyDecrease", tx(gd, V, "emergencyDecrease(bytes32,uint128,uint256)", PID + pi(L) + deadline))]
        OUT["route.emergency.full"] = scenario("em", em)
        OUT["route.emergency.partial50"] = scenario("em", [("emergencyDecrease", tx(gd, V, "emergencyDecrease(bytes32,uint128,uint256)", PID + pi(L // 2) + deadline))])
        OUT["route.emergency.bankrun"] = bank_run(U_ord + N_ord, None, em)
    except Repin as e:
        dict.__setitem__(OUT, "_aborted", {"reason": str(e), "atSec": time.time() - x_t0[0]})
    return OUT


x_t0 = [time.time()]


def main():
    mlogs = load_market_logs()
    extend(mlogs, int(call("eth_blockNumber", []), 16))
    for attempt in range(8):
        try:
            x_t0[0] = time.time()
            S, sims = pinned_pass(mlogs)
            break
        except Repin as e:
            print("REPIN:", e, flush=True)
            time.sleep(1)
    else:
        raise SystemExit("no pinned pass completed")
    for n, x in mlogs.items():
        json.dump({"address": x["address"], "scannedTo": x["scannedTo"], "logs": x["logs"]}, open(HERE / f"logs-{n}.json", "w"))
    json.dump(S, open(HERE / "snapshot.json", "w"), indent=1)
    json.dump(sims, open(HERE / "sims.json", "w"), indent=1)
    print(json.dumps(S["pin"]), json.dumps(S["timings"]), STATS)


if __name__ == "__main__":
    main()
