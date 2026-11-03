"""Offline: snapshot.json + sims.json + holder-evidence.json -> balance-sheet.json. No RPC. Integer arithmetic for token amounts; floats only for display values."""
import json, math
from pathlib import Path
HERE = Path(__file__).parent
S = json.load(open(HERE / "snapshot.json")); D = json.load(open(HERE / "sims.json"))
try:
    EV = json.load(open(HERE / "holder-evidence.json"))
except Exception:
    EV = None
R = S["reads"]
def words(h):
    h = h[2:]
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]
def w(n, i=0):
    if isinstance(R[n], dict):
        raise RuntimeError(n + str(R[n]))
    return words(R[n])[i]
def ok(n):
    return not isinstance(R[n], dict)
def sw(v):
    return v - (1 << 256) if v >= (1 << 255) else v
Q96 = 1 << 96; E18 = 10**18
PX = w("g.prices", 0); UX = w("g.prices", 1)  # USD 1e18 per whole token
sqrtp = S["pool"]["sqrtPriceX96"]
# pool spot: USDG per NVDA. token0=USDG(6) token1=NVDA(18). raw1/raw0 = (sqrt/Q96)^2
SPOTX = (Q96 * Q96 * 10**12 * E18) // (sqrtp * sqrtp)
ref = words(R["g.validateRemoval"])[2]
oracle_px, spot_px, usdg_px = PX / E18, SPOTX / E18, UX / E18
def usd(n, u, px):
    return n / E18 * px + u / 1e6 * usdg_px
def row(name, n=0, u=0, n_spot=None, u_spot=None, note=""):
    ns = n if n_spot is None else n_spot; us = u if u_spot is None else u_spot
    return {"line": name, "NVDA_raw": n, "USDG_raw": u, "NVDA": n / E18, "USDG": u / 1e6, "USD_at_oracle": usd(n, u, oracle_px), "NVDA_raw_spotview": ns, "USDG_raw_spotview": us, "USD_at_spot": usd(ns, us, spot_px), "note": note}

L = dict(zip(["stockPrincipal", "usdgPrincipal", "stockIdle", "usdgIdle", "cumulativeLossUSDG", "lastCheckpoint"], words(R["v.ledger"])))
pos_spot = dict(zip(["tokenId", "liquidity", "stock", "usdg"], words(R["a.positionState"])))
pos_ref = dict(zip(["tokenId", "liquidity", "stock", "usdg"], words(R["a.stateAtRef"])))
ticks = words(R["a.positionTicks"]); tl, tu = sw(ticks[0]), sw(ticks[1])
b = lambda h, t: w(f"bal.{h}.{t}")
M = {}
for mn in ("pNVDA", "pUSDG"):
    g = lambda f: w(f"m.{mn}.{f}")
    x = {f: g(f) for f in ("totalSupply", "exchangeRateStored", "exchangeRateCurrent", "totalBorrows", "totalBorrowsCurrent", "totalReserves", "getCash", "reserveFactorMantissa", "accrualBlockNumber", "vaultAccountedAssets", "vaultLiquidAssets", "vaultWithdrawableAssets", "totalManagedAssets", "cumulativeVaultLoss", "vaultBufferMantissa", "vaultPaused", "borrowRatePerBlock", "supplyRatePerBlock")}
    x["local"] = b(mn, "NVDA" if mn == "pNVDA" else "USDG")
    x["interest"] = x["totalBorrowsCurrent"] - x["totalBorrows"]
    x["reservesCurrent"] = x["totalReserves"] + x["interest"] * x["reserveFactorMantissa"] // E18
    x["claimStored"] = x["totalSupply"] * x["exchangeRateStored"] // E18
    x["claimCurrent"] = x["totalSupply"] * x["exchangeRateCurrent"] // E18
    x["netStored"] = x["local"] + x["vaultAccountedAssets"] + x["totalBorrows"] - x["totalReserves"]
    x["netCurrent"] = x["local"] + x["vaultAccountedAssets"] + x["totalBorrowsCurrent"] - x["reservesCurrent"]
    x["identityResidualStored"] = x["netStored"] - x["claimStored"]
    x["identityResidualCurrent"] = x["netCurrent"] - x["claimCurrent"]
    x["principalEqualsAccounted"] = x["vaultAccountedAssets"] == (L["stockPrincipal"] if mn == "pNVDA" else L["usdgPrincipal"])
    M[mn] = x
N_, U_ = M["pNVDA"], M["pUSDG"]

# ---------- uncollected fees, analytic from PoolManager storage ----------
fg0, fg1 = w("pool.fg0"), w("pool.fg1"); cur = S["pool"]["tick"]; MOD = 1 << 256
def inside(fg, fol, fou):
    below = fol if cur >= tl else (fg - fol) % MOD
    above = fou if cur < tu else (fg - fou) % MOD
    return (fg - below - above) % MOD
i0 = inside(fg0, w("tick.lower.fo0"), w("tick.upper.fo0")); i1 = inside(fg1, w("tick.lower.fo1"), w("tick.upper.fo1"))
pl = w("position.liq")
fee_usdg = (((i0 - w("position.fgi0")) % MOD) * pl) >> 128; fee_nvda = (((i1 - w("position.fgi1")) % MOD) * pl) >> 128
sim_ad = words(D["adapter.collectFees(from=vault,eth_call)"]["r"])
vc = D["vault.collectFees"]["steps"][0]; sim_v = words(vc["ret"])
RFB = words(R["v.pairConfig"])[9]
fees = {"analytic_from_PoolManager_storage": {"NVDA_raw": fee_nvda, "USDG_raw": fee_usdg}, "sim_adapter_collectFees_from_vault_eth_call": {"NVDA_raw": sim_ad[0], "USDG_raw": sim_ad[1]}, "sim_vault_collectFees_from_keeper_eth_simulateV1": {"NVDA_raw": sim_v[0], "USDG_raw": sim_v[1]},
        "agree": fee_nvda == sim_ad[0] == sim_v[0] and fee_usdg == sim_ad[1] == sim_v[1], "reserveFeeBps": RFB, "toReserve": {"NVDA_raw": sim_ad[0] * RFB // 10000, "USDG_raw": sim_ad[1] * RFB // 10000},
        "note": "On collection net fees raise idle AND principal equally on each side, so they cannot reduce a native shortage."}
FN, FU = sim_ad[0], sim_ad[1]

# ---------- balance sheet ----------
vault_diff = {"NVDA_balance_minus_ledgerIdle": b("vault", "NVDA") - L["stockIdle"], "USDG_balance_minus_ledgerIdle": b("vault", "USDG") - L["usdgIdle"]}
rs = {t: {"available": w(f"r.available.{t}"), "accounted": w(f"r.accounted.{t}"), "balance": b("reserve", t)} for t in ("NVDA", "USDG")}
assets = [
    row("pNVDA market local cash", n=N_["local"]), row("pUSDG market local cash", u=U_["local"]),
    row("pNVDA borrows (current, simulated accrual)", n=N_["totalBorrowsCurrent"]), row("pUSDG borrows (current, simulated accrual)", u=U_["totalBorrowsCurrent"]),
    row("vault idle (ledger)", n=L["stockIdle"], u=L["usdgIdle"]),
    row("LP position (oracle ref / pool spot amounts)", n=pos_ref["stock"], u=pos_ref["usdg"], n_spot=pos_spot["stock"], u_spot=pos_spot["usdg"]),
    row("uncollected fees, gross (simulated collect, equals storage calc)", n=FN, u=FU, note="20% would go to the loss reserve on collection"),
]
claims = [
    row("pNVDA suppliers: totalSupply x exchangeRateCurrent", n=N_["claimCurrent"], note=f"at stored rate {N_['claimStored']}"), row("pUSDG suppliers: totalSupply x exchangeRateCurrent", u=U_["claimCurrent"], note=f"at stored rate {U_['claimStored']}"),
    row("pNVDA market reserves (current)", n=N_["reservesCurrent"]), row("pUSDG market reserves (current)", u=U_["reservesCurrent"]),
]
elim = [row("MEMO, ELIMINATED: vault principal, stock (asset of pNVDA, liability of vault)", n=L["stockPrincipal"]), row("MEMO, ELIMINATED: vault principal, USDG (asset of pUSDG, liability of vault)", u=L["usdgPrincipal"])]
sub_claims = []
reserve_lines = [row("loss reserve available (contingent, capped)", n=rs["NVDA"]["available"], u=rs["USDG"]["available"])]
stray = {h: {"NVDA": b(h, "NVDA"), "USDG": b(h, "USDG")} for h in ("adapter", "guard", "settlementLib", "timelock", "marginVault", "insurance", "flashVault", "feeDistributor", "swapModule", "executor", "liquidator", "riskEngine", "governor", "keeper")}
sum_col = lambda rows, c: sum(r[c] for r in rows)
TA_N, TA_U = sum_col(assets, "NVDA_raw"), sum_col(assets, "USDG_raw"); TC_N, TC_U = sum_col(claims, "NVDA_raw"), sum_col(claims, "USDG_raw")
TA_Ns, TA_Us = sum_col(assets, "NVDA_raw_spotview"), sum_col(assets, "USDG_raw_spotview")
recon = {
    "claims_total": {"NVDA_raw": TC_N, "USDG_raw": TC_U, "USD_oracle": usd(TC_N, TC_U, oracle_px), "USD_spot": usd(TC_N, TC_U, spot_px)},
    "assets_total_oracleRefView": {"NVDA_raw": TA_N, "USDG_raw": TA_U, "USD_oracle": usd(TA_N, TA_U, oracle_px)},
    "assets_total_spotView": {"NVDA_raw": TA_Ns, "USDG_raw": TA_Us, "USD_spot": usd(TA_Ns, TA_Us, spot_px)},
    "residual_incl_fees_oracleRef": {"NVDA_raw": TA_N - TC_N, "USDG_raw": TA_U - TC_U, "USD": usd(TA_N, 0, oracle_px) - usd(TC_N, 0, oracle_px) + (TA_U - TC_U) / 1e6 * usdg_px},
    "residual_incl_fees_spot": {"NVDA_raw": TA_Ns - TC_N, "USDG_raw": TA_Us - TC_U, "USD": (TA_Ns - TC_N) / E18 * spot_px + (TA_Us - TC_U) / 1e6 * usdg_px},
    "residual_excl_fees_oracleRef": {"NVDA_raw": TA_N - FN - TC_N, "USDG_raw": TA_U - FU - TC_U, "USD": (TA_N - FN - TC_N) / E18 * oracle_px + (TA_U - FU - TC_U) / 1e6 * usdg_px},
    "residual_excl_fees_spot": {"NVDA_raw": TA_Ns - FN - TC_N, "USDG_raw": TA_Us - FU - TC_U, "USD": (TA_Ns - FN - TC_N) / E18 * spot_px + (TA_Us - FU - TC_U) / 1e6 * usdg_px},
    "market_identity": {mn: {"identityResidualStored_raw": M[mn]["identityResidualStored"], "identityResidualCurrent_raw": M[mn]["identityResidualCurrent"], "principalEqualsAccounted": M[mn]["principalEqualsAccounted"]} for mn in M},
    "elimination_statement": "Market assets = local cash + vault principal + borrows - market reserves = supplier claims. Principal is an intra-system claim: replacing it with what the vault actually holds (idle + LP + fees) gives the consolidated residual, which is exactly (vault assets - principal) per token.",
    "vault_assets_minus_principal_excl_fees_oracleRef": {"NVDA_raw": L["stockIdle"] + pos_ref["stock"] - L["stockPrincipal"], "USDG_raw": L["usdgIdle"] + pos_ref["usdg"] - L["usdgPrincipal"]},
}
recon["residual_incl_reserve_oracleRef_USD"] = recon["residual_incl_fees_oracleRef"]["USD"] + usd(rs["NVDA"]["available"], rs["USDG"]["available"], oracle_px)
recon["residual_incl_reserve_spot_USD"] = recon["residual_incl_fees_spot"]["USD"] + usd(rs["NVDA"]["available"], rs["USDG"]["available"], spot_px)

# ---------- B ----------
benchmark = usd(L["stockPrincipal"], L["usdgPrincipal"], oracle_px)
gross_ref = usd(L["stockIdle"] + pos_ref["stock"], L["usdgIdle"] + pos_ref["usdg"], oracle_px)
benchmark_spot = usd(L["stockPrincipal"], L["usdgPrincipal"], spot_px)
gross_spot = usd(L["stockIdle"] + pos_spot["stock"], L["usdgIdle"] + pos_spot["usdg"], spot_px)
cp = D["vault.checkpoint"]["steps"][0]
cpe = next(e for e in cp["logs"] if e["event"] == "PairCheckpoint")["data"]
B = {
    "1_realised": {"cumulativeLossUSDG_1e18": L["cumulativeLossUSDG"], "USD": L["cumulativeLossUSDG"] / E18, "pNVDA.cumulativeVaultLoss_raw": N_["cumulativeVaultLoss"], "pUSDG.cumulativeVaultLoss_raw": U_["cumulativeVaultLoss"], "note": "already written into principals and exchange rates"},
    "2_marked": {"benchmark_USD_oracle": benchmark, "gross_at_oracle_ref_USD": gross_ref, "gross_minus_benchmark_excl_fees_USD": gross_ref - benchmark, "uncollected_fees_USD_oracle": usd(FN, FU, oracle_px),
                 "at_spot": {"benchmark": benchmark_spot, "gross": gross_spot, "gross_minus_benchmark": gross_spot - benchmark_spot},
                 "sim_checkpoint": {"status": cp["status"], "pnlUSDG_ret": sw(words(cp["ret"])[0]), "event_stockAssets": cpe[0], "event_usdgAssets": cpe[1], "event_benchmark": cpe[2], "event_pnl": sw(cpe[3]), "event_stockPrincipal": cpe[4], "event_usdgPrincipal": cpe[5], "gasUsed": cp["gasUsed"]}},
    "3_withdrawal_liquidity": {
        "vault.withdrawableAssets": {"NVDA_raw": w("v.withdrawableAssets.stock"), "USDG_raw": w("v.withdrawableAssets.usdg")}, "vault.liquidAssets": {"NVDA_raw": w("v.liquidAssets.stock"), "USDG_raw": w("v.liquidAssets.usdg")},
        "market.getCash": {"pNVDA_raw": N_["getCash"], "pUSDG_raw": U_["getCash"]}, "market.local": {"pNVDA_raw": N_["local"], "pUSDG_raw": U_["local"]},
        "singleHolderMax_sim": D["redeem.singleHolderMax"]},
    "4_solvency": {"oracle": {"assets_incl_fees": usd(TA_N, TA_U, oracle_px), "claims": usd(TC_N, TC_U, oracle_px), "surplus": usd(TA_N, TA_U, oracle_px) - usd(TC_N, TC_U, oracle_px)},
                   "spot": {"assets_incl_fees": usd(TA_Ns, TA_Us, spot_px), "claims": usd(TC_N, TC_U, spot_px), "surplus": usd(TA_Ns, TA_Us, spot_px) - usd(TC_N, TC_U, spot_px)}},
}
def run_total(run, mkt):
    return sum(r.get("redeemed", 0) for r in run["rows"] if r["market"] == mkt)
for nm in ("bankrun.USDGfirst", "bankrun.NVDAfirst"):
    B["3_withdrawal_liquidity"][nm] = {"USDG_out_raw": run_total(D[nm], "pUSDG"), "NVDA_out_raw": run_total(D[nm], "pNVDA"), "rows": D[nm]["rows"], "final": D[nm]["final"].get("post")}

# ---------- C ----------
short = L["usdgPrincipal"] - L["usdgIdle"] - pos_ref["usdg"]
surplus_n = L["stockIdle"] + pos_ref["stock"] - L["stockPrincipal"]
short_spot = L["usdgPrincipal"] - L["usdgIdle"] - pos_spot["usdg"]; surplus_spot = L["stockIdle"] + pos_spot["stock"] - L["stockPrincipal"]
total_backing_nvda_value = (N_["local"] + N_["totalBorrowsCurrent"] + L["stockIdle"] + pos_ref["stock"]) / E18 * oracle_px
total_backing_usdg_value = (U_["local"] + U_["totalBorrowsCurrent"] + L["usdgIdle"] + pos_ref["usdg"]) / 1e6
C_ = {"oracle_ref": {"USDG_shortage_raw": short, "NVDA_surplus_raw": surplus_n, "NVDA_surplus_USD": surplus_n / E18 * oracle_px, "NVDA_needed_to_equal_shortage_at_oracle_raw": int(short / 1e6 / oracle_px * E18), "remainder_USD_surplus_minus_shortage": surplus_n / E18 * oracle_px - short / 1e6,
                      "remainder_NVDA_raw": surplus_n - int(short / 1e6 / oracle_px * E18)},
      "pool_spot": {"USDG_shortage_raw": short_spot, "NVDA_surplus_raw": surplus_spot, "NVDA_surplus_USD": surplus_spot / E18 * spot_px, "remainder_USD": surplus_spot / E18 * spot_px - short_spot / 1e6},
      "value_dependence": {"NVDA_backing_USD": total_backing_nvda_value, "USDG_backing_USD": total_backing_usdg_value, "NVDA_share_of_total_backing": total_backing_nvda_value / (total_backing_nvda_value + total_backing_usdg_value),
                           "USDG_claims_USD": U_["claimCurrent"] / 1e6, "share_of_USDG_claims_backed_by_USDG_or_USDG_debt": total_backing_usdg_value / (U_["claimCurrent"] / 1e6), "share_of_USDG_claims_relying_on_NVDA_value": short / 1e6 / (U_["claimCurrent"] / 1e6)}, "sensitivity": []}
for lbl, sc in S["scenarios"].items():
    st = words(R[f"scen.{lbl}"]); pxs = sc["priceWad"] / E18
    sn, su = L["stockIdle"] + st[2], L["usdgIdle"] + st[3]
    bm = usd(L["stockPrincipal"], L["usdgPrincipal"], pxs); gr = usd(sn, su, pxs); loss = max(0, bm - gr); f = gr / bm if gr < bm else 1
    tot_claim_usd = N_["claimCurrent"] / E18 * pxs + U_["claimCurrent"] / 1e6
    C_["sensitivity"].append({"scenario": lbl, "price": pxs, "LP_NVDA": st[2] / E18, "LP_USDG": st[3] / 1e6, "vault_NVDA": sn / E18, "vault_USDG": su / 1e6, "USDG_shortage": (L["usdgPrincipal"] - su) / 1e6, "NVDA_surplus": (sn - L["stockPrincipal"]) / E18, "NVDA_surplus_USD": (sn - L["stockPrincipal"]) / E18 * pxs,
                              "benchmark": bm, "gross": gr, "gross_minus_benchmark": gr - bm, "vault_would_recognise_loss_USD": loss, "pro_rata_factor": f, "stockPrincipal_after": L["stockPrincipal"] * f / E18, "usdgPrincipal_after": L["usdgPrincipal"] * f / 1e6, "USDG_shortage_after_loss": (L["usdgPrincipal"] * f - su) / 1e6, "NVDA_share_of_vault_value": sn / E18 * pxs / gr})

# break-even: price at which gross = benchmark for the current L/range, pool assumed at oracle (bisection, float position math)
def gm(px):
    s = (1e12 / px) ** 0.5; sa = (1.0001 ** (tl / 2)); sb = (1.0001 ** (tu / 2)); lq = pos_spot["liquidity"]
    s = min(max(s, sa), sb)
    u = lq * (1 / s - 1 / sb) / 1e6; n = lq * (s - sa) / E18
    return (L["stockIdle"] / E18 + n) * px + L["usdgIdle"] / 1e6 + u - (L["stockPrincipal"] / E18 * px + L["usdgPrincipal"] / 1e6)
lo_, hi_ = oracle_px * 0.5, oracle_px
for _ in range(80):
    mid = (lo_ + hi_) / 2
    if gm(mid) > 0:
        hi_ = mid
    else:
        lo_ = mid
C_["break_even_price_gross_equals_benchmark"] = {"price": hi_, "pct_from_oracle": (hi_ / oracle_px - 1) * 100, "model_check_gm_at_oracle": gm(oracle_px)}
# ---------- D ----------
ethusd = words(R["ethfeed"])[1] / 1e8; base = S["pin"]["baseFeePerGas"]
def gascost(g):
    return {"gas": g, "ETH": g * base / E18, "USD": g * base / E18 * ethusd}
def amt0(Lq, sp, spu):  # USDG in position at sqrt price
    return (Lq * Q96 * (spu - sp)) // (sp * spu)
def amt1(Lq, spl, sp):
    return (Lq * (sp - spl)) // Q96
def tick_sqrt(t):
    return int(1.0001 ** (t / 2) * Q96)
slow, sup = S["tickSqrt"]["tickLower"], S["tickSqrt"]["tickUpper"]
Lq = pos_spot["liquidity"]
maxu = words(R["tickstate.tickLower"])[3]
need = L["usdgPrincipal"] - L["usdgIdle"]
tick_price = lambda t: 1e12 / 1.0001 ** t
route_i = {"USDG_in_position_at_ref": pos_ref["usdg"], "USDG_max_in_position_at_tickLower": maxu, "tickLower": tl, "tickLower_price": tick_price(tl), "USDG_principal_minus_idle": need, "reachable_in_current_range": maxu >= need, "gap_at_tickLower_raw": need - maxu}
if maxu >= need:
    x_ = (Lq * Q96) // (need + (Lq * Q96) // sup)
    route_i["covering_sqrtX96"] = x_
    p_ = 1e12 / ((x_ / Q96) ** 2)
    route_i["covering_price_USDG_per_NVDA"] = p_
    route_i["pct_from_oracle"] = (p_ / oracle_px - 1) * 100
route_i["pct_tickLower_vs_oracle"] = (tick_price(tl) / oracle_px - 1) * 100
# model: price rises, keeper recenters each trigger, redeploying matched inventory (no fees, no execution loss). Labelled as a model, not chain simulation.
def path_model():
    pol = words(R["v.rangePolicy"])
    half, trig = pol[1], pol[2]
    sp0 = sqrtp; spacing = S["pool"]["tickSpacing"]
    idle_n, idle_u = L["stockIdle"], L["usdgIdle"]; lq = Lq; lo, up = tl, tu
    centre = sw(words(R["v.rangeState"])[0])
    t = math.floor(math.log(1e12 / oracle_px) / math.log(1.0001)); rows = []
    start_t = t
    steps = 0
    while steps < 4000:
        steps += 1
        t -= 10
        sp = tick_sqrt(t)
        sl_, su_ = tick_sqrt(lo), tick_sqrt(up)
        if sp <= sl_:
            usd_lp = amt0(lq, sl_, su_); n_lp = 0
        else:
            usd_lp = amt0(lq, sp, su_); n_lp = amt1(lq, sl_, sp)
        if idle_u + usd_lp >= L["usdgPrincipal"]:
            return {"covering_tick": t, "covering_price": tick_price(t), "pct_from_oracle": (tick_price(t) / oracle_px - 1) * 100, "recenters_assumed": len(rows), "recenters": rows}
        if abs(t - centre) >= trig:
            # recenter at t
            n = idle_n + n_lp; u = idle_u + usd_lp
            px = tick_price(t)
            matched = min(n / E18 * px, u / 1e6)
            nn = int(matched / px * E18); uu = int(matched * 1e6)
            floor = (t // spacing) * spacing
            lo, up = floor - half, floor + half + spacing
            sl_, su_ = tick_sqrt(lo), tick_sqrt(up)
            l0 = (uu * sp * su_) // (Q96 * (su_ - sp)); l1 = (nn * Q96) // (sp - sl_)
            lq = min(l0, l1)
            un = amt0(lq, sp, su_); nnn = amt1(lq, sl_, sp)
            idle_u = u - un; idle_n = n - nnn; centre = t
            rows.append({"tick": t, "price": px, "L": lq, "LP_USDG": un / 1e6, "LP_NVDA": nnn / E18, "idle_NVDA": idle_n / E18, "idle_USDG": idle_u / 1e6})
        t = t
        if t < start_t - 8000:
            break
    return {"not_reached": True, "recenters": rows}
route_i["model_with_recenters"] = path_model()

# swap
sw_step = D["route.swap.fullSideWithdraw"]["steps"][0]
sw_ev = next((e for e in sw_step.get("logs", []) if e["event"] == "SettlementSwap"), None)
cfg = words(R["v.pairConfig"])
fee_pips = S["pool"]["lpFee"]; pf = S["pool"]["protocolFee"]; pf_up = pf >> 12; pf_dn = pf & 0xfff
# NVDA(token1) in = oneForZero: protocol fee upper 12 bits
pf_used = pf_up
swap_fee_pips = pf_used + fee_pips - pf_used * fee_pips // 1_000_000
def local_quote(usdg_out):
    # exact-output quote walking initialised ticks upward
    sp = sqrtp; lq = S["pool"]["liquidity"]; remain = usdg_out; nin = 0
    tix = sorted(t for t in S["initialisedTicksNearby"] if t > cur)
    crossings = 0
    for t in tix:
        snext = None
        snext = S["tick_sqrt_cache"].get(str(t)) if "tick_sqrt_cache" in S else None
        snext = snext or tick_sqrt(t)
        avail = (lq * Q96 * (snext - sp)) // (sp * snext)
        if avail >= remain:
            # sp' from token0 out
            newsp = (lq * Q96 * sp) // (lq * Q96 - remain * sp)
            nin += (lq * (newsp - sp)) // Q96
            sp = newsp; remain = 0
            break
        nin += (lq * (snext - sp)) // Q96
        remain -= avail; sp = snext
        wn = int(R[f"tickinfo.{t}"], 16)
        net = wn >> 128
        net = net - (1 << 128) if net >= (1 << 127) else net
        lq += net; crossings += 1
    gross_in = nin * 1_000_000 // (1_000_000 - swap_fee_pips)
    return {"NVDA_in_raw": gross_in, "USDG_out_raw": usdg_out - remain, "post_sqrt": sp, "crossings": crossings, "avg_price": (usdg_out - remain) / 1e6 / (gross_in / E18), "impact_excl_fee_bps": ((sp / sqrtp) ** 2 - 1) * 1e4}
q = local_quote(short)
pre, post = {"fullNVDA": L["stockIdle"] + pos_ref["stock"]}, D["route.swap.fullSideWithdraw"]["post"]
swap = {"swapsPaused_at_pin": bool((int(S["slots"]["pairConfigFlagsWord"], 16) >> (8 * 25)) & 0xff), "maxSettlementSwapUSDG": cfg[5] / E18, "maxSwapSlippageBps": cfg[10], "lpFee_pips": fee_pips, "protocolFee_oneForZero_pips": pf_used, "effective_swap_fee_pips": swap_fee_pips,
        "local_quote_for_shortage": q, "local_quote_cost_USD_vs_oracle": q["NVDA_in_raw"] / E18 * oracle_px - q["USDG_out_raw"] / 1e6, "local_quote_cost_bps": (q["NVDA_in_raw"] / E18 * oracle_px / (q["USDG_out_raw"] / 1e6) - 1) * 1e4,
        "oracle_floor_price_with_slippage": oracle_px * (1 - cfg[10] / 1e4), "sim": {"status": sw_step["status"], "error": sw_step.get("error"), "gas": sw_step["gasUsed"], "swap_event": sw_ev["data"] if sw_ev else None, "post": post, "withdrawal": next((e["data"] for e in sw_step.get("logs", []) if e["event"] == "Withdrawal"), None), "reserveCovered": next((e["data"] for e in sw_step.get("logs", []) if e["event"] == "ReserveCovered"), None)},
        "gas": gascost(sw_step["gasUsed"])}
if sw_ev:
    ni, uo = sw_ev["data"]
    swap["sim_swap_cost_USD_vs_oracle"] = ni / E18 * oracle_px - uo / 1e6; swap["sim_swap_cost_bps"] = (ni / E18 * oracle_px / (uo / 1e6) - 1) * 1e4
    swap["sim_effective_price"] = (uo / 1e6) / (ni / E18)
    swap["NVDA_surplus_after_sim_raw"] = post["ledger"][2] - post["ledger"][0]
    swap["USDG_side_after"] = {"principal": post["ledger"][1], "idle": post["ledger"][3]}
    swap["principals_scaled"] = post["ledger"][4] != L["cumulativeLossUSDG"]
    swap["cumulativeLossUSDG_after"] = post["ledger"][4]
swap["bankrun"] = {"rows": D["route.swap.bankrun.USDGfirst"]["rows"], "final": D["route.swap.bankrun.USDGfirst"]["final"].get("post"), "USDG_out_raw": run_total(D["route.swap.bankrun.USDGfirst"], "pUSDG"), "NVDA_out_raw": run_total(D["route.swap.bankrun.USDGfirst"], "pNVDA")}

# reserve
rc = dict(zip(["stockToken", "usdg", "maxUsePerTxUSDG", "dailyCapUSDG", "maxCoverageBps", "paused", "exists"], words(R["r.config"])))
day = words(R["r.daily"])
trace = D["route.reserve.sequentialFullSide"]
remain_cov = short; rows = []
reserve = {"limits": {"maxUsePerTx_USD": rc["maxUsePerTxUSDG"] / E18, "dailyCap_USD": rc["dailyCapUSDG"] / E18, "maxCoverageBps": rc["maxCoverageBps"], "paused": rc["paused"], "dailyUsage": {"day": day[0], "used_USD": day[1] / E18, "pinDay": S["pin"]["timestamp"] // 86400}}, "balances": rs,
           "existing_available_USDG_raw": rs["USDG"]["available"], "sim_params": D["route.reserve.params"], "sim_firstWithdraw": {"steps": [{k: s_.get(k) for k in ("label", "status", "gasUsed")} | {"logs": [(e["event"], e["data"]) for e in s_.get("logs", [])]} for s_ in D["route.reserve.deposit+withdraw1"]["steps"]], "post": D["route.reserve.deposit+withdraw1"]["post"]},
           "sim_sequentialFullSide": trace, "bankrun": {"rows": D["route.reserve.bankrun.USDGfirst"]["rows"], "final": D["route.reserve.bankrun.USDGfirst"]["final"].get("post"), "USDG_out_raw": run_total(D["route.reserve.bankrun.USDGfirst"], "pUSDG"), "NVDA_out_raw": run_total(D["route.reserve.bankrun.USDGfirst"], "pNVDA")}}
n, x_ = 0, short
series = []
while x_ > 0 and n < 40:
    cv = max(1, min(x_ // 2, int(rc["maxUsePerTxUSDG"] / 10**12)))
    series.append(cv); x_ -= cv; n += 1
reserve["analytic_geometric"] = {"first_six_covers_raw": series[:6], "covers_until_zero": n, "covers_until_below_1cent": next(i + 1 for i in range(len(series)) if short - sum(series[:i + 1]) < 10000), "total_paid_raw": sum(series), "days_needed": 1 if sum(series) / 1e6 <= rc["dailyCapUSDG"] / E18 - (day[1] / E18 if day[0] == S["pin"]["timestamp"] // 86400 else 0) else None}

# recenter / rebalance
rec = D["vault.recenter.forcedByOverride"]; re0 = D["vault.recenter.asIs"]; reb = D["vault.rebalance"]
rc_step = rec["steps"][0]
recenter = {"asIs": {"status": re0["steps"][0]["status"], "revert": (re0["steps"][0].get("error") or {}).get("data")}, "rebalance_after_checkpoint": {"status": reb["steps"][-1]["status"], "revert": (reb["steps"][-1].get("error") or {}).get("data")},
            "forced_by_centre_override": {"status": rc_step["status"], "revert": (rc_step.get("error") or {}).get("data"), "gas": rc_step["gasUsed"], "events": [(e["event"], e["data"]) for e in rc_step.get("logs", [])], "post_ledger": rec["post"]["ledger"], "post_position": rec["post"]["position"], "post_ticks": rec["post"]["ticks"], "gasCost": gascost(rc_step["gasUsed"])}}
if rc_step["status"] == 1:
    p_ = rec["post"]
    pe = [e for e in rc_step["logs"] if e["event"] == "PairCheckpoint"]
    recenter["forced_by_centre_override"]["USDG_shortage_before_raw_at_event"] = pe[0]["data"][5] - pe[0]["data"][1]
    recenter["forced_by_centre_override"]["USDG_shortage_after_raw_at_event"] = pe[-1]["data"][5] - pe[-1]["data"][1]
    recenter["forced_by_centre_override"]["stockAssets_before_after"] = (pe[0]["data"][0], pe[-1]["data"][0])
    recenter["forced_by_centre_override"]["usdgAssets_before_after"] = (pe[0]["data"][1], pe[-1]["data"][1])
    v0 = pe[0]["data"][0] / E18 * oracle_px + pe[0]["data"][1] / 1e6; v1 = pe[-1]["data"][0] / E18 * oracle_px + pe[-1]["data"][1] / 1e6
    recenter["forced_by_centre_override"]["value_change_USD_at_oracle"] = v1 - v0

em = D["route.emergency.full"]
emergency = {"full": {"status": em["steps"][0]["status"], "gas": em["steps"][0]["gasUsed"], "events": [(e["event"], e["data"]) for e in em["steps"][0].get("logs", [])], "post": em["post"], "gasCost": gascost(em["steps"][0]["gasUsed"])},
             "partial50": {"status": D["route.emergency.partial50"]["steps"][0]["status"], "post_ledger": D["route.emergency.partial50"]["post"]["ledger"], "post_position": D["route.emergency.partial50"]["post"]["position"]},
             "bankrun": {"rows": D["route.emergency.bankrun"]["rows"], "final": D["route.emergency.bankrun"]["final"].get("post")}}
def syncrow(k):
    v = D.get(k)
    if not v or "post" not in v:
        return v
    st_ = v["steps"]
    return {"status": [s_["status"] for s_ in st_], "gas": sum(s_["gasUsed"] for s_ in st_ if s_["label"].startswith("sync")), "covers": [e["data"] for s_ in st_ for e in s_.get("logs", []) if e["event"] == "ReserveCovered"], "vaultWithdrawals": [e["data"] for s_ in st_ for e in s_.get("logs", []) if e["event"] == "VaultWithdrawal"], "checkpoints": [e["data"] for s_ in st_ for e in s_.get("logs", []) if e["event"] == "PairCheckpoint"], "reverts": [(s_["label"], (s_.get("error") or {}).get("data")) for s_ in st_ if s_["status"] != 1], "post": v["post"]}
reserve["sync_path"] = {k: syncrow(k) for k in D if k.startswith("route.reserve.sync") }
swap["sync_path"] = syncrow("route.swap.syncVault"); reserve["sync_noFunding"] = syncrow("route.sync.noFunding")
inkind = {"USDG_side_cash_USDG": L["usdgIdle"] + pos_ref["usdg"], "USDG_side_inKind_NVDA_raw_at_oracle": int(short / 1e6 / oracle_px * E18), "NVDA_left_over_raw": surplus_n - int(short / 1e6 / oracle_px * E18), "NVDA_left_over_USD": (surplus_n - int(short / 1e6 / oracle_px * E18)) / E18 * oracle_px, "per_USDG_of_vault_claim": {"USDG": (L["usdgIdle"] + pos_ref["usdg"]) / L["usdgPrincipal"], "USD_of_NVDA": short / L["usdgPrincipal"]}, "exists_on_chain": False}
routes = {"i_wait": route_i, "ii_swap": swap, "iii_reserve": reserve, "iv_recenter": recenter, "v_emergency": emergency, "vi_inkind": inkind, "ethUsd": ethusd, "baseFeeWei": base, "roles": {k: bool(int(v, 16)) for k, v in R.items() if k.startswith("v.hasRole")}}

# ---------- E ----------
holders = {}
for mn, tkn in (("pNVDA", 1), ("pUSDG", 2)):
    rows, tot = [], 0
    for h in S["holderList"][mn]:
        bal = w(f"hb.{mn}.{h}")
        fromlog = S["holders_fromLogs"][mn].get(h, 0)
        if bal == 0 and fromlog == 0:
            continue
        tot += bal
        claim = bal * M[mn]["exchangeRateCurrent"] // E18
        rows.append({"holder": h, "shares": bal, "sharesFromLogsEqual": bal == fromlog, "claim_raw": claim, "claim_tokens": claim / (E18 if mn == "pNVDA" else 1e6), "claim_USD_oracle": claim / E18 * oracle_px if mn == "pNVDA" else claim / 1e6, "code_bytes": ((len(R[f"code.{h}"]) - 2) // 2) if f"code.{h}" in R else None, "nonce": int(R[f"nonce.{h}"], 16) if f"nonce.{h}" in R else None, "debt_NVDA_current": w(f"d.pNVDA.current.{h}") if f"d.pNVDA.current.{h}" in R and ok(f"d.pNVDA.current.{h}") else None, "debt_USDG_current": w(f"d.pUSDG.current.{h}") if f"d.pUSDG.current.{h}" in R and ok(f"d.pUSDG.current.{h}") else None, "singleHolderMax": D["redeem.singleHolderMax"].get(f"{mn}.{h}")})
    holders[mn] = {"rows": rows, "sumShares": tot, "totalSupply": M[mn]["totalSupply"], "sumEqualsSupply": tot == M[mn]["totalSupply"]}
accts = {}
for a in S["accountList"]:
    rec_ = None
    if ok(f"re.accounts.{a}"):
        ww = words(R[f"re.accounts.{a}"])
        rec_ = {"positionId": ww[0], "owner": "0x" + hex(ww[1])[2:].rjust(40, "0"), "side": ww[5], "status": ww[6]}
    m_ = None
    if ok(f"re.metrics.{a}"):
        ww = words(R[f"re.metrics.{a}"])
        m_ = {"gross": ww[0] / E18, "debt": ww[1] / E18, "equity": sw(ww[2]) / E18, "healthBps": ww[5], "leverageX100": ww[6]}
    liq = words(R[f"liq.{a}"]) if ok(f"liq.{a}") else None
    accts[a] = {"debt_NVDA_stored": w(f"d.pNVDA.stored.{a}"), "debt_NVDA_current": w(f"d.pNVDA.current.{a}"), "debt_USDG_stored": w(f"d.pUSDG.stored.{a}"), "debt_USDG_current": w(f"d.pUSDG.current.{a}"), "controller_liquidity": liq, "riskEngine": rec_ if rec_ and rec_["positionId"] else None, "metrics": m_, "mv_free": w(f"mv.free.{a}"), "mv_locked": w(f"mv.locked.{a}"), "code_bytes": (len(R[f"code.{a}"]) - 2) // 2, "nonce": int(R[f"nonce.{a}"], 16)}
debt_sum = {"NVDA_current": sum(v["debt_NVDA_current"] for v in accts.values()), "USDG_current": sum(v["debt_USDG_current"] for v in accts.values()), "NVDA_stored": sum(v["debt_NVDA_stored"] for v in accts.values()), "USDG_stored": sum(v["debt_USDG_stored"] for v in accts.values()),
            "vs_totalBorrowsCurrent": {"NVDA_residual": N_["totalBorrowsCurrent"] - sum(v["debt_NVDA_current"] for v in accts.values()), "USDG_residual": U_["totalBorrowsCurrent"] - sum(v["debt_USDG_current"] for v in accts.values())}}
mv = {"totalFree_pUSDG": w("mv.totalFree"), "totalLocked_pUSDG": w("mv.totalLocked"), "balance_pUSDG": w("hb.pUSDG." + "0x04d4a5555b7a37017a67b4d21a1da5838de28b9e"), "users": {u: {"free": w(f"mv.free.{u}"), "locked": w(f"mv.locked.{u}")} for u in S["marginVaultLogs"]["users"]}, "locks": {str(i): words(R[f"mv.lock.{i}"]) for i in range(1, 8) if ok(f"mv.lock.{i}")}}
Eout = {"holders": holders, "accounts": accts, "debt_sum": debt_sum, "marginVault": mv, "evidenceFile": "holder-evidence.json" if EV else None}

OUT = {"pin": S["pin"], "prices": {"oracle_USD": oracle_px, "pool_spot_USDG_per_NVDA": spot_px, "usdg_USD": usdg_px, "spot_vs_oracle_bps": (spot_px / oracle_px - 1) * 1e4, "referenceSqrtPriceX96": ref, "pool": S["pool"], "feed": {"answer": words(R["feed.latest"])[1], "updatedAt": words(R["feed.latest"])[3], "ageSec": S["pin"]["timestamp"] - words(R["feed.latest"])[3]}, "lending_oracle_pNVDA": w("lo.pNVDA"), "lending_oracle_pUSDG": w("lo.pUSDG"), "guard": {"validatePool_ok": ok("g.validatePool"), "validateRemoval_ok": ok("g.validateRemoval"), "maxDev": w("g.maxDev"), "maxRemDev": w("g.maxRemDev")}},
       "position": {"tokenId": pos_spot["tokenId"], "isExpected": pos_spot["tokenId"] == 4148743, "ownerOf": "0x" + R["pm.ownerOf"][-40:], "pm_liquidity": w("pm.liq"), "adapter_liquidity": pos_spot["liquidity"], "storage_liquidity": pl, "tickLower": tl, "tickUpper": tu, "ranged": bool(ticks[2]), "amountsSpot": pos_spot, "amountsRef": pos_ref, "price_range": [tick_price(tu), tick_price(tl)], "inRange": tl <= cur < tu},
       "ledger": L, "vault_balance_vs_ledger": vault_diff, "markets": M, "fees": fees, "balance_sheet": {"assets": assets, "claims": claims, "eliminated_principal_memo": elim, "loss_reserve_contingent": reserve_lines, "stray_or_out_of_perimeter_balances_raw": stray}, "reconciliation": recon, "B": B, "C": C_, "D": routes, "E": Eout}
json.dump(OUT, open(HERE / "balance-sheet.json", "w"), indent=1)
print("written", S["pin"]["l2"])
