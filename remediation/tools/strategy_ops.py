"""Keeper costs (item 5) and the shared liquidity-policy evidence (00 section 6a, added by PR #12) for pNVDA and pUSDG."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from evidence_lib import EvidenceError, iso, keccak_topic
from strategy_analysis import Ctx, utc_day
from strategy_data import KEEPERS, NVDA, PAIR_ID, USDG
from strategy_metrics import liquidity_scenario

KEEPER_FUNCTIONS = {
    "checkpoint": "checkpoint(bytes32,uint256)", "rebalance": "rebalance(bytes32,uint256)", "recenter": "recenter(bytes32,uint256)",
    "collectFees": "collectFees(bytes32,uint256)", "burnEmptyPosition": "burnEmptyPosition(bytes32,uint256)", "clearRange": "clearRange(bytes32)",
}
MIN_GAS_WEI = 300_000_000_000_000  # keeper/worker.py: refuses to act below 0.0003 ETH


def _cached_tx(ctx: Ctx, tx: str) -> dict:
    cache = ctx.rpc.cache_dir
    path = cache / f"tx-{tx[2:]}.json" if cache else None
    if path and path.exists():
        return json.loads(path.read_text())
    t, r = ctx.rpc.tx(tx), ctx.rpc.receipt(tx)
    if "effectiveGasPrice" not in r or "gasUsed" not in r:
        raise EvidenceError(f"receipt {tx} lacks gasUsed/effectiveGasPrice; cost cannot be computed")
    rec = {"from": t["from"].lower(), "selector": t["input"][:10], "gasUsed": int(r["gasUsed"], 16),
           "effectiveGasPrice": int(r["effectiveGasPrice"], 16), "status": int(r["status"], 16),
           "block": int(r["blockNumber"], 16), "l1Fee": int(r["l1Fee"], 16) if r.get("l1Fee") else 0}
    if path:
        path.write_text(json.dumps(rec))
    return rec


def analyse_keeper_costs(ctx: Ctx) -> dict:
    selectors = {keccak_topic(sig)[:10]: name for name, sig in KEEPER_FUNCTIONS.items()}
    txs: dict[str, dict] = {}
    for src in (ctx.vault_events, ctx.adapter_events):
        for evs in src.values():
            for l in evs:
                if ctx.frm <= l["blockNumber"] <= ctx.to:
                    txs.setdefault(l["tx"], {"block": l["blockNumber"]})
    rows = []
    for tx in sorted(txs, key=lambda h: txs[h]["block"]):
        rec = _cached_tx(ctx, tx)
        rec["tx"] = tx
        rec["action"] = selectors.get(rec["selector"], f"other:{rec['selector']}")
        rec["ts"] = ctx.ti.ts(rec["block"])
        rows.append(rec)
    eth = ctx.eth_oracle
    if eth is None:
        raise EvidenceError("no ETH/USD oracle series: keeper cost in USD cannot be computed")
    keeper = KEEPERS[0]
    by_action: dict = defaultdict(lambda: {"count": 0, "gas": [], "ethCost": 0.0, "usdCost": 0.0, "revertedAmongEventTransactions": 0})
    per_wallet: dict = defaultdict(int)
    per_day: dict = defaultdict(int)
    for r in rows:
        if r["from"] not in KEEPERS:
            continue
        a = by_action[r["action"]]
        cost_eth = (r["gasUsed"] * r["effectiveGasPrice"] + r["l1Fee"]) / 1e18
        a["count"] += 1
        a["gas"].append(r["gasUsed"])
        a["ethCost"] += cost_eth
        a["usdCost"] += cost_eth * eth.price(r["ts"])
        a["revertedAmongEventTransactions"] += 1 if r["status"] != 1 else 0
        per_wallet[r["from"]] += 1
        per_day[utc_day(r["ts"])] += 1
    actions = {k: {"count": v["count"], "medianGas": statistics.median(v["gas"]), "meanGas": statistics.fmean(v["gas"]), "maxGas": max(v["gas"]),
                   "ethCost": v["ethCost"], "usdCost": v["usdCost"],
                   "revertedAmongEventTransactions": v["revertedAmongEventTransactions"]} for k, v in by_action.items()}
    window_days = (ctx.t1 - ctx.t0) / 86400
    k_state = ctx.snap["keepers"][keeper]
    sent_with_events = sum(1 for r in rows if r["from"] == keeper)
    spent_known_eth = sum(v["ethCost"] for v in by_action.values())
    total_eth = sum(v["ethCost"] for v in by_action.values())
    last_ts = max((r["ts"] for r in rows if r["from"] in KEEPERS), default=None)
    burn_per_day = spent_known_eth / window_days if window_days else None
    eth_usd = eth.price(ctx.t1) if eth else None
    return {
        "keeperAddress": keeper, "transactionsWithVaultEvents": sent_with_events,
        "byAction": actions, "actionsPerDay": dict(sorted(per_day.items())),
        "totalEth": total_eth, "totalUsd": total_eth * eth_usd if eth_usd else None, "ethUsdAtEnd": eth_usd,
        "walletAtEnd": {"balanceEth": k_state["balanceWei"] / 1e18, "nonce": k_state["nonce"], "gasFloorEth": MIN_GAS_WEI / 1e18,
                        "belowFloor": k_state["balanceWei"] < MIN_GAS_WEI},
        "lastKeeperActionWithEvents": iso(int(last_ts)) if last_ts else None,
        "daysSinceLastAction": ((ctx.t1 - last_ts) / 86400) if last_ts else None,
        "averageBurnEthPerDayWhileActive": burn_per_day,
        "failedOrEventlessTransactions": {
            "cloudKeeperLifetimeNonce": k_state["nonce"],
            "cloudKeeperTransactionsWithVaultEvents": per_wallet[keeper],
            "cloudKeeperTransactionsWithoutEvents": k_state["nonce"] - per_wallet[keeper],
            "note": "the nonce counts every transaction the cloud keeper wallet ever sent, reverted ones included, so a difference from the number of "
                    "transactions that left vault events would be reverted or unrelated sends. Events before the window are excluded from the right-hand count, "
                    "so the difference is an upper bound on reverted sends inside the window. No explorer is reachable, so their gas cannot be itemised.",
            "perWalletTransactionsWithEvents": dict(per_wallet),
        },
        "waitingActions": {"status": "NOT_AVAILABLE_FROM_CHAIN",
                           "reason": "waits and simulated reverts never reach the chain; they exist only in the keeper's own JSON log. "
                                     "Persist and ingest that journal to count them (handoff 08 section 7)."},
    }


# ------------------------------------------------------------------------------ liquidity policy


def analyse_liquidity_policy(ctx: Ctx, samples: list[dict]) -> dict:
    """Per token, observed contract behaviour at the end block. Not a proposal and not a guarantee."""
    snap = ctx.snap
    price = ctx.oracle.price(ctx.t1)
    pos = snap["position"]
    led = snap["vault"]["ledger"]
    vault_tok = snap["vault"]["vaultToken"]
    out = {"asOfBlock": ctx.to, "oracleUsd": price, "settlementSwapsNote":
           "settlement swaps are paused in the live pair config, so idle NVDA is NOT counted as USDG redemption capacity, nor the reverse",
           "reserveNote": "a borrowing reserve is a floor for new loans and deployments; it is not guaranteed withdrawal coverage", "tokens": {}}
    for name, cfg in (("pNVDA", {"dec": 18, "side": "stock", "price": price, "tok": "NVDA"}),
                      ("pUSDG", {"dec": 6, "side": "usd", "price": 1.0, "tok": "USDG"})):
        m = snap[name]
        probe = snap["exitProbe"][name]
        dec = 10 ** cfg["dec"]
        local = (m["getCash"] - m["vaultWithdrawableAssets"]) / dec
        accounted = m["vaultAccountedAssets"] / dec
        borrows = m["totalBorrows"] / dec
        reserves = m["totalReserves"] / dec
        idle = vault_tok[cfg["tok"]] / dec
        lp_tok = (pos["stockAmount"] if cfg["side"] == "stock" else pos["usdgAmount"]) / dec
        supplier_assets = local + accounted + borrows - reserves
        model_cap = min(accounted, idle + lp_tok)
        sim_returned = None if probe["reverted"] else probe["returned"] / dec
        sim_loss = None if probe["reverted"] else probe["realizedLoss"] / dec
        borrow_cap = m["borrowCap"] / dec
        out["tokens"][name] = {
            "localCash": local, "strategyAccountedValue": accounted, "strategyIdleInVault": idle, "strategyLpTokenAmount": lp_tok,
            "outstandingBorrows": borrows, "marketReserves": reserves, "supplierAssets": supplier_assets,
            "shares": {"localCash": local / supplier_assets, "strategy": accounted / supplier_assets, "loans": borrows / supplier_assets},
            "vaultBufferTarget": m["vaultBufferMantissa"] / 1e18,
            "contractReportedWithdrawable": m["vaultWithdrawableAssets"] / dec,
            "redemption": {
                "localCashOnly": local,
                "modelStrategyCapacityInRequestedToken": model_cap,
                "simulatedFullClaimExit": {"requestedClaim": probe["requested"] / dec, "returned": sim_returned, "realizedLossOnExit": sim_loss,
                                           "reverted": probe["reverted"], "how": "eth_call of withdrawForSide as the market at the end block (no transaction)"},
                "immediateCapacityNormal": local + (sim_returned if sim_returned is not None else 0.0),
                "immediateCapacityIfStrategyExitFails": local,
                "label": "simulated exit is observed contract behaviour at the end block; the model value is an estimate",
            },
            "borrowing": {"getCash": m["getCash"] / dec, "borrowCap": borrow_cap, "capHeadroom": borrow_cap - borrows,
                          "capacityNormal": min(m["getCash"] / dec, borrow_cap - borrows),
                          "capacityStressedOracleInvalid": min(local, borrow_cap - borrows),
                          "note": "observed: borrow uses getCash and _prepareFixedLiquidity may unwind the vault synchronously for a shortfall; "
                                  "6a proposes new borrowing must not depend on an unwind. Margin opens and pending multi-step opens are not included."},
        }
    out["scenario_50_40_10"] = {
        "isLive": False,
        "statement": "illustrative arithmetic from 00 section 6a, not the live configuration or a proven capacity",
        "redeem25_of_100": liquidity_scenario(100, 0.5, 0.4, 0.1, 25),
        "redeem25_strategy_returns_only_12": liquidity_scenario(100, 0.5, 0.4, 0.1, 25, strategy_returns=12),
    }
    return out
