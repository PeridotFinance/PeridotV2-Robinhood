"""Fee-in-force history, SetFeeProtocol history and where collected protocol fees went."""
import bisect, collections, json, pickle, statistics as st
from common import *
ti = TimeIndex(rpc(), path=CACHE / "headers.json")
out = {}
for name in ("SPY", "GLD"):
    raw = json.load(open(CACHE.parent / f"ramses-group-pool-{name}.json"))
    cols, meta = pickle.load(open(RESULTS / f"dataset_{name}.pkl", "rb"))
    fa = sorted(raw["feeadj"], key=lambda l: (l["blockNumber"], l["logIndex"]))
    first_old = words(fa[0]["data"])[0]
    changes = [(ti.ts(l["blockNumber"]), words(l["data"])[1]) for l in fa]
    # time-weighted fee over the pool's life
    t_end = cols["ts"][-1]; cur_t = ti.ts(raw["init"][0]["blockNumber"]); cur_f = first_old
    dur = collections.Counter()
    for t, f in changes:
        dur[cur_f] += t - cur_t; cur_t, cur_f = t, f
    dur[cur_f] += t_end - cur_t
    tot = sum(dur.values())
    twf = sum(f * d for f, d in dur.items()) / tot
    # volume-weighted from dataset
    last30 = [i for i, t in enumerate(cols["ts"]) if t >= t_end - 30 * 86400]
    # protocol
    proto = [(iso(int(ti.ts(l["blockNumber"]))), words(l["data"])[0] / 1e6, words(l["data"])[1] / 1e6, l["blockNumber"]) for l in raw["proto_b"]]
    # collected protocol fees: recipients and token totals
    rec = collections.Counter(); amt = collections.defaultdict(lambda: [0, 0]); senders = collections.Counter()
    for l in raw["collectproto"]:
        r = topic_addr(l["topics"][2]); w = words(l["data"])
        rec[r] += 1; amt[r][0] += w[0]; amt[r][1] += w[1]; senders[topic_addr(l["topics"][1])] += 1
    first_cp = ti.ts(raw["collectproto"][0]["blockNumber"]); last_cp = ti.ts(raw["collectproto"][-1]["blockNumber"])
    stock0 = ADDR[name]["stock_is_token0"]
    out[name] = {"pool_init": iso(int(ti.ts(raw["init"][0]["blockNumber"]))), "initial_fee_ppm": first_old, "fee_changes": len(changes),
                 "time_weighted_fee_ppm": twf, "fee_ppm_time_share": {str(k): v / tot for k, v in sorted(dur.items(), key=lambda kv: -kv[1])[:10]},
                 "fee_min": min(dur), "fee_max": max(dur), "setfeeprotocol": proto,
                 "last30d_mean_fee_ppm_by_swap": st.mean(cols["fee_ppm"][i] for i in last30),
                 "collectProtocol_events": len(raw["collectproto"]), "collectProtocol_first": iso(int(first_cp)), "collectProtocol_last": iso(int(last_cp)),
                 "collect_recipients": {k: {"calls": v, "raw0": amt[k][0], "raw1": amt[k][1]} for k, v in rec.items()}, "collect_callers": dict(senders)}
    print(name, json.dumps(out[name], indent=1))
json.dump(out, open(RESULTS / "fee_history.json", "w"), indent=1)
