"""Fetch the NVDA/USD Chainlink aggregator AnswerUpdated history (whole chain), read the proxy phase/aggregator at head."""
import json, sys
from lb_rpc import *
from lb_logs import scan
FEED="0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15"
out={}
for n in ["aggregator()","phaseId()","decimals()","latestRound()"]:
    try: out[n]=eth_call(FEED,n)
    except Exception as e: out[n]="ERR "+str(e)[:100]
agg = addr(int(out["aggregator()"],16)); out["aggregator_addr"]=agg
r=scan("aggregator",agg)
print(agg, len(r["logs"]))
ANS=topic("AnswerUpdated(int256,uint256,uint256)")
ans=[l for l in r["logs"] if l["topics"][0]==ANS]
print("AnswerUpdated",len(ans))
rows=[]
for l in ans:
    rows.append({"block":int(l["blockNumber"],16),"answer":sword(int(l["topics"][1],16)),"roundId":int(l["topics"][2],16),"updatedAt":int(l["data"],16)})
json.dump({"proxy":FEED,"proxy_reads":out,"rows":rows},open(HERE/"raw"/"feed-answers.json","w"))
print(rows[0],rows[-1])
