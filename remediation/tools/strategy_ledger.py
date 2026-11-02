"""Exact event-sourced reconstruction of the vault pair ledger and our Uniswap v4 positions.

Pure functions over decoded log dicts, so they are unit-testable offline. Ordering is (blockNumber, logIndex).
Rules (from RobinhoodBoostedVaultV3):
  Deposited        principal[side] += amount
  FeesProcessed    principal[stock] += stockFees - stockReserved ; principal[usdg] += usdgFees - usdgReserved
  PairCheckpoint   principal := (stockAccounted, usdgAccounted)           (absolute, post-event)
  Withdrawal       principal[side] -= returned                            (loss already recognised by a checkpoint)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from evidence_lib import EvidenceError, sN, s256, topic_addr, words
from strategy_data import NVDA, USDG


def _key(l: dict) -> tuple[int, int]:
    return (l["blockNumber"], l["logIndex"])


@dataclass
class LedgerPoint:
    block: int
    idx: int
    kind: str
    stock_principal: int
    usdg_principal: int
    detail: dict = field(default_factory=dict)


def build_principal_timeline(vault_events: dict, start_block: int = 0) -> list[LedgerPoint]:
    """Principals (native units) after every principal-changing vault event, oldest first."""
    evs: list[tuple[tuple[int, int], str, dict]] = []
    for kind in ("deposited", "withdrawal", "fees", "checkpoint"):
        for l in vault_events[kind]:
            if l["blockNumber"] >= start_block:
                evs.append((_key(l), kind, l))
    evs.sort(key=lambda e: e[0])
    stock = usdg = 0
    out: list[LedgerPoint] = []
    for (b, i), kind, l in evs:
        w = words(l["data"])
        detail: dict = {"tx": l["tx"]}
        if kind in ("deposited", "withdrawal"):
            token = topic_addr(l["topics"][2])
            is_stock = token == NVDA.lower()
            if not is_stock and token != USDG.lower():
                raise EvidenceError(f"unexpected token in {kind}: {token}")
            amount = w[0] if kind == "deposited" else w[1]  # Withdrawal data: requested, returned, realizedLoss
            sign = 1 if kind == "deposited" else -1
            if is_stock:
                stock += sign * amount
            else:
                usdg += sign * amount
            detail.update(side="stock" if is_stock else "usdg", amount=amount)
            if kind == "withdrawal":
                detail.update(requested=w[0], returned=w[1], realizedLoss=w[2])
        elif kind == "fees":
            stock_fees, usdg_fees, stock_res, usdg_res = w[:4]
            stock += stock_fees - stock_res
            usdg += usdg_fees - usdg_res
            detail.update(stockFees=stock_fees, usdgFees=usdg_fees, stockReserved=stock_res, usdgReserved=usdg_res)
        else:  # checkpoint
            stock_assets, usdg_assets, benchmark, pnl, stock_acc, usdg_acc = w[0], w[1], w[2], s256(w[3]), w[4], w[5]
            detail.update(stockAssets=stock_assets, usdgAssets=usdg_assets, benchmarkUSDG=benchmark, pnlUSDG=pnl,
                          stockBefore=stock, usdgBefore=usdg)
            stock, usdg = stock_acc, usdg_acc
        out.append(LedgerPoint(b, i, kind, stock, usdg, detail))
    return out


@dataclass
class Position:
    lower: int
    upper: int
    liq: int  # raw v4 liquidity


def our_token_ids(adapter_events: dict) -> set[int]:
    return {int(l["topics"][2], 16) for l in adapter_events["added"]}


def build_position_events(v4_mods: list[dict], token_ids: set[int], manager_filter: str | None = None) -> list[dict]:
    """ModifyLiquidity events that belong to our NFTs (salt == tokenId), oldest first."""
    out = []
    for m in v4_mods:
        if m["salt"] in token_ids and (manager_filter is None or m["sender"] == manager_filter.lower()):
            out.append(m)
    out.sort(key=lambda m: (m["block"], m["idx"]))
    return out


class PositionBook:
    """Our open positions as events are applied in order."""

    def __init__(self):
        self.positions: dict[int, Position] = {}

    def apply(self, m: dict) -> None:
        p = self.positions.get(m["salt"])
        if p is None:
            p = self.positions[m["salt"]] = Position(m["lower"], m["upper"], 0)
        if (p.lower, p.upper) != (m["lower"], m["upper"]):
            raise EvidenceError(f"tick range changed for tokenId {m['salt']}")
        p.liq += m["delta"]
        if p.liq < 0:
            raise EvidenceError(f"negative liquidity for tokenId {m['salt']}")
        if p.liq == 0:
            del self.positions[m["salt"]]

    def live(self) -> list[Position]:
        return list(self.positions.values())


def principal_at(timeline: list[LedgerPoint], block: int, idx: int = 10 ** 9) -> tuple[int, int]:
    """Principals in force after all events up to and including (block, idx)."""
    lo, hi = 0, len(timeline)
    while lo < hi:
        mid = (lo + hi) // 2
        if (timeline[mid].block, timeline[mid].idx) <= (block, idx):
            lo = mid + 1
        else:
            hi = mid
    return (0, 0) if lo == 0 else (timeline[lo - 1].stock_principal, timeline[lo - 1].usdg_principal)
