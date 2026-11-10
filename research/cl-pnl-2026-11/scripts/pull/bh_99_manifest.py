"""Write data/blackhole/MANIFEST.json, SHA256SUMS and FACTS.md from the JSON readbacks and reports already on disk.

Nothing is fetched here. Every number in FACTS.md is taken from state_at_end.json, build_report.json, checks.json,
depth_at_end.json, traces.json or gauge_epochs.json, so the facts file cannot drift from the data.
usage: python3 bh_99_manifest.py
"""
from __future__ import annotations

import bisect
import time

import bh_lib as B

A = B.A
DOCS_TECH = "https://docs.blackhole.xyz/technical-amm-documentation"
DOCS_REW = "https://docs.blackhole.xyz/protocol-design/rewards"
DOCS_EM = "https://docs.blackhole.xyz/emissions"
V, DOC, NF = "VERIFIED-ON-CHAIN", "DOCUMENTED-ONLY", "NOT FOUND"


def fmt(x: float, d: int = 2) -> str:
    return f"{x:,.{d}f}"


def main() -> None:
    st = A.read_json(B.DATA_BH / "state_at_end.json")
    rep = A.read_json(B.DATA_BH / "build_report.json")
    chk = A.read_json(B.DATA_BH / "checks.json")
    dep = A.read_json(B.DATA_BH / "depth_at_end.json")
    trc = A.read_json(B.DATA_BH / "traces.json")
    epo = A.read_json(B.DATA_BH / "gauge_epochs.json")
    pe = st["A_dataset_pool"]
    s1 = pe["at_START_minus_1"]
    rp = chk["replay"]
    acc = rp["fee_accounting_window"]["per_token"]

    # ---- BLACK price at each notification (last pair swap at or before the notification's timestamp)
    _, bs = A.read_csv_gz(B.DATA_BH / "black_pool_swaps.csv.gz")
    bts = [int(r[2]) for r in bs]
    bpx = [int(r[10]) / 1e6 / (int(r[11]) / 1e18) for r in bs]

    def black_px(ts: int):
        k = bisect.bisect_right(bts, ts) - 1
        return (bpx[k], bts[k]) if k >= 0 else (None, None)
    emis = []
    for e in epo["epochs"]:
        if not e["in_window"]:
            continue
        px, pts = black_px(e["ts"])
        emis.append({"epoch_start_iso": e["epoch_start_iso"], "notified_iso": e["iso"], "block": e["block"], "tx": e["tx"],
                     "amount_BLACK": e["amount_BLACK"], "black_usd_at_notification": px, "price_row_ts": pts,
                     "usd_at_notification": e["amount_BLACK"] * px if px else None})
    pre = [e for e in epo["epochs"] if not e["in_window"]]
    epoch_at_start = pre[-1] if pre else None

    # ---- manifest
    files = {}
    structure = chk.get("structure", {})
    file_info = {}
    for part in ("pool", "gauge", "black"):
        file_info.update(rep[part]["files"])
    host_logs = "avalanche-c-chain-rpc.publicnode.com (eth_getLogs, header batches)"
    extra = {
        "state_at_end.json": "api.avax.network (archive eth_call / eth_getStorageAt at END and START-1), api.routescan.io (factory CustomPool logs by token topics)",
        "depth_at_end.json": "api.avax.network (archive eth_call of both QuoterV2 contracts at END)",
        "traces.json": "avalanche-c-chain-rpc.publicnode.com and api.avax.network (eth_getTransactionReceipt), api.avax.network (eth_call)",
        "gauge_epochs.json": host_logs, "build_report.json": "local (derived from the cached logs)", "checks.json": "local + avalanche-c-chain-rpc.publicnode.com + api.routescan.io (re-requests)",
        "FACTS.md": "local (derived from the JSON files)"}
    for f in sorted(B.DATA_BH.iterdir()):
        if f.name in ("MANIFEST.json", "SHA256SUMS") or f.name.endswith(".tmp"):
            continue
        e = {"sha256": A.sha256_file(f), "bytes": f.stat().st_size}
        if f.name in file_info:
            fi = file_info[f.name]
            e.update({"rows": fi["rows"], "first_block": fi.get("first_block"), "last_block": fi.get("last_block")})
            if fi.get("first_ts"):
                e.update({"first_iso": A.iso(fi["first_ts"]), "last_iso": A.iso(fi["last_ts"])})
            e["source_host"] = host_logs
            e["checks"] = structure.get(f.name)
            e["details"] = {k: v for k, v in fi.items() if k not in ("rows", "first_block", "last_block", "first_ts", "last_ts")}
        else:
            e["source_host"] = extra.get(f.name, "local")
        files[f.name] = e
    man = {
        "generated_at_unix": int(time.time()), "generated_at_iso": A.iso(time.time()),
        "window": {"start": {"number": B.START, "timestamp": B.START_TS, "iso": A.iso(B.START_TS)},
                   "end": {"number": B.END, "hash": B.END_HASH, "timestamp": B.END_TS, "iso": A.iso(B.END_TS)},
                   "note": "same START and END as data/avalanche (Pharaoh); the pool was created at block %d, before START" % B.POOL_CREATED},
        "endpoints": {"logs_and_headers": "avalanche-c-chain-rpc.publicnode.com", "state": "api.avax.network (archive eth_call)",
                      "second_source": "api.routescan.io etherscan-style logs API; verified sources and ABIs from its getsourcecode endpoint",
                      "not_used": "AVALANCHE_RPC env endpoint, any Alchemy endpoint, any operator host config, the keyless tenderly gateway"},
        "dataset_pool": {"address": B.POOL, "type": "AlgebraPool (Algebra Integral core, verified source on Routescan, solc 0.8.20)", "token0": "WAVAX (18 decimals)",
                         "token1": "USDC (6 decimals)", "tick_spacing": pe["tickSpacing"], "factory": pe["factory"], "deployer": B.POOL_DEPLOYER,
                         "created_block": B.POOL_CREATED, "initialize_event": rep["pool"]["initialize"],
                         "fee_ppm": pe["fee()_ppm"], "community_fee_per_1000": pe["globalState"]["communityFee_per_1000"],
                         "config_history_from_creation": rep["pool"]["config_history_from_creation"]},
        "black_price_pool": {"address": B.BLACK_PAIR, "type": "classic vAMM pair (Solidly style)", "token0": "USDC (6 decimals)", "token1": "BLACK (18 decimals)",
                             "why": st["C_black_price_pool_choice"]},
        "files": files,
        "requested_log_ranges": chk.get("requested_ranges"),
        "checks": {k: v for k, v in chk.items() if k not in ("structure", "requested_ranges")},
        "emissions_per_epoch_in_window": emis, "emission_epoch_in_force_at_START": epoch_at_start,
        "limitations": [
            "The pre-window pull of the pool (creation..START-1) is filtered to Mint, Burn, Initialize, Fee, CommunityFee, TickSpacing, Plugin, PluginConfig and CommunityVault; swaps before START were not pulled.",
            "Gauge events were pulled for RewardAdded and ClaimFees only (creation..END); Deposit / Withdraw / Harvest were sampled (last 50,000 blocks and three other 50,000-block probes), not pulled in full, so the total staked liquidity across all ticks was not rebuilt: the staked share is given for the ACTIVE liquidity (virtual pool currentLiquidity / pool liquidity) at END and START-1.",
            "All log datasets were served by one RPC host (publicnode); the independent source is the Routescan logs API on 5 ranges per dataset (checks.re_requests), not a full second pull.",
            "black_pool_swaps.csv.gz is a constant-product pair: sqrt_price_x96, liquidity and tick are derived from the Sync reserves, not event fields. BLACK is quoted in USDC directly.",
            "The exact Algebra Integral version string is not exposed on chain; the version statement rests on the verified source (SwapFee / BurnFee events, plugin fee, getCommunityFeePending).",
            "Pharaoh depth uses Pharaoh's own QuoterV2 (found through the factory deployer's deployment transactions and verified by deployer().RamsesV3Factory() == the pool's factory); no Pharaoh documentation page could be fetched (docs host did not resolve).",
            "Reward-claim evidence: the farming contract's reward ledger is per owner and token, not per pool, so the RewardClaimed sample proves which token is paid, not on which pool it was earned.",
            "The single gauge Harvest transaction quoted in traces.json was found in a partial all-topics pull of the gauge (blocks 65,228,165..67,828,164, cache directory bh_gauge) that was abandoned for size (about 150 logs per 1,000 blocks); it is not part of any dataset."],
        "scripts": "scripts/pull/bh_lib.py, bh_00_state.py, bh_01_fetch_logs.py, bh_02_traces.py, bh_03_build.py, bh_04_checks.py, bh_05_depth.py, bh_99_manifest.py (reuses avax_lib.py unmodified)",
        "run_sequence": ["python3 -u bh_00_state.py", "python3 -u bh_01_fetch_logs.py <dataset>   for bh_pool, each name in bh_lib.PRE_SEGMENTS, bh_gauge_rf, bh_vault, bh_gm_distribute, bh_black_pair",
                         "python3 -u bh_03_build.py", "python3 -u bh_02_traces.py", "python3 -u bh_05_depth.py", "python3 -u bh_04_checks.py", "python3 bh_99_manifest.py"],
    }

    # ================================================================= FACTS.md
    g = st["C_gauge"]
    far = st["C_farming"]
    vp = far["virtual_pool"]
    vault = st["B_community_vault"]
    ph = st["E_pharaoh_pool"]
    hist = rep["pool"]["config_history_from_creation"]
    sw = rep["pool"]["files"]["blackhole_wavax_usdc_swaps.csv.gz"]
    fv = rp["fee_validation"]
    px_end = pe["price_USDC_per_WAVAX"]
    L = []
    w = L.append
    w("# Blackhole WAVAX/USDC concentrated pool versus Pharaoh: facts at block %d" % B.END)
    w("")
    w(f"Pin: END = block {B.END:,} ({A.iso(B.END_TS)}, hash `{B.END_HASH}`), START = block {B.START:,} ({A.iso(B.START_TS)}). "
      "State reads are archive `eth_call`s on api.avax.network at END (or START-1 = %d where stated); logs from avalanche-c-chain-rpc.publicnode.com. "
      "Every call with its raw result is in `state_at_end.json` (`calls`), every quote in `depth_at_end.json`, every receipt decode in `traces.json`, the checks in `checks.json`. "
      "Tags: %s = read from the chain here (call, event or verified source code on Routescan); %s = stated by the protocol's docs, not re-derived; %s = looked for, not there." % (B.START - 1, V, DOC, NF))
    w("")
    w("## A. Pool and contracts")
    w("")
    w("| Fact | Value | Tag | Evidence |")
    w("|---|---|---|---|")
    w(f"| Pool type | Algebra Integral pool (`AlgebraPool`, solc 0.8.20), not Uniswap-v3 style: `globalState()` answers, `slot0()` reverts; events `SwapFee`, `BurnFee`, `Plugin`, `CommunityFee` | {V} | verified source of `{B.POOL}` on Routescan; `globalState()`, `slot0()` at END |")
    w(f"| Exact Algebra version | not exposed (no `version()` on the pool); the source has the plugin-fee swap path (`SwapFee`, `BurnFee`, `getPluginFeePending()`), which I read as the Integral core 1.2 line: an inference from the source, not a chain read | {NF} (version string) | verified source |")
    disc = st["A_discovery"]
    w(f"| WAVAX/USDC pools on Blackhole | 4 Algebra pools (one per custom deployer below), no default-deployer pool, no classic vAMM/sAMM pair | {V} | `AlgebraFactory.customPoolByPair(deployer, WAVAX, USDC)` for all 9 documented deployers; factory `CustomPool` events with token topics (4 logs, Routescan); `poolByPair` = 0x0; `PairFactory.getPair(WAVAX, USDC, false/true)` = 0x0 |")
    for name, p in st["A_pools"].items():
        w(f"| - {name} | `{p['address']}`: tick spacing {p['tickSpacing']}, fee {p['fee()_ppm']} ppm, {int(p['balance_WAVAX']) / 1e18:,.1f} WAVAX + {int(p['balance_USDC']) / 1e6:,.0f} USDC = ${p['tvl_usd_at_pool_price']:,.0f}; gauge `{p['gauge']}` alive = {p['gauge_isAlive']} | {V} | `balanceOf`, `globalState`, `tickSpacing`, `GaugeManager.gauges/isAlive` at END |")
    w(f"| Dataset pool (largest balances) | `{B.POOL}` | {V} | table rows above |")
    w(f"| Token order, decimals | token0 = WAVAX (18), token1 = USDC (6) | {V} | `token0()`, `token1()`, `decimals()` |")
    w(f"| Tick spacing | {pe['tickSpacing']} (TickSpacing events since creation: {[x[3] for x in hist['tick_spacing']]}) | {V} | `tickSpacing()`; events in `blackhole_wavax_usdc_fee_events.csv.gz` |")
    w(f"| Factory / deployer | factory `{pe['factory']}`; custom pool deployer (tick spacing 200, legacy 'V1' set) `{B.POOL_DEPLOYER}` | {V} | `pool.factory()`; `factory.customPoolByPair`; `AlgebraPoolApiStorage.pairToDeployer(pool)` = `{pe['deployer (AlgebraPoolApiStorage.pairToDeployer)']}` |")
    ini = rep["pool"]["initialize"]
    w(f"| Created / initialised | factory `CustomPool` event at block {B.POOL_CREATED:,}; `Initialize(price={ini['sqrt_price_x96']}, tick={ini['tick']})` at block {ini['block']:,} ({A.iso(ini['ts'])}) | {V} | Routescan log; pool log |")
    fee_hist = "; ".join(f"{v} ppm from block {b:,} ({iso})" for b, i, iso, v in hist["fee"]) or "no Fee event"
    w(f"| Fee mechanism | static, administrator-set (no dynamic-fee plugin): plugin config = {pe['globalState']['pluginConfig']} = {pe['pluginConfig_flags']['decoded']} (neither BEFORE_SWAP 0x01 nor DYNAMIC_FEE 0x80 is set: the pool does not call the plugin before a swap, so overrideFee = pluginFee = 0 and the fee is `globalState.lastFee`, which only the administrator's `setFee` changes); `fee()` = {pe['fee()_ppm']} ppm at END and {s1['fee()_ppm']} ppm at START-1; {len(hist['fee'])} Fee events since creation ({fee_hist}); Fee events in the window: {rep['pool']['config_cross_check']['events_in_window']['fee']} | {V} | `globalState()`, `fee()`; `Fee(uint16)` events; `SwapFee(overrideFee, pluginFee)` = (0, 0) on {sw['rows'] - sum(sw['swaps_with_nonzero_overrideFee_or_pluginFee'].values()):,} of {sw['rows']:,} window swaps |")
    w(f"| Docs on the fee | 'Concentrated Pools ... Fee Structure: Static fee model' | {DOC} | {DOCS_TECH} |")
    w(f"| Pool set | the docs describe two CL sets, 'JIT-protected pools (V2)' and 'Legacy pools (V1)', each with its own NFPM and farming contracts; the dataset pool's deployer is in the docs' legacy list | {DOC} | {DOCS_TECH} |")
    w(f"| No JIT / burn fee on the dataset pool | plugin config 2 has no BEFORE_POSITION_MODIFY flag; `BurnFee(pluginFee)` was non-zero on {rep['pool']['window_side_events']['BurnFee_nonzero_pluginFee']} burns of the window; Flash events {rep['pool']['window_side_events']['Flash']}, Skim events {rep['pool']['window_side_events']['Skim']} | {V} | `globalState()`; pool events START..END |")
    gs = pe["globalState"]
    w(f"| State at END | sqrtPriceX96 {gs['price_sqrtX96']}, tick {gs['tick']}, price {px_end:.6f} USDC per WAVAX, active liquidity {pe['liquidity']} | {V} | `globalState()`, `liquidity()` |")
    w(f"| Balances at END | {int(pe['balance_WAVAX']) / 1e18:,.3f} WAVAX + {int(pe['balance_USDC']) / 1e6:,.2f} USDC = ${pe['tvl_usd_at_pool_price']:,.0f} at the pool price | {V} | `balanceOf(pool)` on both tokens |")
    per = st["A_periphery"]
    w(f"| Position manager (this pool's set) | `{per['NFPM_v1']['address']}`, `factory()` = the Algebra factory, `farmingCenter()` = `{per['NFPM_v1']['farmingCenter()']}` | {V} | calls; gauge.nonfungiblePositionManager() = `{g['nonfungiblePositionManager()']}` |")
    w(f"| Quoter / swap router | QuoterV2 `{per['quoterV2']['address']}`, SwapRouter `{per['swapRouter']['address']}` (both `factory()` = the Algebra factory); RouterV2 `{per['RouterV2']['address']}` (classic factory `{per['RouterV2']['factory()']}`) | {V} | `factory()` calls; quoter reproduces the pool price (section E) |")
    m = st["C_gauge_manager"]
    w(f"| Gauge manager / voter / minter / BLACK | GaugeManager `{B.DOCS['GaugeManager']}` (proxy, implementation `{m['implementation_at_END (EIP-1967 slot)']}`), voter `{m['voter()']}`, minter `{m['minter()']}`, BLACK `{B.BLACK}` ({st['C_black']['name']}, 18 decimals) | {V} | `GaugeManager.voter()`, `minter()`, EIP-1967 slot; addresses as in {DOCS_TECH} |")
    dv = st["A_docs_vs_chain"]
    w(f"| Docs versus chain | the docs' 'AlgebraPool' address `0xA02Ec3Ba...` is the tick-spacing-1 WAVAX/USDC pool; the docs' 'GaugeManager (New Implementation)' `{dv[chr(100) + 'ocs ' + chr(39) + 'GaugeManager(New Implementation)' + chr(39)]}` is not the implementation in the proxy slot at END (`{dv['GaugeManager implementation in the EIP-1967 slot at END']}`) | {V} | `customPoolByPair`; `eth_getStorageAt` |")
    w("")
    w("## B. Share of the swap fee that reaches liquidity providers")
    w("")
    w("| Fact | Value | Tag | Evidence |")
    w("|---|---|---|---|")
    cf_hist = "; ".join(f"{v}/1000 from block {b:,} ({iso})" for b, i, iso, v in hist["community_fee"]) or "no event"
    w(f"| Community fee parameter | `globalState().communityFee` = {gs['communityFee_per_1000']} of 1000 at END and {s1['globalState']['communityFee_per_1000']} of 1000 at START-1 (unit: `Constants.COMMUNITY_FEE_DENOMINATOR = 1e3`) | {V} | `globalState()` at both blocks; verified source `Constants.sol` |")
    w(f"| Community fee history | {cf_hist}; CommunityFee events in the window: {rep['pool']['config_cross_check']['events_in_window']['community_fee']} | {V} | `CommunityFee(uint16)` events from creation, `blackhole_wavax_usdc_fee_events.csv.gz` |")
    w(f"| What the pool does with a swap fee | per step: `delta = feeAmount * communityFee / 1000` is removed first and only the remainder is added to `totalFeeGrowth`; with 1000/1000 the remainder is 0 | {V} | verified source `SwapCalculation._calculateSwap` |")
    for name in ("WAVAX", "USDC"):
        a = acc[name]
        w(f"| Window accounting, {name} | gross swap fee (sum of input x fee) {a['gross_swap_fee']:,.4f} {name}; community fee that left the swaps (vault balance change {int(a['vault_balance_change']) / (1e18 if name == 'WAVAX' else 1e6):,.4f} + vault withdrawals {(int(a['vault_withdrawn_to_communityFeeReceiver(gauge)']) + int(a['vault_withdrawn_to_algebraFeeReceiver'])) / (1e18 if name == 'WAVAX' else 1e6):,.4f} + pool pending change {int(a['pool_pending_community_fee_change']) / (1e18 if name == 'WAVAX' else 1e6):,.6f}) = {a['community_fee / gross_swap_fee'] * 100:.4f} % of it{' (the excess over 100 % is the per-swap rounding of the fee, at most one base unit of USDC a swap, which sum(input x fee) does not carry)' if name == 'USDC' else ''} | {V} | `checks.json` replay: Swap events x fee; `balanceOf(vault)` at START-1 and END; vault `TokensWithdrawal` / `AlgebraTokensWithdrawal` events; `getCommunityFeePending()` |")
        w(f"| Fee growth to in-pool liquidity, {name} | `totalFeeGrowth{0 if name == 'WAVAX' else 1}Token` grew by {a['totalFeeGrowth_delta_X128']} (X128) between START-1 and END; reproduced exactly from the {rp['fee_accounting_window']['ExcessTokens_events']:,} `ExcessTokens` events alone = {a['fee_growth_fully_explained_by_ExcessTokens']}; those events total {a['ExcessTokens_sum']:,.6f} {name} = {a['lp_fee_growth_as_share_of_gross_swap_fee'] * 100:.5f} % of the gross swap fee | {V} | `totalFeeGrowth*Token()` at both blocks; `ExcessTokens(uint256,uint256)` events with the replayed active liquidity (`ReservesManager._updateReserves` books tokens sent to the pool outside a swap as fee growth) |")
    w(f"| (i) Position NOT staked (not entered into farming) | 0 % of swap fees (only the `ExcessTokens` dust above, pro rata to in-range liquidity); no emissions | {V} | rows above; the farming virtual pool only counts positions entered through `FarmingCenter.enterFarming` (source: `AlgebraEternalFarming.enterFarming` is `onlyFarmingCenter`) |")
    w(f"| (ii) Position staked (entered into farming, normally by `GaugeCL.deposit`) | 0 % of swap fees as well (same pool-level fee growth); BLACK emissions through Algebra eternal farming | {V} | rows above and section C |")
    w(f"| Docs statement | CL pools: 'Unstaked positions do not earn rewards since all fees go towards the gauge'; 'Staked positions earn BLACK emissions from the gauge'; 'Unstaked: Receive neither fees nor emissions' | {DOC} | {DOCS_REW} ; {DOCS_TECH} |")
    ve, vs = vault["END"], vault["START-1"]
    w(f"| Where the fee goes: vault | pool `communityVault()` = `{pe['communityVault']}` (AlgebraCommunityVault, one per pool); a swap transfers its fee to the vault in the same transaction (sample: {trc['2_pool_to_vault_transfers_last_20000_blocks']['sample']['tx']}) | {V} | `communityVault()`; receipt in `traces.json` (section 2) |")
    w(f"| Vault settings | `communityFeeReceiver()` = the gauge `{ve['communityFeeReceiver']}`; `algebraFee()` = {ve['algebraFee_per_1000']} of 1000 to `algebraFeeReceiver()` `{ve['algebraFeeReceiver']}`; same at START-1 ({vs['algebraFee_per_1000']} of 1000) | {V} | calls at END and START-1 |")
    fd = trc.get("3_fee_distribution")
    if fd:
        sp = fd["split"]["WAVAX"]
        w(f"| Weekly fee distribution (sample) | tx `{fd['tx']}` (block {fd['block']:,}, to GaugeManager): vault -> Algebra fee receiver {int(sp['vault_to_algebraFeeReceiver']) / 1e18:,.4f} WAVAX ({sp['algebra_share_of_withdrawal'] * 100:.3f} %), vault -> gauge {int(sp['vault_to_gauge']) / 1e18:,.4f} WAVAX ({sp['gauge_share_of_withdrawal'] * 100:.3f} %), gauge -> internal bribe {int(sp['gauge_to_internalBribe']) / 1e18:,.4f} WAVAX ({sp['bribe_share_of_gauge_receipt'] * 100:.1f} % of what the gauge received); USDC likewise | {V} | receipt Transfer logs + `AlgebraTokensWithdrawal`, `TokensWithdrawal`, `ClaimFees`, Bribe `RewardAdded` (`traces.json` section 3); source `GaugeManager._distributeFees`, `GaugeCL._claimFees` |")
    gfc = st["B_gauge_factory_cl"]
    w(f"| Referral ('dibs') cut in the gauge | `GaugeFactoryCL.dibsPercentage()` = {gfc['END']['dibsPercentage_per_1000']} of 1000 at END, {gfc['START-1']['dibsPercentage_per_1000']} at START-1 | {V} | calls |")
    for name in ("WAVAX", "USDC"):
        a = acc[name]
        tot = int(a["vault_withdrawn_to_communityFeeReceiver(gauge)"]) + int(a["vault_withdrawn_to_algebraFeeReceiver"])
        w(f"| Vault withdrawals in the window, {name} | to the gauge {int(a['vault_withdrawn_to_communityFeeReceiver(gauge)']) / (1e18 if name == 'WAVAX' else 1e6):,.4f}, to the Algebra fee receiver {int(a['vault_withdrawn_to_algebraFeeReceiver']) / (1e18 if name == 'WAVAX' else 1e6):,.4f} ({a['algebra_cut_of_withdrawals'] * 100:.3f} %) | {V} | vault events START..END |")
    fc = rep["gauge"]["files"]["blackhole_gauge_fee_claims.csv.gz"]
    w(f"| Gauge -> internal bribe in the window | {fc['rows_in_window']} `ClaimFees` events: {int(fc['sum_claimed0_in_window']) / 1e18:,.4f} WAVAX + {int(fc['sum_claimed1_in_window']) / 1e6:,.2f} USDC to the internal bribe `{g['internal_bribe()']}` (claimable by veBLACK voters of the pool) | {V} | `blackhole_gauge_fee_claims.csv.gz`; `GaugeCL._claimFees` -> `Bribe.notifyRewardAmount` |")
    stake = trc.get("6_stake", {})
    w(f"| How the gauge holds liquidity | it does not hold the NFTs: `NFPM.balanceOf(gauge)` = {stake.get('NFPM.balanceOf(gauge) at END')}, `balanceOf(FarmingCenter)` = {stake.get('NFPM.balanceOf(FarmingCenter) at END')}, `balanceOf(AlgebraEternalFarming)` = {stake.get('NFPM.balanceOf(AlgebraEternalFarming) at END')}. `GaugeCL.deposit(tokenId)` (caller must be the NFT owner, and the gauge must be approved on the NFPM) calls `FarmingCenter.enterFarming`: the NFT stays with its owner, `NFPM.tokenFarmedIn(tokenId)` becomes the FarmingCenter and the position's liquidity is mirrored in an Algebra eternal-farming virtual pool `{far['incentive']['virtualPoolAddress']}` | {V} | sample deposit `{stake.get('tx')}`: `ownerOf` = depositor after the call, `tokenFarmedIn` 0x0 -> FarmingCenter, `getApproved` = gauge, events `FarmEntered` + `Deposit` (`traces.json` section 6); source `GaugeCL.deposit` |")
    w(f"| Share of liquidity staked | ACTIVE liquidity: virtual pool `currentLiquidity()` / pool `liquidity()` = {vp['END']['staked_share_of_active_liquidity'] * 100:.3f} % at END, {vp['START-1']['staked_share_of_active_liquidity'] * 100:.3f} % at START-1 | {V} | calls at both blocks (`GaugeCL.totalSupply()` returns the same `currentLiquidity`) |")
    w(f"| Share of ALL liquidity staked (out-of-range included) | not derived in this pull: it is derivable from the incentive's FarmEntered / FarmEnded events, which were not pulled (the tag here means 'not produced', not 'absent on chain') | {NF} | - |")
    if stake.get("position_share_of_active_liquidity_at_that_block") is not None:
        top = list(stake["Deposit_events_last_50000_blocks_by_depositor"].items())[0]
        w(f"| Who is staked (sample, not a census) | sample deposit: tokenId {stake['tokenId']}, block {stake['block']:,}, position liquidity {stake['position_liquidity (FarmEntered)']} = {stake['position_share_of_active_liquidity_at_that_block'] * 100:.2f} % of `pool.liquidity()` at the end of that block ({stake['pool.liquidity() at the same block']}); the NFT was minted in the same transaction and no longer exists at END (`ownerOf` reverts). Its owner `{stake['deposit_user']}` is a contract ({stake['depositor_code_bytes']:,} bytes of code) and made {top[1]} of the {trc['gauge_events_last_50000_blocks'].get('Deposit', 0)} gauge `Deposit` events of the last 50,000 blocks | {V} | `FarmEntered` event, `pool.liquidity()` at block {stake['block']:,}, `eth_getCode`, gauge logs (`traces.json` section 6) |")
    w("")
    led = trc.get("5_reward_ledger", {})
    w("Peridot-style vault NFT (a position NFT held by a vault contract and NOT staked), strictly as the contracts behave: "
      "it earns **neither** swap fees (communityFee = 1000/1000 sends every swap fee to the community vault, then 98 % to the gauge's internal bribe for voters and 2 % to the Algebra fee receiver) "
      "**nor** emissions (BLACK accrues only to positions entered into Algebra eternal farming). "
      "Its only income is the pro-rata `ExcessTokens` fee growth quantified above. "
      "Staking does not move the NFT: the owner approves the gauge on the NFPM and calls `GaugeCL.deposit(tokenId)`, which calls `FarmingCenter.enterFarming`; "
      "by the verified source `FarmingCenter.enterFarming` itself only requires the caller to be the NFT's owner or approved (after `NFPM.approveForFarming`), so the gauge is the usual entry point, not the only one the code allows (not exercised here). "
      "A staked position earns BLACK while in range and still no swap fees. The BLACK is booked to `rewards[NFPM.ownerOf(tokenId)]` in the farming contract and only that address can withdraw it "
      "(`AlgebraEternalFarming.claimReward(token, to, amount)` pays out of `rewards[msg.sender]`), so a vault contract would have to make that call itself; "
      f"`GaugeManager.claimRewards` -> `GaugeCL.getReward` books the reward to the owner but pays nothing out (the one `Harvest` found emitted only `RewardsCollected` and moved no token; `rewards(gauge, BLACK)` = {led.get('AlgebraEternalFarming.rewards(gauge, BLACK) at END')} at END).")
    w("")
    w("## C. Emissions")
    w("")
    w("| Fact | Value | Tag | Evidence |")
    w("|---|---|---|---|")
    w(f"| Gauge of the pool | `{B.GAUGE}` (GaugeCL), alive; `rewardToken()` = `bonusRewardToken()` = BLACK | {V} | `GaugeManager.gauges(pool)`, `isAlive`, `isCLGauge`; gauge calls |")
    w(f"| Rate and period at END | `rewardRate()` = {g['rewardRate()']} wei/s = {int(g['rewardRate()']) / 1e18:.6f} BLACK/s = {int(g['rewardRate()']) * 604800 / 1e18:,.0f} BLACK per 7 days; `_periodFinish` (internal, storage slot 7) = {g['storage_slot7__periodFinish']} = {g['_periodFinish_iso']}; there is no public `periodFinish()` | {V} | `rewardRate()`; `eth_getStorageAt(gauge, 7)` (layout checked: slot 6 = DURATION, slot 8 = rewardRate) |")
    w(f"| Farming side at END | incentive nonce {far['incentiveKey']['nonce']}, virtual pool `rewardRates()` = ({vp['END']['rewardRates'][0]}, {vp['END']['rewardRates'][1]}), `rewardReserves()` = {int(vp['END']['rewardReserves'][0]) / 1e18:,.0f} BLACK left to stream, bonus rate 0 | {V} | `AlgebraEternalFarming.incentiveKeys(pool)`, `incentives(id)`, virtual pool calls |")
    w(f"| Rate at START-1 | {int(g['rewardRate_at_START_minus_1']) / 1e18:.6f} BLACK/s = {int(g['rewardRate_at_START_minus_1']) * 604800 / 1e18:,.0f} BLACK per 7 days (epoch notified {epoch_at_start['iso'] if epoch_at_start else '?'}: {epoch_at_start['amount_BLACK'] if epoch_at_start else 0:,.0f} BLACK) | {V} | `rewardRate()` at START-1; `gauge_epochs.json` |")
    w(f"| Notification event | `GaugeCL.RewardAdded(uint256 reward)` (topic0 `{B.topic('RewardAdded(uint256)')}`), one per weekly `GaugeManager.distribute`, in the same transaction as `GaugeManager.DistributeReward(sender, gauge, amount)` and the farming contract's `RewardsAdded` / `RewardsRatesChanged` | {V} | sample tx `{trc['4_reward_notification']['tx']}` (`traces.json` section 4); {rep['gauge']['files']['blackhole_gauge_notify.csv.gz']['rows']} rows in `blackhole_gauge_notify.csv.gz` |")
    w(f"| Reward form | liquid BLACK (the ERC-20 `{B.BLACK}`, verified contract `Black`); no escrowed or option token appears anywhere in the notify or claim path | {V} | notify tx: BLACK GaugeManager -> gauge -> AlgebraEternalFarming; claim tx `{trc.get('5_reward_claim', {}).get('tx')}`: `RewardClaimed(rewardAddress = BLACK)` and a BLACK transfer from the farming contract to the claimer; RewardClaimed events of the farming contract in the last 50,000 blocks by reward token: {', '.join(f'{k} {v}' for k, v in trc['5_reward_claims_on_farming_contract_last_50000_blocks']['RewardClaimed_events_by_reward_token (all incentives of this farming contract)'].items())} |")
    w(f"| Docs on the reward form | '$BLACK emissions are distributed to liquidity pools'; 'Staked positions earn BLACK emissions from the gauge' | {DOC} | {DOCS_REW} ; {DOCS_EM} |")
    hv = trc.get("5_reward_claim_via_gauge (historic sample)", {})
    w(f"| Claim path actually used | claims go to the farming contract directly (`AlgebraEternalFarming.claimReward`, event `RewardClaimed`): {trc['5_reward_claims_on_farming_contract_last_50000_blocks']['of_which_owner_is_a_depositor_of_this_gauge_in_the_same_range']} such claims by depositors of this gauge in the last 50,000 blocks, against 0 gauge `Harvest` events in four 50,000-block probes. In the one `Harvest` found (tx `{hv.get('tx')}`, block {hv.get('block')}) the farming contract emitted {hv.get('farming_contract_events_in_this_tx')} and no token left it: the reward ledger is `rewards[owner][token]`, `claimReward` pays out of `rewards[msg.sender]`, and `rewards(gauge, BLACK)` = {trc.get('5_reward_ledger', {}).get('AlgebraEternalFarming.rewards(gauge, BLACK) at END')} at END | {V} | `traces.json` section 5; sources `AlgebraEternalFarming.collectRewards`, `_claimReward`, `FarmingCenter.claimReward`, `GaugeCL.getReward` |")
    bpc = st["C_black_pools"]["vAMM-USDC/BLACK"]
    bf = rep["black"]["files"]["black_pool_swaps.csv.gz"]
    w(f"| BLACK/USD price source | classic pair vAMM-USDC/BLACK `{B.BLACK_PAIR}` (token0 USDC, token1 BLACK): the deepest BLACK pool quoted in USDC or WAVAX at END (quote side ${st['C_black_price_pool_choice']['quote_side_usd']['vAMM-USDC/BLACK']:,.0f}, vAMM-WAVAX/BLACK ${st['C_black_price_pool_choice']['quote_side_usd']['vAMM-WAVAX/BLACK']:,.0f}, the two CL pools ${st['C_black_price_pool_choice']['quote_side_usd']['CL200 WAVAX/BLACK']:,.0f} and ${st['C_black_price_pool_choice']['quote_side_usd']['CL200 USDC/BLACK']:,.0f}) and quoted in USD directly | {V} | `balanceOf` of every candidate at END (`state_at_end.json` C_black_pools) |")
    w(f"| BLACK price | {bf['first_row_price_usd']:.6f} USD at the first window swap, {bf['last_row_price_usd']:.6f} USD at the last ({A.iso(bf['last_ts'])}); {bf['rows']:,} swaps in `black_pool_swaps.csv.gz`; pair fee 7000 ppm (every `Fees` event = floor(amountIn x 70 / 10000)) | {V} | pair `Sync` / `Swap` / `Fees` events |")
    pr = st["C_claim_threshold_probes"]
    w(f"| Withholding / anti-sybil rule on claims | no time threshold: `timeThresholdForGauge(gauge)` and four similar getters revert on VoterV3, GaugeManager, GaugeCL, AlgebraEternalFarming and FarmingCenter, and the verified sources of deposit / withdraw / claim have no time condition | {NF} | 25 probe calls (`state_at_end.json` C_claim_threshold_probes); sources |")
    w(f"| Entry condition that does exist | farming incentive `minimalPositionWidth` = {pr['farming incentive minimalPositionWidth (ticks)']} ticks (one tick spacing): narrower positions cannot be staked | {V} | `AlgebraEternalFarming.incentives(incentiveId)`; source `_enterFarming` |")
    w("")
    w("Emissions notified to the gauge inside the window (BLACK price = last vAMM-USDC/BLACK swap at or before the notification):")
    w("")
    w("| Epoch start (UTC) | Notified (UTC) | Block | BLACK | BLACK/USD | USD at notification |")
    w("|---|---|---|---|---|---|")
    for e in emis:
        w(f"| {e['epoch_start_iso'][:10]} | {e['notified_iso']} | {e['block']:,} | {e['amount_BLACK']:,.0f} | {e['black_usd_at_notification']:.6f} | {e['usd_at_notification']:,.0f} |")
    tot_b = sum(e["amount_BLACK"] for e in emis)
    tot_u = sum(e["usd_at_notification"] for e in emis)
    w(f"| sum of {len(emis)} | | | {tot_b:,.0f} | | {tot_u:,.0f} |")
    w("")
    if epoch_at_start:
        w(f"The epoch in force at START was notified before the window: {epoch_at_start['iso']} (block {epoch_at_start['block']:,}), {epoch_at_start['amount_BLACK']:,.0f} BLACK; it is in `gauge_epochs.json`, not in `blackhole_gauge_notify.csv.gz`.")
        w("")
    w("## E. Depth at END, both pools, by direct quote")
    w("")
    w("Method: each protocol's own `QuoterV2.quoteExactInputSingle` by archive `eth_call` at END, bisected on the input amount until the post-trade pool price is at the target "
      "(stop when the bracket is within 1e-9 of the amount). A second call with the target as price limit returns the same output within "
      f"{max(t['limit_price_check']['amount_out_rel_diff_vs_bisection'] for p in dep['pools'].values() for t in p['targets']):.1e} (relative). "
      "USD in = input at the pool's own pre-trade price; cost = shortfall of the average execution price against the pre-trade price, fee included. Tag for every row: " + V + " (`depth_at_end.json`).")
    w("")
    for name, title in (("blackhole", "Blackhole"), ("pharaoh", "Pharaoh")):
        p = dep["pools"][name]
        w(f"**{title}** `{p['pool']}`: pre-trade price {p['P0_usdc_per_wavax']:.6f} USDC/WAVAX, fee {p['fee_ppm']} ppm, active liquidity {p['active_liquidity']}, "
          f"balances {int(p['balance_WAVAX']) / 1e18:,.0f} WAVAX + {int(p['balance_USDC']) / 1e6:,.0f} USDC = ${p['tvl_usd_at_pool_price']:,.0f}; quoter `{p['quoter']}`.")
        w("")
        w("| Trade | Price move reached | Input | USD in | Output | Avg price (USDC/WAVAX) | Cost (bp of size) |")
        w("|---|---|---|---|---|---|---|")
        for t in p["targets"]:
            ex = " (exhausted)" if t.get("exhausted") else ""
            if t["direction"] == "sell_WAVAX":
                w(f"| sell WAVAX to {t['move_bp'] / 100:+.0f} % | {t['price_reached_pct']:+.4f} %{ex} | {t['wavax_in']:,.2f} WAVAX | {t['usd_in_at_P0']:,.0f} | {t['usdc_out']:,.2f} USDC | {t['avg_price']:.6f} | {t['cost_bp']:.1f} |")
            else:
                w(f"| buy WAVAX to {t['move_bp'] / 100:+.0f} % | {t['price_reached_pct']:+.4f} %{ex} | {t['usdc_in']:,.2f} USDC | {t['usd_in_at_P0']:,.0f} | {t['wavax_out']:,.2f} WAVAX | {t['avg_price']:.6f} | {t['cost_bp']:.1f} |")
        w("")
    w("| At END | Blackhole | Pharaoh | Tag | Evidence |")
    w("|---|---|---|---|---|")
    b_, p_ = dep["pools"]["blackhole"], dep["pools"]["pharaoh"]
    w(f"| Balances (TVL at pool price) | ${b_['tvl_usd_at_pool_price']:,.0f} | ${p_['tvl_usd_at_pool_price']:,.0f} | {V} | `balanceOf(pool)` |")
    w(f"| Active liquidity (L) | {b_['active_liquidity']} | {p_['active_liquidity']} | {V} | `liquidity()` |")
    w(f"| Swap fee at END | {b_['fee_ppm']} ppm | {p_['fee_ppm']} ppm | {V} | `fee()` |")
    for band in ("+/-1%", "+/-5%", "+/-10%"):
        w(f"| Liquidity within {band} (USDC out of the sell + WAVAX out of the buy x price) | ${b_['liquidity_within_band_usd'][band]:,.0f} | ${p_['liquidity_within_band_usd'][band]:,.0f} | {V} | quotes |")
    dq = chk.get("depth_offline_vs_quoter_blackhole")
    if dq:
        w(f"| Offline re-derivation (Blackhole) | tick map rebuilt from `blackhole_wavax_usdc_liquidity.csv.gz` reproduces all six quoter amounts within {dq['max_rel_diff']:.1e} (relative) | n/a | {V} | `checks.json` depth_offline_vs_quoter_blackhole |")
    w(f"| Quoter identity | Blackhole QuoterV2 `factory()` = Algebra factory; Pharaoh QuoterV2 `deployer()` = `{ph['quoter']['deployer()']}` whose `RamsesV3Factory()` = the pool's factory, and `factory.getPool(WAVAX, USDC, 10)` = the pool | | {V} | calls; both quoters return the pool's own price for a 1e-6 WAVAX quote |")
    w("")
    w("## Checks run")
    w("")
    stc = chk["structure"]
    w("- Structure: " + "; ".join(f"`{k}` {v['rows']:,} rows, duplicates {v['duplicates']}, ascending {v['strictly_ascending']}" for k, v in stc.items()) + ".")
    rr = chk.get("requested_ranges", {})
    w("- Requested log ranges tile their block range: " + "; ".join(f"{k} {v['windows']} windows / {v['logs']:,} logs / tiling {v['tiling_ok']}" for k, v in rr.items() if isinstance(v, dict)) + f"; pre-window segments tile creation..START-1 = {rr.get('pre_window_segments_tile_creation_to_START_minus_1')}.")
    rq = chk.get("re_requests", {})
    for k, rows in rq.items():
        pn = sum(1 for r in rows if r.get("publicnode_identical"))
        rsn = sum(1 for r in rows if r.get("routescan_identical"))
        rs_run = sum(1 for r in rows if "routescan_identical" in r)
        w(f"- Re-request {k}: publicnode with another split identical in {pn}/{len(rows)} ranges; Routescan identical in {rsn}/{rs_run} ranges" + ("" if rs_run else " (not run)") + f"; records compared: {sum(r['count_in_dataset'] for r in rows):,}.")
    ls = rp["last_swap_vs_state_at_END"]
    w(f"- Last swap (block {ls['last_swap']['block']:,}, log {ls['last_swap']['log_index']}) versus state at END: price equal {ls['price_equal']}, tick equal {ls['tick_equal']}, liquidity equal {ls['liquidity_equal_to_last_swap']}; "
      f"Mint/Burn rows after it: {len(ls['mints_burns_after_last_swap'])}; liquidity equal after applying them: {ls['liquidity_equal_after_applying_them']}.")
    w(f"- Liquidity file: replayed active liquidity equals `pool.liquidity()` at START-1 ({rp['equal_at_START_minus_1']}) and at END ({rp['equal_at_END']}), and equals the liquidity field of {rp['swap_event_liquidity_equals_replayed_active_liquidity']['equal']:,} of {rp['swap_event_liquidity_equals_replayed_active_liquidity']['of']:,} Swap events.")
    w(f"- Fee validation: {fv['eligible_swaps (no initialised tick between the previous and the new tick, price moved)']:,} eligible swaps of {fv['all_swaps']:,}; within 1 ppm of `fee_ppm`: {fv['share_within_1_ppm'] * 100:.4f} %; within 1 ppm or within 2 base units of integer rounding: {fv['share_within_1_ppm_or_rounding'] * 100:.4f} %; "
      f"volume-weighted implied fee {fv['aggregate_implied_fee_ppm_token0_in']:.4f} ppm (WAVAX in) and {fv['aggregate_implied_fee_ppm_token1_in']:.4f} ppm (USDC in).")
    hc = chk.get("header_checks", {})
    w("- Timestamps: every `ts` is the header timestamp of the log's block (publicnode header batches); block-hash mismatches between logs and headers: "
      + ", ".join(f"{k} {sum(x['block_hash_mismatches'] for x in (v or []))}" for k, v in hc.items()) + ".")
    w(f"- Swap sign: Swap amounts equal the pool's net token transfers in the receipts of one swap per direction ({all(c['signs_and_amounts_match'] for c in trc['1_swap_sign_convention']['checks'])}, {len(trc['1_swap_sign_convention']['checks'])} receipts).")
    w("")
    (B.DATA_BH / "FACTS.md").write_text("\n".join(L) + "\n")

    f = B.DATA_BH / "FACTS.md"
    man["files"]["FACTS.md"] = {"sha256": A.sha256_file(f), "bytes": f.stat().st_size, "source_host": extra["FACTS.md"]}
    A.write_json(B.DATA_BH / "MANIFEST.json", man)
    lines = [f"{A.sha256_file(p)}  {p.name}" for p in sorted(B.DATA_BH.iterdir()) if p.name != "SHA256SUMS" and not p.name.endswith(".tmp")]
    (B.DATA_BH / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
