"""Weekly USD volume, realized volatility (5-minute pool returns) and pool fee income per week, for SPY and GLD."""
import json, math, pickle, collections
from bt_lib import *
import engine as E
Q = E.Q
out = {}
for name in ("SPY", "GLD"):
    ds, meta, _ = load_dataset(name)
    stock0 = ADDR[name]["stock_is_token0"]
    ts, sa = ds.ts, ds.s_after
    price = [(s * s * Q if stock0 else Q / (s * s)) for s in sa]
    vol_usd = collections.defaultdict(float); fee_usd = collections.defaultdict(float)
    for i in range(len(ts)):
        wk = int(ts[i] // 604800)
        p = price[i]
        amt = ds.in_amt[i]
        if stock0:
            usd = amt / 1e6 if ds.in_tok[i] == 1 else amt / 1e18 * p
        else:
            usd = amt / 1e6 if ds.in_tok[i] == 0 else amt / 1e18 * p
        vol_usd[wk] += usd; fee_usd[wk] += usd * ds.fee_ppm[i] / 1e6
    # realized vol from 5-minute samples
    rets = collections.defaultdict(list)
    step = 300; t = ts[0]; j = 0; last = price[0]
    while t < ts[-1]:
        t += step
        while j + 1 < len(ts) and ts[j + 1] <= t: j += 1
        p = price[j]
        rets[int(t // 604800)].append(math.log(p / last)); last = p
    rows = []
    for wk in sorted(vol_usd):
        r = rets.get(wk, [])
        sig = (sum(x * x for x in r) / max(len(r), 1)) ** 0.5 * math.sqrt(365 * 288) if r else 0
        rows.append({"week_start": iso(wk * 604800), "volume_usd": vol_usd[wk], "pool_fee_income_usd": fee_usd[wk], "realized_vol_annualized": sig})
    out[name] = rows
    print(name)
    for r in rows: print("  %s vol $%10.0f feeinc(gross) $%7.0f rv %.1f%%" % (r["week_start"][:10], r["volume_usd"], r["pool_fee_income_usd"], r["realized_vol_annualized"] * 100))
json.dump(out, open(RESULTS / "weekly_context.json", "w"), indent=1)
