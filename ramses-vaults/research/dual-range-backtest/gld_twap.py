import bisect, json, pickle, statistics as st
from common import *
import engine as E
Q = E.Q
tr = json.load(open(RESULTS / "transmissions.json"))
def q(xs,p):
    xs=sorted(xs);return xs[min(len(xs)-1,int(len(xs)*p))]
out={}
for name in ("GLD","SPY"):
    cols,_=pickle.load(open(RESULTS/f"dataset_{name}.pkl","rb"))
    stock0=ADDR[name]["stock_is_token0"]; ts=cols["ts"]; sa=cols["s_after"]
    price=[(s*s*Q if stock0 else Q/(s*s)) for s in sa]
    def twap(t,w):
        # time-weighted average price over [t-w,t] from the step function
        a=t-w; k=bisect.bisect_right(ts,a)-1; k=max(k,0)
        tot=0.0; cur=a; p=price[k]
        j=k+1
        while j<len(ts) and ts[j]<=t:
            tot+=p*(ts[j]-cur); cur=ts[j]; p=price[j]; j+=1
        tot+=p*(t-cur)
        return tot/w
    rows=[x for x in tr[name] if x["obsTs"]>=ts[0]+4000 and x["answer"]>1]
    res={}
    for w in (1,60,300,900,1800,3600,7200):
        errs=[abs(x["answer"]/twap(x["obsTs"],w)-1)*100 for x in rows]
        res[w]={"median_abs_err_pct":st.median(errs),"p90":q(errs,.9)}
    # median of node observations vs pool; also the single best-matching node
    out[name]=res
    print(name,json.dumps(res))
json.dump(out,open(RESULTS/"gld_twap.json","w"),indent=1)
