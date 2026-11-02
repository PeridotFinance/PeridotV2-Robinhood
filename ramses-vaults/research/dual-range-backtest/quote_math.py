"""Pure, offline quote math for Ramses/Uniswap-v3-style concentrated-liquidity pools.

Input is a saved pool-inputs document (see pool_quote.py): the full pinned sqrtPriceX96, tick, liquidity, fee, the tickBitmap words and
every initialized tick's liquidityNet. Nothing here touches the network, so a quote can be replayed from the saved file alone.

The swap walk follows the pool exactly in structure: constant liquidity between initialized ticks; the fee is taken from the gross input;
crossing a tick adds (moving up) or subtracts (moving down) its liquidityNet; and **an interval with zero liquidity costs nothing to
cross: the price jumps to the next initialized tick**. Every quote ends in exactly one status:

  filled                  the whole input was consumed
  price_limit_rejection   the swap stopped at the caller's sqrtPriceLimit with input left (a router quote with a slippage limit)
  capacity_exhausted      the ENTIRE tick range was scanned and every initialized tick was crossed: no executable liquidity is left
  scan_window_exhausted   only a window around the price was scanned, so what lies beyond it is UNKNOWN (not a capacity claim)
"""
from __future__ import annotations

import copy
import math

Q96 = 2 ** 96
MIN_TICK, MAX_TICK = -887272, 887272
LN = math.log(1.0001)


def tick_sqrt(t: float) -> float:
    return math.exp(t * LN / 2.0)


def make_state(inputs: dict) -> dict:
    ticks = sorted((int(t), int(v["liquidityNet"])) for t, v in inputs["ticks"].items())
    cur = int(inputs["slot0"]["tick"])
    idx = sum(1 for t, _ in ticks if t <= cur)
    scan = inputs["scan"]
    return {"s": int(inputs["slot0"]["sqrtPriceX96"]) / Q96, "L": float(int(inputs["liquidity"])), "idx": idx, "ticks": ticks,
            "fee": int(inputs["fee_ppm"]) / 1e6, "full": scan["mode"] == "full", "lo_edge": tick_sqrt(scan["fromTick"]), "hi_edge": tick_sqrt(scan["toTick"]),
            "dec0": inputs["dec0"], "dec1": inputs["dec1"], "token0": inputs["token0"], "token1": inputs["token1"]}


def swap(state: dict, gross: float, zero_for_one: bool, sqrt_limit: float | None = None) -> dict:
    """Execute a swap on ``state`` IN PLACE (copy first if you need the original). ``gross`` is the raw input including the fee."""
    fee = state["fee"]
    ticks = state["ticks"]
    s, L, idx = state["s"], state["L"], state["idx"]
    rem, out, used = float(gross), 0.0, 0.0   # ``used`` is accumulated directly: gross - rem loses all precision for a huge probe input
    status = "filled"
    while rem > 0:
        if zero_for_one:
            bound = ticks[idx - 1] if idx > 0 else None
            floor_edge = tick_sqrt(MIN_TICK) if state["full"] else state["lo_edge"]
            target = tick_sqrt(bound[0]) if bound else floor_edge
            real_bound = bound
            if sqrt_limit is not None and sqrt_limit > target:
                target, bound, limited = sqrt_limit, None, True
            else:
                limited = False
            if L > 0:
                need_gross = L * (1.0 / target - 1.0 / s) / (1.0 - fee)
        else:
            bound = ticks[idx] if idx < len(ticks) else None
            ceil_edge = tick_sqrt(MAX_TICK) if state["full"] else state["hi_edge"]
            target = tick_sqrt(bound[0]) if bound else ceil_edge
            real_bound = bound
            if sqrt_limit is not None and sqrt_limit < target:
                target, bound, limited = sqrt_limit, None, True
            else:
                limited = False
            if L > 0:
                need_gross = L * (target - s) / (1.0 - fee)
        if L > 0:
            if rem <= need_gross:
                net = rem * (1.0 - fee)
                if zero_for_one:
                    s_new = 1.0 / (1.0 / s + net / L)
                    out += L * (s - s_new)
                else:
                    s_new = s + net / L
                    out += L * (1.0 / s - 1.0 / s_new)
                used += rem
                s, rem = s_new, 0.0
                break
            out += L * (s - target) if zero_for_one else L * (1.0 / s - 1.0 / target)
            rem -= need_gross
            used += need_gross
        jumped_empty = L <= 0
        s = target                      # zero liquidity: the price jumps to the target for free
        if limited:
            # A limit only "rejects" a fill when executable liquidity lies beyond it. If the price merely jumped through empty space and nothing
            # is left beyond, the real problem is capacity (full scan) or an unknown beyond the window.
            if jumped_empty and real_bound is None:
                status = "capacity_exhausted" if state["full"] else "scan_window_exhausted"
            else:
                status = "price_limit_rejection"
            break
        if bound is None:
            status = "capacity_exhausted" if state["full"] else "scan_window_exhausted"
            break
        L = L - bound[1] if zero_for_one else L + bound[1]
        idx = idx - 1 if zero_for_one else idx + 1
    state["s"], state["L"], state["idx"] = s, max(L, 0.0), idx
    consumed = used
    return {"status": status, "requested": gross, "consumed": consumed, "filledFraction": (consumed / gross) if gross else 1.0, "out": out, "sqrtAfter": s}


def usdg_per_stock(state: dict, usdg: str) -> float:
    r = state["s"] ** 2
    usdg0 = state["token0"].lower() == usdg.lower()
    d_stock = state["dec1"] if usdg0 else state["dec0"]
    return (10 ** d_stock) / (r * 1e6) if usdg0 else r * (10 ** d_stock) / 1e6


def limit_sqrt(s0: float, usdg0: bool, up: bool, pct: float) -> float:
    """sqrtPriceLimit that caps the USDG-per-stock move at ``pct`` percent (up = buying the stock)."""
    f = math.sqrt(1 + pct / 100.0) if up else math.sqrt(1 - pct / 100.0)
    return s0 / f if usdg0 else s0 * f


def trade_plan(inputs: dict, usdg: str, sizes_usd: list) -> dict:
    """Integer raw amounts used for BOTH the offline quote and the fork replay: buy = size USDG; sell = the stock amount worth ``size`` at the mid."""
    st = make_state(inputs)
    usdg0 = st["token0"].lower() == usdg.lower()
    p = usdg_per_stock(st, usdg)
    d_stock = st["dec1"] if usdg0 else st["dec0"]
    return {"usdg0": usdg0, "midUsdgPerStock": p, "buy": [int(s * 1e6) for s in sizes_usd], "sell": [int(s / p * 10 ** d_stock) for s in sizes_usd], "stockDecimals": d_stock}


def quote_all(inputs: dict, usdg: str, sizes_usd: list, limit_pct: float = 10.0) -> dict:
    st0 = make_state(inputs)
    plan = trade_plan(inputs, usdg, sizes_usd)
    usdg0, p, d_stock = plan["usdg0"], plan["midUsdgPerStock"], plan["stockDecimals"]
    s0 = st0["s"]
    lim_buy, lim_sell = limit_sqrt(s0, usdg0, True, limit_pct), limit_sqrt(s0, usdg0, False, limit_pct)
    rows = []
    for k, size in enumerate(sizes_usd):
        row = {"sizeUsd": size, "buyRawIn": plan["buy"][k], "sellRawIn": plan["sell"][k]}
        for side, amount, zfo, lim in (("buy", plan["buy"][k], usdg0, lim_buy), ("sell", plan["sell"][k], not usdg0, lim_sell)):
            for tag, limit in (("", None), ("Limited", lim)):
                st = copy.deepcopy(st0)
                r = swap(st, amount, zfo, limit)
                if side == "buy":
                    q = r["out"] / 10 ** d_stock
                    cost = (1 - q * p / size) * 1e4 if r["status"] == "filled" else None
                else:
                    cost = (1 - (r["out"] / 1e6) / size) * 1e4 if r["status"] == "filled" else None
                row[f"{side}{tag}"] = {"status": r["status"], "costBps": cost, "filledFraction": r["filledFraction"], "out": r["out"], "consumed": r["consumed"], "sqrtAfter": r["sqrtAfter"]}
        # sequential round trip: buy then sell the stock obtained, in the SAME evolving pool state (fees paid both ways, no re-quote)
        st = copy.deepcopy(st0)
        b = swap(st, plan["buy"][k], usdg0)
        if b["status"] == "filled":
            sold = swap(st, b["out"], not usdg0)
            row["sequentialRoundTrip"] = {"status": sold["status"], "costBps": (1 - (sold["out"] / 1e6) / size) * 1e4 if sold["status"] == "filled" else None}
        else:
            row["sequentialRoundTrip"] = {"status": "buy_" + b["status"], "costBps": None}
        # not a round trip: kept only to show what the old (wrong) sum of two independent quotes would have said
        bb, ss = row["buy"]["costBps"], row["sell"]["costBps"]
        row["sumOfIndependentOneWayQuotesBps"] = (bb + ss) if bb is not None and ss is not None else None
        rows.append(row)
    return {"plan": plan, "limitPct": limit_pct, "rows": rows}


def walk_to(state: dict, target: float, zero_for_one: bool) -> dict:
    """Gross raw input that moves the sqrt price to ``target`` (price-impact depth), with the same status vocabulary."""
    st = copy.deepcopy(state)
    r = swap(st, 1e40, zero_for_one, sqrt_limit=target)
    reached = r["status"] == "price_limit_rejection"
    return {"reached": reached, "gross": r["consumed"] if reached else None, "status": "filled" if reached else r["status"]}


def depth(inputs: dict, usdg: str) -> list:
    st0 = make_state(inputs)
    usdg0 = st0["token0"].lower() == usdg.lower()
    p = usdg_per_stock(st0, usdg)
    d_stock = st0["dec1"] if usdg0 else st0["dec0"]
    out = []
    for up in (True, False):
        zfo = usdg0 if up else (not usdg0)
        for pct in (1.0, 2.0, 5.0):
            r = walk_to(st0, limit_sqrt(st0["s"], usdg0, up, pct), zfo)
            in_is_usdg = (zfo == usdg0)
            usd = None if not r["reached"] else (r["gross"] / 1e6 if in_is_usdg else r["gross"] / 10 ** d_stock * p)
            out.append({"direction": "price_up_buy_stock" if up else "price_down_sell_stock", "movePct": pct, "inputUsd": usd, "status": r["status"]})
    return out
