"""Writes gaps.json (classified) and cadence-costs.json from saved evidence."""
import json,statistics,bisect,time,sys
from pathlib import Path
H=Path(__file__).parent; P=H.parent
T=json.load(open(H/'timeline.json')); G=json.load(open(H/'gaps-raw.json'))['gaps']
F=json.load(open(P/'raw/feed-answers.json'))['rows']; F=sorted([r for r in F if r['roundId']>1],key=lambda r:r['updatedAt']); FT=[r['updatedAt'] for r in F]
nvda=lambda t:F[bisect.bisect_right(FT,t)-1]['answer']/1e8
C={ '2026-09-15T17:58:25Z':('d','Not yet deployed. Same-day operator test: governor added liquidity 09-15T17:58:44, removed it in the tx that ended the gap. range_keeper.py first committed 10-08, wallet 10-15.'),
 '2026-09-16T11:58:10Z':('d','Liquidity zero, then paused. Adapter LiquidityDecreased at 09-16T11:58:10 removed 137355898984 (all added). PairPauseUpdated allocationPaused=true 09-26T20:15:48, false 10-06T13:58:37 (timelock schedule 10-05, execute 10-06). Checkpoint 2m23s after reopening. No position to checkpoint, no keeper existed. Feed fresh 70.1%: not the cause.'),
 '2026-10-06T14:01:00Z':('d','Not yet deployed. Full-range LP open (128455941567) from 10-06T14:01:02; only manual governor txs; keeper tool first committed 10-08, V3 upgrade executed 10-09T11:05:07. Feed fresh 94.2%: not the cause.'),
 '2026-10-09T11:06:06Z':('d','Not yet deployed (unattended). Governor-signed; local mode needs keystore password per send (range_keeper.py docstring). Feed fresh 100%.'),
 '2026-10-09T15:00:06Z':('d','Not yet deployed (unattended). Same governor-signed mode. Feed fresh 100%.'),
 '2026-10-09T17:12:24Z':('d','Not yet deployed (unattended). Feed stale 44.86h of 69.08h (10-10T03:09:16 to 10-12T00:00:41, weekend) would have refused any keeper, but 24.2h was fresh and also unused (10h Fri, 14.3h Mon), so staleness alone does not explain it.'),
 '2026-10-12T14:17:22Z':('d','Not yet deployed (unattended). Governor-signed checkpoint/rebalance one day later. Feed fresh 100%.'),
 '2026-10-13T13:17:33Z':('d','Not yet deployed for 117.8h, then 13.0h oracle refusal. Last governor tx 10-13T13:17:33. Cloud monitor-only EXECUTE=false from 10-15T12:00:40; KEEPER_ROLE queued 10-15T11:54:51 (ready +1h), executed 10-18T11:04:38; deployment b447801c with signer started 10-18T11:05:19. Logs then show 151 StaleOracle cycles 10-18T11:05:25 to 10-19T00:00:30; feed updated 00:00:42; first keeper tx 00:05:40 (recenter). Why the grant waited 2.9 days after it was ready: undetermined.'),
 '2026-10-22T16:29:58Z':('a','Gas shortage. Keeper rebalance nonce 137 left 294912258829028 wei, below MIN_GAS_WEI 3e14. Logs: 2998 consecutive awaiting_gas cycles 10-22T16:35:06 to 11-02T10:57:58, balance constant, worker skips simulation in that state. Process up: single started line, max heartbeat gap 656s, run_restarted empty, no deployment since 10-18. Feed fresh 68.1%, so oracle does not explain it. Ended by governor nonce 321 sending 0.005 ETH.')}
out=[]
for g in G:
    c=C[g['from']]; g2=dict(g); g2['classification']=c[0]; g2['evidence']=c[1]; out.append(g2)
legend={'a':'gas shortage','b':'oracle or contract rejection','c':'process not running','d':'deliberately paused or not yet deployed','e':'undetermined'}
json.dump({'legend':legend,'expectedCadenceSec':3600,'thresholdSec':7200,'scope':'production pair NVDA/USDG; any sender; PairCheckpoint emitted by checkpoint, rebalance, recenter, withdraw','gaps':out},open(H/'gaps.json','w'),indent=1)
K=sorted([r for r in T if r['who']=='keeper'],key=lambda r:r['nonce'])
types={}
for ty in('checkpoint','rebalance','recenter'):
    S=[r for r in K if r['type']==ty]
    types[ty]=dict(n=len(S),gasMedian=statistics.median(r['gasUsed'] for r in S),gasMin=min(r['gasUsed'] for r in S),gasMax=max(r['gasUsed'] for r in S),eth=sum(r['feeWei'] for r in S)/1e18,usd=sum(r['usd'] for r in S),usdAvg=statistics.mean(r['usd'] for r in S))
reb=[r for r in K if r['type']=='rebalance']
for r in reb: r['dep']=r['stockUsed']/1e18*nvda(r['ts'])+r['usdgUsed']/1e6
fees=sum(r.get('feeStock',0)/1e18*nvda(r['ts'])+r.get('feeUsdg',0)/1e6 for r in K)
win={}
for nm,a,b in (('2026-10-19_to_22','2026-10-19','2026-10-23'),('2026-11-02_to_03','2026-11-02','2026-11-04')):
    S=[r for r in K if a<=r['iso']<b]; d=(S[-1]['ts']-S[0]['ts'])/86400
    win[nm]=dict(first=S[0]['iso'],last=S[-1]['iso'],days=round(d,3),txs=len(S),txPerDay=round(len(S)/d,1),usdPerDay=round(sum(r['usd'] for r in S)/d,3),ethPerDay=sum(r['feeWei'] for r in S)/1e18/d)
json.dump({'byType':types,'windows':win,'patternsFromChainAndLogs':{'checkpoint+rebalance pairs':56,'solo checkpoint then dust wait':41,'solo rebalance':23,'recenter':2},
 'rebalanceDeployedUsdTotal':sum(r['dep'] for r in reb),'rebalanceDeployedUsdMedian':statistics.median(r['dep'] for r in reb),'rebalancesWhereDeployedLessThanGas':sum(r['dep']<r['usd'] for r in reb),'rebalanceCount':len(reb),
 'feesCollectedInKeeperTxsUsd':fees,'keeperGasUsdTotal':sum(r['usd'] for r in K),'keeperFeeWeiTotal':sum(r['feeWei'] for r in K),
 'note':'Both running windows are weekdays only; feed-stale hours send nothing (balance flat in logs).'},open(H/'cadence-costs.json','w'),indent=1)
print(json.dumps(types,indent=0)[:600]); print(win); print(len(reb),fees)
