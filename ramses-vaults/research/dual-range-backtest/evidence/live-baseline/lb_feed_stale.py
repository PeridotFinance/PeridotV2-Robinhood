"""Quantify NVDA/USD feed age vs the 12h guard bound and the 72h lending-oracle bound. Inputs raw/feed-answers.json (AnswerUpdated logs) and snapshot pin time."""
import json, time, bisect
from lb_rpc import HERE
d=json.load(open(HERE/"raw"/"feed-answers.json")); snap=json.load(open(HERE/"raw"/"snapshot.json"))
rows=[r for r in d["rows"] if r["roundId"]>1]       # round 1 is an initialisation print (answer 2.08e18 on an 8-dec feed)
rows.sort(key=lambda r:r["updatedAt"])
T=[r["updatedAt"] for r in rows]
now=snap["pin"]["timestamp"]
def stale_seconds(t0,t1,bound):
    """seconds in [t0,t1] for which (t - latest updatedAt) > bound; feed age is measured from the most recent update at or before t."""
    tot=0
    # segment boundaries: each update at T[i] starts a segment [T[i], T[i+1]) where age = t-T[i]; stale part = [T[i]+bound, T[i+1])
    for i,u in enumerate(T):
        s=u; e=T[i+1] if i+1<len(T) else now
        a=max(s+bound,t0); b=min(e,t1)
        if b>a: tot+=b-a
    return tot
def window(name,t0,t1):
    span=t1-t0
    # observation starts when first valid update exists
    t0=max(t0,T[0])
    span=t1-t0
    res={"window":name,"from":time.strftime("%FT%TZ",time.gmtime(t0)),"to":time.strftime("%FT%TZ",time.gmtime(t1)),"seconds":span}
    for b,l in [(43200,"gt12h"),(259200,"gt72h")]:
        s=stale_seconds(t0,t1,b); res[l+"_seconds"]=s; res[l+"_pct"]=round(100*s/span,3)
    # longest gap in window
    gaps=[(T[i+1]-T[i],T[i],T[i+1]) for i in range(len(T)-1) if T[i+1]>t0 and T[i]<t1]
    g=max(gaps) if gaps else None
    if g: res["longest_gap_h"]=round(g[0]/3600,2); res["longest_gap_from"]=time.strftime("%FT%TZ",time.gmtime(g[1])); res["longest_gap_to"]=time.strftime("%FT%TZ",time.gmtime(g[2]))
    res["gaps_over_12h"]=sum(1 for x in gaps if x[0]>43200); res["gaps_over_72h"]=sum(1 for x in gaps if x[0]>259200)
    res["updates_in_window"]=sum(1 for t in T if t0<=t<=t1)
    return res
out={"feed_pin_age_sec":now-T[-1],"latest_update":time.strftime("%FT%TZ",time.gmtime(T[-1])),"n_rounds":len(rows),"first_valid_update":time.strftime("%FT%TZ",time.gmtime(T[0])),
     "windows":[window("last_30d",now-30*86400,now),window("last_7d",now-7*86400,now),window("since_feed_start",T[0],now),window("since_lending_markets_listed_2026-09-05",1788596760,now)]}
# all gaps >72h ever
big=[(T[i+1]-T[i],T[i],T[i+1]) for i in range(len(T)-1) if T[i+1]-T[i]>259200]
out["gaps_over_72h_list"]=[{"hours":round(g/3600,2),"from":time.strftime("%FT%TZ",time.gmtime(a)),"to":time.strftime("%FT%TZ",time.gmtime(b))} for g,a,b in big]
big12=[(T[i+1]-T[i],T[i],T[i+1]) for i in range(len(T)-1) if T[i+1]-T[i]>43200 and T[i+1]>now-30*86400]
out["gaps_over_12h_last30d"]=[{"hours":round(g/3600,2),"from":time.strftime("%FT%TZ",time.gmtime(a)),"to":time.strftime("%FT%TZ",time.gmtime(b))} for g,a,b in big12]
json.dump(out,open(HERE/"feed-staleness.json","w"),indent=1)
print(json.dumps(out,indent=1))
