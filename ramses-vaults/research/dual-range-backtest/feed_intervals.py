import json, statistics as st
from common import *
d = json.loads((RESULTS / "answer_updated.json").read_text())
head_ts = 1793582365 + 3600  # about now; refined below
out = {}
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs)-1, int(len(xs)*p))]
for name, rows in d["series"].items():
    ts = [r["updatedAt"] for r in rows]; px = [r["answer"]/1e8 for r in rows]
    end = max(ts)
    for label, win in [("14d", 14*86400), ("all", None)]:
        sel = [i for i, t in enumerate(ts) if win is None or t >= end - win]
        t2 = [ts[i] for i in sel]; p2 = [px[i] for i in sel]
        gaps = [b-a for a, b in zip(t2, t2[1:])]
        moves = [abs(b/a-1)*100 for a, b in zip(p2, p2[1:])]
        # stale time: seconds with age > 12h within window
        over12 = sum(max(0, g-43200) for g in gaps)
        span = t2[-1]-t2[0]
        # by session of update
        sess = {}
        for t in t2: sess[session_of(t)] = sess.get(session_of(t), 0) + 1
        # gap within trading sessions only (gaps whose start is regular/overnight and end the same)
        non_wk = [g for a, g in zip(t2, gaps) if session_of(a) in ("regular","overnight")]
        rec = dict(n=len(t2), span_h=span/3600, gap_median_s=st.median(gaps), gap_p90_s=pct(gaps,.9), gap_max_h=max(gaps)/3600,
                   frac_time_age_gt12h=over12/span, move_median_pct=st.median(moves), move_p90_pct=pct(moves,.9), move_max_pct=max(moves),
                   updates_by_session=sess, nonweekend_gap_p90_s=pct(non_wk,.9) if non_wk else None, nonweekend_gap_max_h=max(non_wk)/3600 if non_wk else None)
        out[f"{name}_{label}"] = rec
        print(name, label, json.dumps({k: (round(v,3) if isinstance(v,float) else v) for k,v in rec.items()}))
(RESULTS / "feed_intervals.json").write_text(json.dumps(out, indent=1))
