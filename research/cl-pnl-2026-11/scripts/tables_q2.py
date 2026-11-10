"""Markdown tables for Q2 from out/q2_*.json."""
import json
import sys

from lib_cl import OUT, VENUES
from q2_geometry import HALVES, TRIGGERS

short = lambda v: VENUES[v].label.split(" (")[0]


def load(venue, capital=1000, mpd=4):
    p = OUT / f"q2_{venue}_{capital}_mpd{mpd}.json"
    return json.loads(p.read_text()) if p.exists() else None


def cell(rows, h, t):
    full = next((r for r in rows if r["half"] == h and r["trigger"] == t and r["window"] == "full" and "error" not in r), None)
    wins = [r for r in rows if r["half"] == h and r["trigger"] == t and r["window"] != "full" and "error" not in r]
    return full, wins


def matrix(o, fn, title):
    print(f"\n**{title}**\n")
    print("| Half-width \\\\ trigger | " + " | ".join(str(t) for t in TRIGGERS) + " |")
    print("| --- |" + " --- |" * len(TRIGGERS))
    for h in HALVES:
        out = []
        for t in TRIGGERS:
            full, wins = cell(o["rows"], h, t)
            out.append("n/a" if full is None else fn(full, wins))
        print(f"| ±{h:,} | " + " | ".join(out) + " |")


def summary(venue, capital=1000, mpd=4):
    o = load(venue, capital, mpd)
    if o is None:
        return
    nwin = len(o["windows"]) - 1
    print(f"\n### {short(venue)}, ${capital:,}, {o['data_window'][0][:10]} to {o['data_window'][1][:10]} ({o['days']:.0f} days), {nwin} rolling 30-day windows\n")
    rw = any(r.get("reward_pct_if_sold") is not None for r in o["rows"])
    matrix(o, lambda f, w: f"{f['excess_mark_pct']:+.2f}%", "Full window: net return against holding")
    matrix(o, lambda f, w: f"{min(x['excess_mark_pct'] for x in w):+.2f}% / {sum(x['excess_mark_pct'] > 0 for x in w)} of {len(w)}" if w else "n/a",
           "Rolling 30 days: worst window / windows above holding")
    matrix(o, lambda f, w: "no fee" if not f["fees_usd"] else f"{f['fee_over_lvr']:.2f}", "Full window: fee ÷ LVR (position level, fees after the 20% reserve cut)")
    if rw:
        matrix(o, lambda f, w: f"{f['excess_mark_pct'] + f['reward_pct_if_sold']:+.2f}% / {sum(x['excess_mark_pct'] + x['reward_pct_if_sold'] > 0 for x in w)} of {len(w)}",
               "With rewards valued if sold: full window / rolling windows above holding")
    # key rows
    print("\n| Cell | Full window | Fees | LVR | Recentres | Capital deployed | Worst 30 d | Median 30 d | Best 30 d |" + (" Rewards if sold |" if rw else ""))
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |" + (" --- |" if rw else ""))
    for h, t in ((600, 300), (1200, 300), (2400, 600), (4800, 1200)):
        f, w = cell(o["rows"], h, t)
        if f is None:
            continue
        xs = sorted(x["excess_mark_pct"] for x in w)
        med = xs[len(xs) // 2] if xs else float("nan")
        print(f"| ±{h:,} / {t} | {f['excess_mark_pct']:+.2f}% | ${f['fees_usd']:,.2f} | ${f['lvr_usd']:,.2f} | {f['recenters_core']} | {100 * f['deployed_frac_avg']:.0f}% | "
              f"{(xs[0] if xs else float('nan')):+.2f}% | {med:+.2f}% | {(xs[-1] if xs else float('nan')):+.2f}% |" + (f" {f['reward_pct_if_sold']:+.2f}% |" if rw else ""))
    run = sum(1 for h in HALVES for t in TRIGGERS if cell(o["rows"], h, t)[0] is not None)
    if run < 39:
        print(f"\nOnly {run} cells were run on this pool (n/a above = not run or not allowed by the contract).")
    any_all = [(h, t) for h in HALVES for t in TRIGGERS if (lambda fw: fw[0] is not None and fw[1] and all(x["excess_mark_pct"] > 0 for x in fw[1]) and fw[0]["excess_mark_pct"] > 0)(cell(o["rows"], h, t))]
    print(f"\nCells positive over the full window and in every rolling window: {any_all if any_all else 'none'}.")


if __name__ == "__main__":
    for v in (sys.argv[1:] or ["v4_nvda", "v4_spy", "ramses_nvda", "ramses_spy", "pharaoh_wavax", "univ3_nvda_005"]):
        summary(v)
    for v in ("v4_nvda", "ramses_nvda"):
        summary(v, 10000)
