"""State reads at the window's END block, used as parameters and as validation targets.

The public RPC keeps only minutes of state, so these reads must run immediately after the end block is
chosen. Each read is recorded (target, signature, calldata, raw result) so the report is auditable. A failed
read raises: nothing is defaulted.
"""
from __future__ import annotations

from evidence_lib import EvidenceError, Rpc, call_data, s256, sN, words
from strategy_data import (ADAPTER, KEEPERS, FEED_PROXY, NVDA, P_NVDA, P_USDG, PAIR_ID, RAMSES_NVDA_POOL, USDG, VAULT)

STATE_VIEW = "0xf3334192D15450CdD385c8B70e03f9A6bD9E673b"
V4_POOL_ID_INT = int("3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1", 16)
PAIR_INT = int(PAIR_ID, 16)


class StateReader:
    def __init__(self, rpc: Rpc, block: int):
        self.rpc, self.block = rpc, block
        hdr = rpc.block(block)
        self.block_hash, self.timestamp = hdr["hash"], int(hdr["timestamp"], 16)
        if "l1BlockNumber" not in hdr:
            raise EvidenceError(f"header {block} has no l1BlockNumber")
        self.l1_block = int(hdr["l1BlockNumber"], 16)
        self.reads: list[dict] = []

    def call(self, to: str, signature: str, *args: int) -> list[int]:
        data = call_data(signature, *args)
        try:
            raw = self.rpc.eth_call(to, data, self.block)
        except EvidenceError as e:
            raise EvidenceError(f"state read failed at block {self.block} (retention is only minutes): {signature}: {e}") from e
        self.reads.append({"to": to, "signature": signature, "data": data, "result": raw})
        return words(raw)

    def addr(self, to: str, signature: str, *args: int) -> str:
        return "0x" + format(self.call(to, signature, *args)[0], "040x")

    def call_as(self, sender: str, to: str, signature: str, *args: int) -> dict:
        """Read-only simulation with msg.sender = ``sender``. A contract revert is a RESULT (what a real call would do);
        any transport or node failure still raises."""
        data = call_data(signature, *args)
        try:
            raw = self.rpc.eth_call(to, data, self.block, sender=sender)
        except EvidenceError as e:
            if "revert" in str(e).lower():
                self.reads.append({"to": to, "signature": signature, "data": data, "from": sender, "result": "REVERT", "error": str(e)[:160]})
                return {"reverted": True, "reason": str(e)[:160]}
            raise
        self.reads.append({"to": to, "signature": signature, "data": data, "from": sender, "result": raw})
        return {"reverted": False, "words": words(raw)}


def read_market(r: StateReader, market: str) -> dict:
    g = lambda sig: r.call(market, sig)[0]
    irm = r.addr(market, "interestRateModel()")
    out = {
        "address": market,
        "totalSupply": g("totalSupply()"),
        "totalBorrows": g("totalBorrows()"),
        "totalReserves": g("totalReserves()"),
        "reserveFactorMantissa": g("reserveFactorMantissa()"),
        "accrualBlockNumber": g("accrualBlockNumber()"),
        "borrowIndex": g("borrowIndex()"),
        "exchangeRateStored": g("exchangeRateStored()"),
        "exchangeRateCurrent": g("exchangeRateCurrent()"),
        "getCash": g("getCash()"),
        "borrowRatePerBlock": g("borrowRatePerBlock()"),
        "supplyRatePerBlock": g("supplyRatePerBlock()"),
        "underlying": r.addr(market, "underlying()"),
        "interestRateModel": irm,
    }
    out["model"] = {
        "baseRatePerBlock": r.call(irm, "baseRatePerBlock()")[0],
        "multiplierPerBlock": r.call(irm, "multiplierPerBlock()")[0],
        "jumpMultiplierPerBlock": r.call(irm, "jumpMultiplierPerBlock()")[0],
        "kink": r.call(irm, "kink()")[0],
    }
    controller = r.addr(market, "peridottroller()")
    out["controller"] = controller
    out["borrowCap"] = r.call(controller, "borrowCaps(address)", int(market, 16))[0]
    for name, sig in (("vaultAccountedAssets", "vaultAccountedAssets()"), ("vaultLiquidAssets", "vaultLiquidAssets()"),
                      ("vaultWithdrawableAssets", "vaultWithdrawableAssets()"), ("vaultBufferMantissa", "vaultBufferMantissa()")):
        out[name] = g(sig)
    return out


def read_vault(r: StateReader) -> dict:
    ledger = r.call(VAULT, "ledger(bytes32)", PAIR_INT)
    if len(ledger) != 6:
        raise EvidenceError(f"unexpected ledger layout: {len(ledger)} words")
    cfg = r.call(VAULT, "pairConfig(bytes32)", PAIR_INT)
    if len(cfg) != 18:
        raise EvidenceError(f"unexpected pairConfig layout: {len(cfg)} words")
    policy = r.call(VAULT, "rangePolicy(bytes32)", PAIR_INT)
    return {
        "ledger": dict(zip(("stockPrincipal", "usdgPrincipal", "stockIdle", "usdgIdle", "cumulativeLossUSDG", "lastCheckpoint"), ledger)),
        "reserveFeeBps": cfg[9], "maxPairValueUSDG": cfg[4], "maxCheckpointAge": cfg[6],
        "rangePolicy": dict(zip(("enabled", "halfWidthTicks", "triggerTicks", "minInterval", "maxPerDay", "maxLossBps", "maxRangedValueUsd"), policy)),
        "vaultToken": {"NVDA": r.call(NVDA, "balanceOf(address)", int(VAULT, 16))[0], "USDG": r.call(USDG, "balanceOf(address)", int(VAULT, 16))[0]},
    }


def read_exit_probe(r: StateReader, market: str, token: str, claim: int) -> dict:
    """What a full-claim redemption through the vault would return right now, simulated as the market (no transaction)."""
    res = r.call_as(market, VAULT, "withdrawForSide(bytes32,address,uint256,address,uint256)",
                    PAIR_INT, int(token, 16), claim, int(market, 16), r.timestamp)
    if res["reverted"]:
        return {"requested": claim, "reverted": True, "reason": res["reason"]}
    return {"requested": claim, "reverted": False, "returned": res["words"][0], "realizedLoss": res["words"][1]}


def read_v4_pool(r: StateReader) -> dict:
    s0 = r.call(STATE_VIEW, "getSlot0(bytes32)", V4_POOL_ID_INT)
    liq = r.call(STATE_VIEW, "getLiquidity(bytes32)", V4_POOL_ID_INT)[0]
    return {"sqrtPriceX96": s0[0], "tick": sN(s0[1], 24), "protocolFee": s0[2], "lpFee": s0[3], "liquidity": liq}


def read_ramses_pool(r: StateReader, pool: str = RAMSES_NVDA_POOL) -> dict:
    s0 = r.call(pool, "slot0()")
    return {"sqrtPriceX96": s0[0], "tick": sN(s0[1], 24), "feeProtocolPpm": s0[5], "unlocked": bool(s0[6]),
            "fee": r.call(pool, "fee()")[0], "liquidity": r.call(pool, "liquidity()")[0], "tickSpacing": sN(r.call(pool, "tickSpacing()")[0], 24)}


def read_feed(r: StateReader) -> dict:
    lr = r.call(FEED_PROXY, "latestRoundData()")
    return {"roundId": lr[0], "answer": s256(lr[1]), "startedAt": lr[2], "updatedAt": lr[3]}


def read_position(r: StateReader) -> dict:
    """Our live v4 position at the end block, including fees accrued but not yet collected."""
    state = r.call(ADAPTER, "positionState(bytes32)", PAIR_INT)
    tid, liq, stock_amt, usdg_amt = state[:4]
    ticks = r.call(ADAPTER, "positionTicks(bytes32)", PAIR_INT)
    manager = r.addr(ADAPTER, "positionManager()")
    out = {"tokenId": tid, "liquidity": liq, "stockAmount": stock_amt, "usdgAmount": usdg_amt,
           "tickLower": sN(ticks[0], 24), "tickUpper": sN(ticks[1], 24), "ranged": bool(ticks[2]), "positionManager": manager}
    if liq:
        info = r.call(STATE_VIEW, "getPositionInfo(bytes32,address,int24,int24,bytes32)", V4_POOL_ID_INT, int(manager, 16),
                      out["tickLower"], out["tickUpper"], tid)
        inside = r.call(STATE_VIEW, "getFeeGrowthInside(bytes32,int24,int24)", V4_POOL_ID_INT, out["tickLower"], out["tickUpper"])
        mod = 1 << 256
        out["stateLiquidity"] = info[0]
        # currency0 = USDG, currency1 = NVDA in this pool
        out["pendingFeesUsdgRaw"] = ((inside[0] - info[1]) % mod) * info[0] >> 128
        out["pendingFeesStockRaw"] = ((inside[1] - info[2]) % mod) * info[0] >> 128
    return out


def read_keepers(rpc: Rpc, block: int) -> dict:
    return {a: {"balanceWei": rpc.balance(a, block), "nonce": rpc.nonce(a, block)} for a in KEEPERS}


def snapshot(rpc: Rpc, block: int) -> dict:
    r = StateReader(rpc, block)
    snap = {
        "block": block, "blockHash": r.block_hash, "timestamp": r.timestamp, "l1BlockNumber": r.l1_block,
        "pNVDA": read_market(r, P_NVDA), "pUSDG": read_market(r, P_USDG),
        "vault": read_vault(r), "v4Pool": read_v4_pool(r), "position": read_position(r), "keepers": read_keepers(rpc, block), "ramsesPool": read_ramses_pool(r), "feed": read_feed(r),
    }
    led = snap["vault"]["ledger"]
    snap["exitProbe"] = {"pNVDA": read_exit_probe(r, P_NVDA, NVDA, led["stockPrincipal"]),
                         "pUSDG": read_exit_probe(r, P_USDG, USDG, led["usdgPrincipal"])}
    snap["reads"] = r.reads
    return snap
