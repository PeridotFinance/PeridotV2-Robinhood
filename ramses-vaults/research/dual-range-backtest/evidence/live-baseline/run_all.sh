#!/bin/bash
# READ-ONLY re-run of the live-baseline reconciliation (python3 stdlib + cast only; RPC via curl, serial, IPv4).
# Note: the public RPC prunes historical *state* after a few minutes; step 1 and 6/7 must be run together and quickly (each pins head-2).
# Log scans (steps 2-4) are whole-chain eth_getLogs and are cached in raw/logs-*.json (delete a file to force a rescan).
set -euo pipefail
cd "$(dirname "$0")"
python3 lb_snapshot.py                      # 1. fresh state at one pinned block -> raw/snapshot.json
python3 - <<'PY'                            # 2. whole-chain log scans (cached)
import sys; sys.path.insert(0,'.')
from lb_logs import scan
from lb_rpc import topic
T={"vault":"0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f","adapter":"0xadA73211711e4790bc83B5d6B39f47fE04D276f3","guard":"0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741","reserve":"0x806b182B050f7EcF908758dD6bBF91DB8B2212aF","pNVDA":"0xa155cccb986774ae818b3f10f07d01d1b7a47b26","pUSDG":"0x55aed0569c8f0d166d71face57b57c2f2624a563","comptroller":"0x6148183676e304dbe63a85c350c208da3ceac39c","timelock":"0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498","oracle":"0x266f014d1325774f1190f963df4369e07dda1d33","adapteroracle":"0xe4e03c2fdaef915ace705d106b2660b1e342a2e4","feed":"0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15"}
for n,a in T.items(): scan(n,a)
TR=topic("Transfer(address,address,uint256)"); pad=lambda a:"0x"+a[2:].rjust(64,"0")
NVDA="0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"; USDG="0x5fc5360d0400a0fd4f2af552add042d716f1d168"
for side,tok,pt in (("NVDA",NVDA,T["pNVDA"]),("USDG",USDG,T["pUSDG"])):
    scan(f"tok{side}_to_pToken",tok,topics=[TR,None,pad(pt)]); scan(f"tok{side}_from_pToken",tok,topics=[TR,pad(pt),None])
PY
python3 lb_feed.py                           # 3. NVDA/USD aggregator AnswerUpdated history
python3 - <<'PY'                             # 4. ETH/USD feed history (gas -> USD)
import sys,json; sys.path.insert(0,'.')
from lb_rpc import *; from lb_logs import scan
F="0x78F3556b67E17Df817D51Ef5a990cDaF09E8d3A9"; agg=addr(int(eth_call(F,"aggregator()"),16)); r=scan("ethaggregator",agg); ANS=topic("AnswerUpdated(int256,uint256,uint256)")
rows=[{"block":int(l["blockNumber"],16),"answer":sword(int(l["topics"][1],16)),"roundId":int(l["topics"][2],16),"updatedAt":int(l["data"],16)} for l in r["logs"] if l["topics"][0]==ANS]
json.dump({"proxy":F,"aggregator":agg,"rows":rows},open("raw/eth-feed-answers.json","w"))
PY
python3 lb_fetch_meta.py tokNVDA_from_pToken tokNVDA_to_pToken tokUSDG_from_pToken tokUSDG_to_pToken   # 5. block headers (ts, l1BlockNumber) + receipts
python3 lb_borrowers.py                      # 6. borrowers, debts, *Current sims (own pin)
python3 lb_stale_sim.py                      # 7. eth_call state-override stale-oracle simulation (own pin)
python3 lb_margin.py > /dev/null            # 7b. isolated-margin stack reads (own pin)
python3 lb_feed_stale.py                     # 8. feed-age statistics
python3 lb_costs.py                          # 9. gas costs
python3 lb_returns.py > raw/returns-run.txt  # 10. log-only market/vault reconstruction + windows (self-checks vs snapshot)
python3 lb_state_table.py > /dev/null        # 11. derived state table
python3 lb_decomp.py > raw/decomp-run.txt    # 12. decomposition incl. protocol-paid costs
