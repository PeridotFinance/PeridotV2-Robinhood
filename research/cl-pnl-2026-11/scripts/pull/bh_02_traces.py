"""Receipt-level evidence for the Blackhole fee and reward paths -> data/blackhole/traces.json.

For one real transaction of each kind the script fetches the receipt (eth_getTransactionReceipt, publicnode) and
decodes every ERC-20 Transfer and the protocol events in it:
  1 swap            sign convention of the Swap event against the token transfers in and out of the pool
  2 fee transfer    a pool -> community vault transfer (the community fee leaving the pool)
  3 fee distribute  the weekly GaugeManager.distributeFees: vault.withdraw -> (Algebra fee receiver, gauge) -> internal bribe
  4 reward notify   the weekly GaugeManager.distribute: BLACK GaugeManager -> gauge -> AlgebraEternalFarming, new rate
  5 reward claim    GaugeManager.claimRewards -> GaugeCL.getReward: which token the staker receives
  6 stake           GaugeCL.deposit(tokenId): the NFT's owner before and after, tokenFarmedIn after
Also samples the gauge's Deposit / Withdraw / Harvest events of the last 50,000 blocks before END (they are not pulled
in full). Read-only.   usage: python3 -u bh_02_traces.py
"""
from __future__ import annotations

from collections import Counter

import bh_lib as B

A = B.A
T = B.topic
D = B.DOCS
XFER = T(B.TRANSFER)
st = A.read_json(B.DATA_BH / "state_at_end.json")
EF = st["C_gauge"]["algebraEternalFarming()"]
FC = st["C_gauge"]["farmingCenter()"]
NFPM = st["C_gauge"]["nonfungiblePositionManager()"]
VP = st["C_farming"]["incentive"]["virtualPoolAddress"]
LABEL = {B.POOL.lower(): "pool", B.POOL_VAULT.lower(): "communityVault", B.GAUGE.lower(): "gauge",
         st["C_gauge"]["internal_bribe()"].lower(): "internalBribe", st["C_gauge"]["external_bribe()"].lower(): "externalBribe",
         D["GaugeManager"].lower(): "GaugeManager", EF.lower(): "AlgebraEternalFarming", FC.lower(): "FarmingCenter",
         NFPM.lower(): "NFPM", VP.lower(): "virtualPool", st["B_community_vault"]["END"]["algebraFeeReceiver"].lower(): "algebraFeeReceiver",
         D["Minter"].lower(): "Minter", D["swapRouter"].lower(): "swapRouter", D["RouterV2"].lower(): "RouterV2",
         "0x0000000000000000000000000000000000000000": "0x0"}
TOKEN = {B.WAVAX.lower(): "WAVAX", B.USDC.lower(): "USDC", B.BLACK.lower(): "BLACK"}
EVN = {T(s): s for s in B.EV_POOL + [
    "RewardAdded(uint256)", "Deposit(address,uint256)", "Withdraw(address,uint256)", "Harvest(address,uint256)",
    "ClaimFees(address,uint256,uint256)", "TokensWithdrawal(address,address,uint256)", "AlgebraTokensWithdrawal(address,address,uint256)",
    "RewardAdded(address,uint256,uint256)", "DistributeReward(address,address,uint256)", "RewardsAdded(uint256,uint256,bytes32)",
    "RewardsRatesChanged(uint128,uint128,bytes32)", "RewardsCollected(uint256,bytes32,uint256,uint256)",
    "RewardClaimed(address,uint256,address,address)", "FarmEntered(uint256,bytes32,uint128)",
    "FarmEnded(uint256,bytes32,address,address,address,uint256,uint256)", "Approval(address,address,uint256)"]}


def lab(a: str) -> str:
    return LABEL.get(a.lower(), B.checksum(a))


def receipt(tx: str) -> dict:
    f = B.CACHE_BH / "receipts" / f"{tx}.json"
    f.parent.mkdir(exist_ok=True)
    if f.exists():
        return A.read_json(f)
    r = A.rpc("publicnode", "eth_getTransactionReceipt", [tx])
    if r is None:                      # publicnode serves receipts of recent blocks only; the API node has them all
        r = A.rpc("avax", "eth_getTransactionReceipt", [tx])
    assert r is not None, f"no receipt for {tx}"
    A.write_json(f, r)
    return r


def decode(tx: str) -> dict:
    r = receipt(tx)
    out = {"tx": tx, "block": int(r["blockNumber"], 16), "status": int(r["status"], 16), "from": B.checksum(r["from"]),
           "to": lab(r["to"]) if r.get("to") else None, "transfers": [], "events": []}
    for lg in r["logs"]:
        t0 = lg["topics"][0] if lg["topics"] else None
        ad = lg["address"].lower()
        if t0 == XFER and len(lg["topics"]) == 3:
            out["transfers"].append({"log_index": int(lg["logIndex"], 16), "token": TOKEN.get(ad, B.checksum(ad)),
                                     "from": lab("0x" + lg["topics"][1][-40:]), "to": lab("0x" + lg["topics"][2][-40:]),
                                     "amount": str(int(lg["data"], 16))})
        elif t0 == XFER and len(lg["topics"]) == 4:
            out["events"].append({"log_index": int(lg["logIndex"], 16), "emitter": lab(ad), "event": "ERC721 Transfer",
                                  "from": lab("0x" + lg["topics"][1][-40:]), "to": lab("0x" + lg["topics"][2][-40:]), "tokenId": int(lg["topics"][3], 16)})
        elif t0 in EVN and (ad in LABEL):
            out["events"].append({"log_index": int(lg["logIndex"], 16), "emitter": lab(ad), "event": EVN[t0],
                                  "topics": lg["topics"][1:], "data_words": [str(x) for x in A.words(lg["data"])] if lg["data"] != "0x" else []})
    return out


def main() -> None:
    out: dict = {"host": "avalanche-c-chain-rpc.publicnode.com (receipts, logs), api.avax.network (eth_call)"}

    # ---- 1 swap: sign convention
    led = A.ledger_rows("bh_pool")
    last = A.read_json_gz(A.CACHE / "logs" / "bh_pool" / f"{led[-1]['from']}-{led[-1]['to']}.json.gz")
    SWAP = T("Swap(address,address,int256,int256,uint160,uint128,int24)")
    sw = [x for x in last if x["t"][0] == SWAP]
    checks = []
    seen_dir = set()
    for x in reversed(sw):
        w = A.words(x["d"])
        a0, a1 = A.s256(w[0]), A.s256(w[1])
        d = "token0_in" if a0 > 0 else "token1_in"
        if d in seen_dir:
            continue
        tr = decode(x["tx"])
        n_swaps_in_tx = sum(1 for e in tr["events"] if e["event"].startswith("Swap(") and e["emitter"] == "pool")
        if n_swaps_in_tx != 1:
            continue
        net = Counter()
        for t in tr["transfers"]:
            if t["token"] in ("WAVAX", "USDC"):
                if t["to"] == "pool":
                    net[t["token"]] += int(t["amount"])
                if t["from"] == "pool" and t["to"] != "communityVault":
                    net[t["token"]] -= int(t["amount"])
        checks.append({"tx": x["tx"], "block": x["b"], "log_index": x["i"], "event_amount0": str(a0), "event_amount1": str(a1),
                       "pool_net_WAVAX_from_transfers (in - out, excluding the fee transfer to the vault)": str(net["WAVAX"]),
                       "pool_net_USDC_from_transfers": str(net["USDC"]),
                       "signs_and_amounts_match": net["WAVAX"] == a0 and net["USDC"] == a1, "transfers": tr["transfers"]})
        seen_dir.add(d)
        if len(seen_dir) == 2:
            break
    out["1_swap_sign_convention"] = {"statement": "Swap.amount0 / amount1 are the pool's own balance changes: positive = the pool received that token, negative = the pool paid it out",
                                     "checks": checks}

    # ---- 2 a pool -> vault transfer near END
    res = A.rpc("publicnode", "eth_getLogs", [{"address": B.WAVAX, "fromBlock": hex(B.END - 20000), "toBlock": hex(B.END),
                                               "topics": [XFER, "0x" + B.a(B.POOL), "0x" + B.a(B.POOL_VAULT)]}])
    res2 = A.rpc("publicnode", "eth_getLogs", [{"address": B.USDC, "fromBlock": hex(B.END - 20000), "toBlock": hex(B.END),
                                                "topics": [XFER, "0x" + B.a(B.POOL), "0x" + B.a(B.POOL_VAULT)]}])
    out["2_pool_to_vault_transfers_last_20000_blocks"] = {
        "WAVAX": {"transfers": len(res), "sum": str(sum(int(x["data"], 16) for x in res))},
        "USDC": {"transfers": len(res2), "sum": str(sum(int(x["data"], 16) for x in res2))},
        "sample": decode(res[-1]["transactionHash"]) if res else None}

    # ---- 3 fee distribution (last ClaimFees in the window)
    try:
        gl = []
        for r in A.ledger_rows("bh_gauge_rf"):
            gl.extend(A.read_json_gz(A.CACHE / "logs" / "bh_gauge_rf" / f"{r['from']}-{r['to']}.json.gz"))
    except FileNotFoundError:
        gl = []
    cf = [x for x in gl if x["t"][0] == T("ClaimFees(address,uint256,uint256)")]
    if cf:
        tr = decode(cf[-1]["tx"])
        mine = {"tx": tr["tx"], "block": tr["block"], "from": tr["from"], "to": tr["to"],
                "transfers": [t for t in tr["transfers"] if {t["from"], t["to"]} & {"communityVault", "gauge", "internalBribe", "algebraFeeReceiver"} and t["token"] in ("WAVAX", "USDC")
                              and ("communityVault" in (t["from"], t["to"]) and (t["to"] in ("gauge", "algebraFeeReceiver")) or (t["from"] == "gauge" and t["to"] == "internalBribe"))],
                "events": [e for e in tr["events"] if e["emitter"] in ("communityVault", "gauge", "internalBribe")]}
        # split per token: only the transfers that belong to THIS pool's vault / gauge
        split = {}
        for tok in ("WAVAX", "USDC"):
            v2a = sum(int(t["amount"]) for t in mine["transfers"] if t["token"] == tok and t["from"] == "communityVault" and t["to"] == "algebraFeeReceiver")
            v2g = sum(int(t["amount"]) for t in mine["transfers"] if t["token"] == tok and t["from"] == "communityVault" and t["to"] == "gauge")
            g2b = sum(int(t["amount"]) for t in mine["transfers"] if t["token"] == tok and t["from"] == "gauge" and t["to"] == "internalBribe")
            tot = v2a + v2g
            split[tok] = {"vault_to_algebraFeeReceiver": str(v2a), "vault_to_gauge": str(v2g), "gauge_to_internalBribe": str(g2b),
                          "algebra_share_of_withdrawal": v2a / tot if tot else None, "gauge_share_of_withdrawal": v2g / tot if tot else None,
                          "bribe_share_of_gauge_receipt": g2b / v2g if v2g else None}
        mine["split"] = split
        out["3_fee_distribution"] = mine

    # ---- 4 reward notification (last DistributeReward of this gauge)
    dl = []
    for r in A.ledger_rows("bh_gm_distribute"):
        dl.extend(A.read_json_gz(A.CACHE / "logs" / "bh_gm_distribute" / f"{r['from']}-{r['to']}.json.gz"))
    tr = decode(dl[-1]["tx"])
    inc = st["C_farming"]["incentiveId"][2:]
    out["4_reward_notification"] = {
        "tx": tr["tx"], "block": tr["block"], "from": tr["from"], "to": tr["to"],
        "BLACK_transfers_touching_this_gauge": [t for t in tr["transfers"] if t["token"] == "BLACK" and "gauge" in (t["from"], t["to"])],
        "events_of_this_gauge_and_incentive": [e for e in tr["events"] if e["emitter"] == "gauge" or (e["emitter"] == "AlgebraEternalFarming" and e["data_words"] and hex(int(e["data_words"][-1]))[2:].rjust(64, "0") == inc)
                                               or (e["emitter"] == "GaugeManager" and len(e["topics"]) > 1 and e["topics"][1][-40:] == B.GAUGE.lower()[2:])]}

    # ---- 5 / 6 gauge user events near END
    names = {T("Deposit(address,uint256)"): "Deposit", T("Withdraw(address,uint256)"): "Withdraw", T("Harvest(address,uint256)"): "Harvest"}
    res = A.rpc("publicnode", "eth_getLogs", [{"address": B.GAUGE, "fromBlock": hex(B.END - 49999), "toBlock": hex(B.END)}], timeout=120)
    cnt = Counter(names.get(x["topics"][0], x["topics"][0]) for x in res)
    out["gauge_events_last_50000_blocks"] = dict(cnt)
    hv = [x for x in res if names.get(x["topics"][0]) == "Harvest" and int(x["data"], 16) > 0]
    if hv:
        x = hv[-1]
        tr = decode(x["transactionHash"])
        owner = "0x" + x["topics"][1][-40:]
        paid = [t for t in tr["transfers"] if t["to"].lower() == B.checksum(owner).lower()]
        out["5_reward_claim"] = {"tx": tr["tx"], "block": tr["block"], "tx_to": tr["to"], "harvest_owner": B.checksum(owner),
                                 "harvest_amount": str(int(x["data"], 16)),
                                 "all_token_transfers_to_the_owner_in_this_tx": paid,
                                 "tokens_received": sorted({t["token"] for t in paid}),
                                 "events": [e for e in tr["events"] if e["emitter"] in ("gauge", "AlgebraEternalFarming")][:12]}
    # Harvest (GaugeManager.claimRewards -> GaugeCL.getReward) is rare: none in the last 50,000 blocks nor in three other
    # 50,000-block probes of the window; rewards are normally taken straight from the farming contract, which emits
    # RewardClaimed(address indexed to, uint256 reward, address indexed rewardAddress, address indexed owner).
    probes = {}
    for lo in (B.END - 49999, B.END - 1000000, B.END - 3000000, B.START):
        hr = A.rpc("publicnode", "eth_getLogs", [{"address": B.GAUGE, "fromBlock": hex(lo), "toBlock": hex(lo + 49999), "topics": [T("Harvest(address,uint256)")]}], timeout=120)
        probes[f"{lo}-{lo + 49999}"] = len(hr)
    out["5_harvest_event_probes (gauge, 50,000-block ranges)"] = probes
    RC = T("RewardClaimed(address,uint256,address,address)")
    rc = A.rpc("publicnode", "eth_getLogs", [{"address": EF, "fromBlock": hex(B.END - 49999), "toBlock": hex(B.END), "topics": [RC]}], timeout=120)
    by_token = Counter(TOKEN.get("0x" + x["topics"][2][-40:], B.checksum("0x" + x["topics"][2][-40:])) for x in rc)
    stakers = {B.checksum("0x" + x["topics"][1][-40:]).lower() for x in res if names.get(x["topics"][0]) == "Deposit"}
    mine = [x for x in rc if "0x" + x["topics"][3][-40:] in stakers and "0x" + x["topics"][2][-40:] == B.BLACK.lower()]
    out["5_reward_claims_on_farming_contract_last_50000_blocks"] = {
        "RewardClaimed_events_by_reward_token (all incentives of this farming contract)": dict(by_token),
        "of_which_owner_is_a_depositor_of_this_gauge_in_the_same_range": len(mine)}
    if mine:
        x = mine[-1]
        tr = decode(x["transactionHash"])
        to = B.checksum("0x" + x["topics"][1][-40:])
        out["5_reward_claim"] = {"tx": tr["tx"], "block": tr["block"], "event": "AlgebraEternalFarming.RewardClaimed",
                                 "owner (position owner, a depositor of this gauge)": B.checksum("0x" + x["topics"][3][-40:]), "to": to,
                                 "rewardAddress": TOKEN.get("0x" + x["topics"][2][-40:]), "reward": str(int(x["data"], 16)),
                                 "token_transfers_out_of_the_farming_contract_in_this_tx": [t for t in tr["transfers"] if t["from"] == "AlgebraEternalFarming"],
                                 "note": "the farming contract's rewards[owner][token] ledger is not per pool: this proves the token paid, not the pool it was earned on"}
    # the one Harvest found by scanning every gauge log of blocks 65,228,165..67,828,164 (path through the gauge)
    htx = "0x40b4bd0fef2970938660a8ba89db3f7c008f3abfa12638325aa53c02f355bb27"
    tr = decode(htx)
    hev = [e for e in tr["events"] if e["emitter"] == "gauge" and e["event"].startswith("Harvest")]
    out["5_reward_claim_via_gauge (historic sample)"] = {
        "tx": htx, "block": tr["block"], "tx_to": tr["to"], "harvest_events": hev,
        "farming_contract_events_in_this_tx": [e["event"].split("(")[0] for e in tr["events"] if e["emitter"] == "AlgebraEternalFarming"],
        "token_transfers_out_of_the_farming_contract_in_this_tx": [t for t in tr["transfers"] if t["from"] == "AlgebraEternalFarming"]}
    # who holds claimable balances: the ledger is rewards[owner][token]; the gauge itself has none
    out["5_reward_ledger"] = {
        "AlgebraEternalFarming.rewards(gauge, BLACK) at END": str(B.W(B.call(EF, "rewards(address,address)", B.a(B.GAUGE) + B.a(B.BLACK)))[0]),
        "AlgebraEternalFarming.rewards(GaugeManager, BLACK) at END": str(B.W(B.call(EF, "rewards(address,address)", B.a(D["GaugeManager"]) + B.a(B.BLACK)))[0]),
        "source_reading": "collectRewards / exitFarming credit rewards[NFPM.ownerOf(tokenId)]; AlgebraEternalFarming.claimReward(token, to, amount) pays out of rewards[msg.sender]; FarmingCenter.claimReward pays out of rewards[its caller]. "
                          "GaugeCL.getReward calls FarmingCenter.collectRewards (books to the owner) and then FarmingCenter.claimReward, which reads rewards[gauge]"}
    dp = [x for x in res if names.get(x["topics"][0]) == "Deposit"]
    if dp:
        x = dp[-1]
        blk = int(x["blockNumber"], 16)
        tid = int(x["data"], 16)
        user = B.checksum("0x" + x["topics"][1][-40:])
        tr = decode(x["transactionHash"])

        def own(b):
            w = B.try_call(NFPM, "ownerOf(uint256)", B.u(tid), b)
            return None if w is None else B.addr_of(B.W(w)[0])

        def rdaddr(sig, b):
            w = B.try_call(NFPM, sig, B.u(tid), b)
            return None if w is None else lab(B.addr_of(B.W(w)[0]))
        out["6_stake"] = {"tx": tr["tx"], "block": blk, "tx_to": tr["to"], "deposit_user": user, "tokenId": tid,
                          "NFPM.ownerOf(tokenId) at block-1": own(blk - 1), "NFPM.ownerOf(tokenId) at block": own(blk), "NFPM.ownerOf(tokenId) at END": own(B.END),
                          "NFPM.tokenFarmedIn(tokenId) at block-1": rdaddr("tokenFarmedIn(uint256)", blk - 1),
                          "NFPM.tokenFarmedIn(tokenId) at block": rdaddr("tokenFarmedIn(uint256)", blk),
                          "NFPM.farmingApprovals(tokenId) at block": rdaddr("farmingApprovals(uint256)", blk),
                          "NFPM.getApproved(tokenId) at block": rdaddr("getApproved(uint256)", blk),
                          "NFPM.isApprovedForAll(owner, gauge) at block-1": bool(B.W(B.call(NFPM, "isApprovedForAll(address,address)", B.a(user) + B.a(B.GAUGE), blk - 1))[0]),
                          "ERC721_transfers_in_tx": [e for e in tr["events"] if e["event"] == "ERC721 Transfer"],
                          "farming_events": [e for e in tr["events"] if e["emitter"] in ("gauge", "AlgebraEternalFarming")]}
        fe = [e for e in out["6_stake"]["farming_events"] if e["event"].startswith("FarmEntered")]
        if fe:
            liq_pos = int(fe[0]["data_words"][0])
            liq_pool = B.W(B.call(B.POOL, "liquidity()", "", blk))[0]
            out["6_stake"]["position_liquidity (FarmEntered)"] = str(liq_pos)
            out["6_stake"]["pool.liquidity() at the same block"] = str(liq_pool)
            out["6_stake"]["position_share_of_active_liquidity_at_that_block"] = liq_pos / liq_pool if liq_pool else None
            out["6_stake"]["depositor_code_bytes"] = (len(A.rpc("avax", "eth_getCode", [user, hex(B.END)])) - 2) // 2
            dcount = Counter(B.checksum("0x" + x["topics"][1][-40:]) for x in dp)
            out["6_stake"]["Deposit_events_last_50000_blocks_by_depositor"] = dict(dcount.most_common(5))
        w = B.W(B.call(NFPM, "balanceOf(address)", B.a(B.GAUGE)))
        out["6_stake"]["NFPM.balanceOf(gauge) at END"] = w[0]
        out["6_stake"]["NFPM.balanceOf(FarmingCenter) at END"] = B.W(B.call(NFPM, "balanceOf(address)", B.a(FC)))[0]
        out["6_stake"]["NFPM.balanceOf(AlgebraEternalFarming) at END"] = B.W(B.call(NFPM, "balanceOf(address)", B.a(EF)))[0]
    A.write_json(B.DATA_BH / "traces.json", out)
    for k, v in out.items():
        print(k, str(v)[:1800])
        print()


if __name__ == "__main__":
    main()
