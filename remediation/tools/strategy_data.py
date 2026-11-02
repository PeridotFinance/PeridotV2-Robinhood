"""Event loaders for the Robinhood strategy evidence (handoff 08, Phase 1).

Everything here is read-only and reconstructs history from logs. Records are plain dicts. Pool amounts are
normalised to the *pool's* perspective: positive means tokens flowed INTO the pool (the swapper paid them).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

from evidence_lib import (EvidenceError, Rpc, TimeIndex, addr_topic, keccak_topic, s256, sN, topic_addr,
                          topic_int, usd_per_stock, words)

# ------------------------------------------------------------------------------------------- addresses
V4_POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
V4_POOL_ID = "0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1"
VAULT = "0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f"
ADAPTER = "0xadA73211711e4790bc83B5d6B39f47fE04D276f3"
RESERVE = "0x806b182B050f7EcF908758dD6bBF91DB8B2212aF"
P_NVDA = "0xa155ccCB986774AE818b3F10F07d01D1b7A47b26"
P_USDG = "0x55aEd0569c8f0D166D71facE57B57C2f2624a563"
NVDA = "0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC"
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
PAIR_ID = "0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06"
FEED_PROXY = "0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15"
FEED_AGGREGATOR = "0xC9d16E4f2569b9E3ea0468fD85844953713DC2a2"
KEEPERS = ("0x978e2adf18da8b846552932c0ab2b1193da1fac4", "0x94696d767e65a75581145646960fa0ec886ce5d2")
RAMSES_NVDA_POOL = "0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b"
RAMSES_NVDA_GAUGE = "0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce"
RAMSES_NFP = "0x2eBd7B85a4E08D5B508b04BA147976C94afE6590"
RAM = "0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802"
ROLLOUT_BLOCK = 84_107_567  # Oct 9 concentrated-liquidity rollout (handoff 08)

# NVDA/USDG token order in each venue (the pools differ only in which token is token0)
V4_STOCK_IS_TOKEN0 = False  # Uniswap v4 pool: currency0 = USDG
RAMSES_NVDA_STOCK_IS_TOKEN0 = False  # Ramses pool: token0 = USDG


def T(sig: str) -> str:
    return keccak_topic(sig)


# ----------------------------------------------------------------------------------- generic trade model


@dataclass
class Trade:
    block: int
    idx: int
    ts: float
    stock_in: float  # stock tokens flowing INTO the pool (negative: LP sold stock)
    usd_in: float    # USDG flowing INTO the pool (negative: LP paid USDG out)
    price: float     # USD per stock after the swap (pool price)
    tick: int
    liq_after: float  # active liquidity after the swap, human units (raw / 1e12)
    fee_ppm: int
    tx: str

    @property
    def notional(self) -> float:
        return abs(self.usd_in)

    @property
    def exec_price(self) -> float:
        """Average execution price; falls back to the pool price for a dust swap with a zero leg."""
        if self.stock_in and self.usd_in:
            return abs(self.usd_in / self.stock_in)
        return self.price

    @property
    def input_is_usd(self) -> bool:
        return self.usd_in > 0

    @property
    def fee_usd(self) -> float:
        """Total swap fee in USD (fee is charged on the input leg)."""
        base = self.usd_in if self.usd_in > 0 else self.stock_in * self.exec_price
        return base * self.fee_ppm / 1e6


def _trade(l: dict, ts: float, a0_pool: int, a1_pool: int, sqrt: int, liq: int, tick: int, fee: int,
           stock_is_token0: bool) -> Trade:
    stock_raw, usd_raw = (a0_pool, a1_pool) if stock_is_token0 else (a1_pool, a0_pool)
    return Trade(block=l["blockNumber"], idx=l["logIndex"], ts=ts, stock_in=stock_raw / 1e18, usd_in=usd_raw / 1e6,
                 price=usd_per_stock(sqrt, stock_is_token0), tick=tick, liq_after=liq / 1e12, fee_ppm=fee, tx=l["tx"])


# ---------------------------------------------------------------------------------------- price feed


def load_feed(rpc: Rpc, final: int, frm: int, to: int, aggregator: str = FEED_AGGREGATOR) -> list[tuple[int, float]]:
    """(updatedAt, price USD) sorted by time, from the aggregator's AnswerUpdated events."""
    logs = rpc.logs(aggregator, [T("AnswerUpdated(int256,uint256,uint256)")], frm, to, final)
    out = []
    for l in logs:
        price = topic_int(l["topics"][1]) / 1e8
        updated = words(l["data"])[0]
        out.append((updated, price))
    out.sort()
    return out


class Oracle:
    def __init__(self, series: list[tuple[int, float]]):
        if not series:
            raise EvidenceError("empty oracle series")
        self.t = [x[0] for x in series]
        self.p = [x[1] for x in series]

    def price(self, ts: float) -> float:
        i = bisect.bisect_right(self.t, ts) - 1
        return self.p[max(i, 0)]

    def age(self, ts: float) -> float:
        i = bisect.bisect_right(self.t, ts) - 1
        return ts - self.t[max(i, 0)]


# ------------------------------------------------------------------------------------ Uniswap v4 pool


def load_v4(rpc: Rpc, ti: TimeIndex, final: int, to: int, pool_id: str = V4_POOL_ID,
            manager: str = V4_POOL_MANAGER) -> dict:
    """All swaps, liquidity modifications and the initialisation of one v4 pool, full history."""
    ev_swap = T("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
    ev_mod = T("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)")
    ev_init = T("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
    swaps_raw = rpc.logs(manager, [ev_swap, pool_id], 0, to, final)
    mods_raw = rpc.logs(manager, [ev_mod, pool_id], 0, to, final)
    init_raw = rpc.logs(manager, [ev_init, pool_id], 0, to, final)
    if len(init_raw) != 1:
        raise EvidenceError(f"expected one Initialize for the v4 pool, got {len(init_raw)}")
    w = words(init_raw[0]["data"])
    init = {"block": init_raw[0]["blockNumber"], "fee": w[0], "tick_spacing": sN(w[1], 24), "sqrt": w[3], "tick": sN(w[4], 24)}
    swaps, mods = [], []
    for l in swaps_raw:
        w = words(l["data"])
        # v4 reports amounts from the swapper's side (negative = swapper pays). Flip to the pool's side.
        swaps.append((l, -sN(w[0], 128), -sN(w[1], 128), w[2], w[3], sN(w[4], 24), w[5]))
    for l in mods_raw:
        w = words(l["data"])
        mods.append({"block": l["blockNumber"], "idx": l["logIndex"], "sender": topic_addr(l["topics"][2]),
                     "lower": sN(w[0], 24), "upper": sN(w[1], 24), "delta": s256(w[2]), "salt": w[3], "tx": l["tx"]})
    return {"init": init, "swaps": swaps, "mods": mods}


def v4_trades(raw: list, ti: TimeIndex | None, lp_fee_ppm: int, ts_from_block: int = 0) -> list[Trade]:
    """Trades with ``fee_ppm`` = the LP fee. The Swap event's own ``fee`` is the combined LP + protocol fee.
    Timestamps are only resolved from ``ts_from_block`` on (earlier swaps keep ts = 0)."""
    out = []
    for l, a0, a1, sqrt, liq, tick, fee in raw:
        ts = ti.ts(l["blockNumber"]) if (ti and l["blockNumber"] >= ts_from_block) else 0.0
        out.append(_trade(l, ts, a0, a1, sqrt, liq, tick, lp_fee_ppm, V4_STOCK_IS_TOKEN0))
    return out


# ------------------------------------------------------------------------------------------ Ramses pool


def load_ramses_pool(rpc: Rpc, final: int, pool: str, to: int, frm: int = 0) -> dict:
    ev_swap = T("Swap(address,address,int256,int256,uint160,uint128,int24)")
    ev_mint = T("Mint(address,address,uint256,int24,int24,uint128,uint256,uint256)")
    ev_burn = T("Burn(address,int24,int24,uint128,uint256,uint256)")
    ev_fee = T("FeeAdjustment(uint24,uint24)")
    ev_setfee = T("SetFee(uint24,uint24)")
    ev_proto = T("SetFeeProtocol(uint8,uint8,uint8,uint8)")
    ev_init = T("Initialize(uint160,int24)")
    return {
        "swaps": rpc.logs(pool, [ev_swap], frm, to, final),
        "mints": rpc.logs(pool, [ev_mint], frm, to, final),
        "burns": rpc.logs(pool, [ev_burn], frm, to, final),
        "fee_adj": rpc.logs(pool, [ev_fee], frm, to, final) + rpc.logs(pool, [ev_setfee], frm, to, final),
        "proto": rpc.logs(pool, [ev_proto], frm, to, final),
        "init": rpc.logs(pool, [ev_init], 0, to, final),
    }


def ramses_trades(raw_swaps: list[dict], ti: TimeIndex, fee_at, stock_is_token0: bool) -> list[Trade]:
    out = []
    for l in raw_swaps:
        w = words(l["data"])
        # v3-style pool event: positive amount = tokens received by the pool
        out.append(_trade(l, ti.ts(l["blockNumber"]), s256(w[0]), s256(w[1]), w[2], w[3], sN(w[4], 24),
                          fee_at(l["blockNumber"], l["logIndex"]), stock_is_token0))
    return out


class StepFunction:
    """Value that changes at (block, logIndex) events; ``at`` returns the value in force at a position."""

    def __init__(self, initial, changes: list[tuple[int, int, object]]):
        self.initial = initial
        self.changes = sorted(changes, key=lambda c: (c[0], c[1]))
        self.keys = [(c[0], c[1]) for c in self.changes]

    def __call__(self, block: int, idx: int):
        i = bisect.bisect_right(self.keys, (block, idx)) - 1
        return self.initial if i < 0 else self.changes[i][2]


def fee_function(raw_fee_events: list[dict], head_fee: int) -> StepFunction:
    """Dynamic Ramses fee in ppm. The value before the first event is that event's ``old`` fee."""
    evs = sorted(raw_fee_events, key=lambda l: (l["blockNumber"], l["logIndex"]))
    if not evs:
        return StepFunction(head_fee, [])
    first_old = words(evs[0]["data"])[0]
    return StepFunction(first_old, [(l["blockNumber"], l["logIndex"], words(l["data"])[1]) for l in evs])


def protocol_share_function(raw: list[dict], head_slot0_fee_protocol: int | None) -> StepFunction:
    """Protocol share of swap fees as a fraction. Defaults to the live slot0 value when no change event exists."""
    evs = sorted(raw, key=lambda l: (l["blockNumber"], l["logIndex"]))
    if not evs:
        v = (head_slot0_fee_protocol or 0) / 1e6
        return StepFunction(v, [])
    # SetFeeProtocol(old0,old1,new0,new1) in percent units of the pool implementation; kept raw and reported
    return StepFunction((head_slot0_fee_protocol or 0) / 1e6, [])


# ------------------------------------------------------------------------------------------ ERC20 / vault


def load_erc20_flows(rpc: Rpc, final: int, token: str, holder: str, to: int) -> list[tuple[int, int, int]]:
    """Signed balance changes of ``holder`` in ``token`` as (block, logIndex, delta), oldest first."""
    t = T("Transfer(address,address,uint256)")
    inbound = rpc.logs(token, [t, None, addr_topic(holder)], 0, to, final)
    outbound = rpc.logs(token, [t, addr_topic(holder)], 0, to, final)
    flows = [(l["blockNumber"], l["logIndex"], int(l["data"], 16)) for l in inbound]
    flows += [(l["blockNumber"], l["logIndex"], -int(l["data"], 16)) for l in outbound]
    # a self-transfer appears in both lists; both signs cancel which is correct
    flows.sort(key=lambda f: (f[0], f[1]))
    return flows


class BalanceSeries:
    def __init__(self, flows: list[tuple[int, int, int]]):
        self.keys = [(f[0], f[1]) for f in flows]
        acc, self.cum = 0, []
        for f in flows:
            acc += f[2]
            self.cum.append(acc)

    def at(self, block: int, idx: int = 10 ** 9) -> int:
        i = bisect.bisect_right(self.keys, (block, idx)) - 1
        return 0 if i < 0 else self.cum[i]


def load_vault_events(rpc: Rpc, final: int, to: int) -> dict:
    sigs = {
        "deposited": "Deposited(bytes32,address,address,uint256)",
        "withdrawal": "Withdrawal(bytes32,address,address,uint256,uint256,uint256)",
        "fees": "FeesProcessed(bytes32,uint256,uint256,uint256,uint256)",
        "checkpoint": "PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)",
        "rebalanced": "LiquidityRebalanced(bytes32,uint256,uint256,uint128)",
        "recentered": "Recentered(bytes32,int24,int24,int24,int24,uint128,int24)",
        "swap": "SettlementSwap(bytes32,address,uint256,uint256)",
        "emergency": "EmergencyLiquidityDecreased(bytes32,uint128,uint256,uint256)",
        "policy": "RangePolicyUpdated(bytes32,(bool,uint16,uint16,uint32,uint8,uint16,uint128))",
    }
    out = {}
    for k, s in sigs.items():
        out[k] = [l for l in rpc.logs(VAULT, [T(s), PAIR_ID], 0, to, final)]
    return out


def load_adapter_events(rpc: Rpc, final: int, to: int) -> dict:
    sigs = {
        "added": "LiquidityAdded(bytes32,uint256,uint256,uint256,uint128)",
        "decreased": "LiquidityDecreased(bytes32,uint256,uint128,uint256,uint256)",
        "fees": "FeesCollected(bytes32,uint256,uint256)",
        "burned": "PositionBurned(bytes32,uint256)",
        "range": "RangeSet(bytes32,int24,int24)",
    }
    return {k: rpc.logs(ADAPTER, [T(s), PAIR_ID], 0, to, final) for k, s in sigs.items()}
