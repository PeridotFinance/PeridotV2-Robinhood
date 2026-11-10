"""Fetch and cache raw logs for the Blackhole pull (read-only eth_getLogs on publicnode; resumable).

usage: python3 -u bh_01_fetch_logs.py <dataset> [chunk]
Datasets (cache directory data/.cache/avalanche/logs/<dataset>/, with a ledger of every requested range):
  bh_pool        dataset pool, every event,            START..END
  bh_pool_pre, bh_pool_pre_1..6   dataset pool, Mint/Burn/Initialize/Fee/CommunityFee/TickSpacing/Plugin/PluginConfig/
                 CommunityVault, pool creation..START-1 in consecutive segments (bh_lib.PRE_SEGMENTS) so that several
                 processes can run at once
  bh_pool_second dataset pool, every event, START..END on a different grid (re-request check; only the ranges asked
                 for by bh_04_checks.py are pulled there, not here)
  bh_gauge_rf    GaugeCL of the pool, RewardAdded + ClaimFees, pool creation..END (Deposit / Withdraw / Harvest are
                 not pulled in full: about 150 logs per 1,000 blocks; bh_02_traces.py samples them)
  bh_vault       AlgebraCommunityVault of the pool, every event, pool creation..END
  bh_gm_distribute  GaugeManager DistributeReward(sender, gauge = this gauge, amount), pool creation..END
  bh_black_pair  vAMM-USDC/BLACK pair (the deepest BLACK pool quoted in USDC or WAVAX), every event, START..END
  bh_farming     AlgebraEternalFarming (v1) RewardsAdded / RewardsRatesChanged, pool creation..END (all incentives; the
                 build step keeps this pool's incentiveId)
The window grid starts at the dataset's first block; the source is always publicnode (the tenderly gateway is not used).
"""
import sys

import bh_lib as B

A = B.A
T = B.topic

SETS = {
    "bh_pool": dict(address=B.POOL, start=B.START, end=B.END, chunk=25000, topics=None),
    "bh_gauge_rf": dict(address=B.GAUGE, start=B.POOL_CREATED, end=B.END, chunk=50000,
                        topics=[[T("RewardAdded(uint256)"), T("ClaimFees(address,uint256,uint256)")]]),
    "bh_vault": dict(address=B.POOL_VAULT, start=B.POOL_CREATED, end=B.END, chunk=50000, topics=None),
    "bh_gm_distribute": dict(address=B.DOCS["GaugeManager"], start=B.POOL_CREATED, end=B.END, chunk=50000,
                             topics=[T("DistributeReward(address,address,uint256)"), None,
                                     "0x" + B.a(B.GAUGE)]),
    "bh_black_pair": dict(address=B.BLACK_PAIR, start=B.START, end=B.END, chunk=50000, topics=None),
    "bh_farming": dict(address=B.DOCS["eternal_v1"], start=B.POOL_CREATED, end=B.END, chunk=50000,
                       topics=[[T("RewardsAdded(uint256,uint256,bytes32)"),
                                T("RewardsRatesChanged(uint128,uint128,bytes32)")]]),
}


for _name, _a, _b in B.PRE_SEGMENTS:
    SETS[_name] = dict(address=B.POOL, start=_a, end=_b, chunk=50000, topics=[[T(s) for s in B.EV_PRE]])


def main():
    name = sys.argv[1]
    cfg = dict(SETS[name]) if name in SETS else None
    if cfg is None:
        raise SystemExit(f"unknown dataset {name}; one of {sorted(SETS)}")
    if len(sys.argv) > 2:
        cfg["chunk"] = int(sys.argv[2])
    logs = A.fetch_logs(name, cfg["address"], cfg["start"], cfg["end"], source="publicnode", chunk=cfg["chunk"],
                        topics=cfg["topics"])
    led = A.ledger_rows(name)
    print(f"{name}: {len(logs)} logs in {len(led)} windows, {led[0]['from']}..{led[-1]['to']}")


if __name__ == "__main__":
    main()
