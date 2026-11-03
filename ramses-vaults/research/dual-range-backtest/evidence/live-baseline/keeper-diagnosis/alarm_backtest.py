"""Replays the proposed liveness alarm over saved history (no RPC, nothing provisioned)."""
import json,bisect,time
from pathlib import Path
H=Path(__file__).parent; P=H.parent
iso=lambda t:time.strftime('%FT%TZ',time.gmtime(t))
F=json.load(open(P/'raw/feed-answers.json'))['rows']; F=sorted([r for r in F if r['roundId']>1],key=lambda r:r['updatedAt']); FT=[r['updatedAt'] for r in F]
T=json.load(open(H/'timeline.json')); CK=sorted(r['ts'] for r in T if r['hasCheckpoint'])
R=[json.loads(l) for l in open(H/'raw/run-logs-redacted.jsonl')]
HB=[(r['unix'],int(r['gasBalanceWei'])) for r in R if r.get('status')=='heartbeat']; HT=[h[0] for h in HB]
FLOOR=3*10**14; BURN=5*10**14; STALE=43200; MAXAGE=3600; ST=2*MAXAGE+600; GRACE=1800; LOWDAYS=3
start=HB[0][0]; end=HB[-1][0]
def state(t):
    i=bisect.bisect_right(FT,t)-1; age=t-FT[i]
    # continuous freshness: walk back while consecutive updates are <=12h apart
    j=i
    while j>0 and FT[j]-FT[j-1]<=STALE: j-=1
    freshFor=t-FT[j]
    ck=CK[bisect.bisect_right(CK,t)-1]; ckAge=t-ck
    b=HB[bisect.bisect_right(HT,t)-1][1]
    if b<FLOOR: return 'GAS_CRITICAL',age,ckAge,b
    if age>STALE: return 'EXPECTED_WAIT_FEED_STALE',age,ckAge,b
    if freshFor>=GRACE and ckAge>ST: return 'STALLED',age,ckAge,b
    if (b-FLOOR)/BURN<LOWDAYS: return 'GAS_LOW',age,ckAge,b
    return 'OK',age,ckAge,b
seg=[];cur=None
for t in range(start,end,300):
    s=state(t)[0]
    if s!=cur: seg.append({'state':s,'from':iso(t),'fromTs':t}); cur=s
for a,b in zip(seg,seg[1:]+[{'fromTs':end}]): a['hours']=round((b['fromTs']-a['fromTs'])/3600,2)
hbgap=max(b-a for a,b in zip(HT,HT[1:]))
out={'rules':{'GAS_CRITICAL':'balance < 0.0003 ETH (worker floor)','EXPECTED_WAIT_FEED_STALE':'feed age > 43200s (quiet, no page)','STALLED':'balance OK AND feed continuously fresh >=1800s AND last PairCheckpoint age > 7800s','GAS_LOW':'(balance-floor)/0.0005 ETH per day < 3 active days','PROCESS_DOWN (log-based, separate)':'newest heartbeat in DO run logs older than 900s or deployment phase != ACTIVE'},
 'segments':seg,'maxHeartbeatGapSec':hbgap,'processDownFalsePositivesAt900s':sum(1 for a,b in zip(HT,HT[1:]) if b-a>900),'window':[iso(start),iso(end)]}
json.dump(out,open(H/'alarm-backtest.json','w'),indent=1)
for s in seg: print(s['from'],s['state'],s['hours'])
print(hbgap)
