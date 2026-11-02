#!/usr/bin/env python3
"""Handoff 08 Phase 1: event-sourced evidence for the live Uniswap v4 NVDA vault and the Ramses NVDA pool.

Read only. Rebuilds the vault's history from logs and checks it against state read at the window's end block,
then runs the six analyses (supplier returns, capital placement, fees and volume, portfolio ledger and markouts,
keeper costs, dilution) plus the shared liquidity-policy evidence and profitable-capacity sentences.

    python3 remediation/tools/strategy_analyze.py --from-block 84107567 --out remediation/evidence/strategy-analysis/RUN.json

Complements strategy_evidence.py (PR #12), which needs an archive node and records raw state snapshots; run that
collector as a cross-check whenever an archive endpoint is available. Exit status is 2 if any section FAILED or the
reconstruction does not match the chain.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from evidence_lib import CHAIN_ID, EvidenceError, PUBLIC_RPC, Rpc, TimeIndex, default_cache_dir, iso
from strategy_analysis import (analyse_capital, analyse_uniswap_fees, build_samples, load_context, validate_reconstruction)
from strategy_data import ROLLOUT_BLOCK, V4_STOCK_IS_TOKEN0, RAMSES_NVDA_STOCK_IS_TOKEN0
from strategy_ops import analyse_keeper_costs, analyse_liquidity_policy
from strategy_returns import analyse_inventory, analyse_supplier_returns
from strategy_sim import Costs, Policy
from strategy_venues import (analyse_ramses, liq_other_ramses, liq_other_v4, load_ramses, policy_runs, v4_dilution)

SCHEMA = "strategy-analysis/1"


def save_partial(results: dict, partial: Path | None):
    if partial is not None:
        tmp = partial.with_suffix(".tmp")
        tmp.write_text(json.dumps(results, indent=1, default=str))
        tmp.replace(partial)


_PARTIAL: dict = {"path": None}


def section(results: dict, name: str, fn, *a):
    try:
        results[name] = fn(*a)
    except EvidenceError as e:
        results[name] = {"status": "FAILED", "reason": str(e)}
    except Exception as e:  # a bug is also a failed section, never a silent skip
        results[name] = {"status": "FAILED", "reason": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1200:]}
    save_partial(results, _PARTIAL["path"])


def policies(spacing: int) -> dict:
    return {
        "rollout policy +-1200 / trigger 300": Policy(half_width=1200, trigger=300, spacing=spacing),
        "current policy +-2400 / trigger 600": Policy(half_width=2400, trigger=600, spacing=spacing),
        "narrower candidate +-600 / trigger 200": Policy(half_width=600, trigger=200, spacing=spacing),
    }


def overall_valid(results: dict) -> bool:
    """The run is only 'valid' if EVERY reconstruction check passed: the vault/v4 checks and the Ramses pool checks."""
    main = bool(results.get("validation", {}).get("ok"))
    ramses = results.get("ramsesFeesAndVolume", {})
    ramses_ok = isinstance(ramses, dict) and bool(ramses.get("validation", {}).get("ok"))
    return main and ramses_ok


def derive_costs(keeper: dict) -> tuple[Costs, float, list[str]]:
    notes = []
    acts = keeper.get("byAction") if isinstance(keeper, dict) else None
    if not acts:
        return Costs(), 0.0, ["keeper costs unavailable: operating cost set to 0 in the capacity runs"]
    def mean(name):
        a = acts.get(name)
        return a["usdCost"] / a["count"] if a and a["count"] else 0.0
    costs = Costs(recenter_usd=mean("recenter"), rebalance_usd=mean("rebalance"))
    ck = acts.get("checkpoint")
    per_day = 0.0
    if ck:
        days_active = max(keeper.get("activeDays") or 1.0, 1.0)
        observed = mean("checkpoint") * ck["count"] / days_active
        required = mean("checkpoint") * 24  # the vault demands a checkpoint at least hourly (maxCheckpointAge 1 h)
        per_day = max(observed, required)
        notes.append(f"checkpoint cost ${per_day:.4f}/day = the larger of the observed average ${observed:.4f} and the hourly cadence the vault requires ${required:.4f}")
    notes.append(f"measured cost per recenter ${costs.recenter_usd:.4f}, per rebalance ${costs.rebalance_usd:.4f} (Uniswap vault keeper; used as a proxy for Ramses)")
    return costs, per_day, notes


def run(args, partial: Path | None = None) -> dict:
    cache = Path(args.cache_dir) if args.cache_dir else default_cache_dir()
    rpc = Rpc(args.rpc, cache_dir=cache)
    if rpc.chain_id() != CHAIN_ID:
        raise EvidenceError("wrong chain; Robinhood chain id 4663 required")
    head = rpc.head()
    to = args.to_block or head - 150
    ti = TimeIndex(rpc, path=cache / "headers.json")
    started = time.time()
    ctx = load_context(rpc, ti, args.from_block, to, progress=lambda m: print(f"[{time.time() - started:6.0f}s] {m}", file=sys.stderr))
    results: dict = {
        "schema": SCHEMA, "readOnly": True,
        "window": {"fromBlock": ctx.frm, "toBlock": ctx.to, "start": iso(int(ctx.t0)), "end": iso(int(ctx.t1)), "days": (ctx.t1 - ctx.t0) / 86400,
                   "endBlockHash": ctx.snap["blockHash"], "headAtRun": head},
        "method": {"history": "reconstructed from logs (the public RPC keeps minutes of state, so archive state reads are impossible there)",
                   "validation": "reconstruction compared with state read at the end block",
                   "timestamps": "block headers on a 10,000-block grid, linearly interpolated; oracle times are exact (updatedAt)"},
    }
    section(results, "timeIndexError", lambda: ti.measure_error(ctx.frm, ctx.to))
    section(results, "validation", validate_reconstruction, ctx)
    samples = []
    try:
        samples = build_samples(ctx)
    except Exception as e:
        results["samples"] = {"status": "FAILED", "reason": str(e)}
    results["sampleCount"] = len(samples)
    section(results, "capitalPlacement", analyse_capital, ctx, samples)
    section(results, "uniswapFeesAndVolume", analyse_uniswap_fees, ctx)
    section(results, "supplierReturns", analyse_supplier_returns, ctx)
    section(results, "portfolioLedger", analyse_inventory, ctx, samples)
    section(results, "keeperCosts", analyse_keeper_costs, ctx)
    if isinstance(results.get("keeperCosts"), dict) and "byAction" in results["keeperCosts"]:
        kc = results["keeperCosts"]
        last = kc.get("lastKeeperActionWithEvents")
        kc["activeDays"] = (ctx.t1 - ctx.t0) / 86400 - (kc.get("daysSinceLastAction") or 0.0)
    section(results, "liquidityPolicy", analyse_liquidity_policy, ctx, samples)
    ramses = {}
    try:
        ramses = load_ramses(ctx)
        section(results, "ramsesFeesAndVolume", analyse_ramses, ctx, ramses)
    except EvidenceError as e:
        results["ramsesFeesAndVolume"] = {"status": "FAILED", "reason": str(e)}
    costs, daily_fixed, cost_notes = derive_costs(results.get("keeperCosts", {}))
    results["operatingCostAssumptions"] = cost_notes
    cost_label = "measured keeper cost" if "byAction" in results.get("keeperCosts", {}) else "NO keeper cost (the keeper section failed)"
    avg_pair = None
    try:
        avg_pair = sum((s["lpIn"][0] + s["lpOut"][0] + s["idle"][0]) * s["pOracle"] + s["lpIn"][1] + s["lpOut"][1] + s["idle"][1] for s in samples) / len(samples)
        results["averagePairValueUsd"] = avg_pair
        section(results, "uniswapDilutionRealisedRanges", v4_dilution, ctx, avg_pair)
    except Exception as e:
        results["uniswapDilutionRealisedRanges"] = {"status": "FAILED", "reason": str(e)}
    capacity = {}
    move_pct = 100 * (ctx.oracle.price(ctx.t1) / ctx.oracle.price(ctx.t0) - 1)
    live_fraction = None
    try:
        from strategy_ledger import principal_at
        sp0, up0 = principal_at(ctx.timeline, ctx.frm, 0)
        p0 = ctx.oracle.price(ctx.t0)
        live_fraction = (sp0 / 1e18 * p0) / (sp0 / 1e18 * p0 + up0 / 1e6)
        results["liveSupplySplitAtWindowStart"] = {"nvdaValueFraction": live_fraction}
    except Exception as e:
        results["liveSupplySplitAtWindowStart"] = {"status": "FAILED", "reason": str(e)}
    results["oraclePriceMovePctOverWindow"] = move_pct
    save_partial(results, _PARTIAL["path"])
    try:
        v4t, v4o = liq_other_v4(ctx)
        capacity["uniswap_v4"] = policy_runs("Uniswap v4 NVDA/USDG 0.30%", v4t, v4o, ctx.oracle, V4_STOCK_IS_TOKEN0, 60, 1.0, costs, daily_fixed, policies(60), move_pct, live_fraction,
                                                 ops_note=cost_label)
        if ramses:
            rt = ramses["trades"]
            capacity["ramses_nvda"] = policy_runs("Ramses NVDA/USDG", rt, liq_other_ramses(rt), ctx.oracle, RAMSES_NVDA_STOCK_IS_TOKEN0, 10,
                                                  1.0 - ramses["protocolShare"], costs, daily_fixed, policies(10), move_pct, live_fraction,
                                                  ops_note=cost_label + " from the Uniswap vault keeper used as a proxy")
    except Exception as e:  # any failure here must not discard the completed sections above
        capacity["status"] = "FAILED"; capacity["reason"] = f"{type(e).__name__}: {e}"; capacity["trace"] = traceback.format_exc()[-1200:]
    results["profitableCapacity"] = capacity
    results["rpcCalls"] = rpc.calls
    results["cacheHits"] = rpc.cache_hits
    results["failedSections"] = [k for k, v in results.items() if isinstance(v, dict) and v.get("status") == "FAILED"]
    results["reconstructionValid"] = overall_valid(results)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rpc", default=PUBLIC_RPC)
    ap.add_argument("--from-block", type=int, default=ROLLOUT_BLOCK)
    ap.add_argument("--to-block", type=int, default=0, help="default: head - 150 (state must still be readable there)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-dir", default="")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} exists; use a new output filename for every run")
    out.parent.mkdir(parents=True, exist_ok=True)
    _PARTIAL["path"] = out.with_name(out.stem + ".partial.json")
    res = run(args)
    out.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({"out": str(out), "reconstructionValid": res["reconstructionValid"], "failedSections": res["failedSections"]}))
    return 0 if res["reconstructionValid"] and not res["failedSections"] else 2


if __name__ == "__main__":
    sys.exit(main())
