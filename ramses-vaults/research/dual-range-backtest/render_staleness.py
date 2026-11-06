"""Markdown table of the feed-staleness breakdown from evidence/staleness-by-session.json (read-only formatting)."""
import json

from common import HERE

d = json.loads((HERE / "evidence" / "staleness-by-session.json").read_text())
print("| feed | regular hours (Mon-Fri 09:30-16:00 ET) | overnight (rest of the 24/5 week) | weekend (Fri 20:00 to Sun 20:00 ET) | US holiday | all trading sessions | all closures | stale hours per week (of which in trading sessions) | stale episodes (median / max hours; began in a trading session / a closure) |")
print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
for n in ("SPY", "NVDA", "GLD"):
    r = d[n]
    b = r["bySession"]
    cell = lambda s: f"{100 * b[s]['staleShare']:.1f}% of {b[s]['hours']:.0f} h" if b[s]["hours"] else "n/a"
    e = r["episodes"]
    print(f"| {n} | {cell('regular')} | {cell('overnight')} | {cell('weekend')} | {cell('holiday')} | {100 * r['tradingSessionsStaleShare']:.1f}% | {100 * r['closuresStaleShare']:.1f}% | "
          f"{r['staleHoursPerWeek']:.1f} ({r['staleHoursPerWeekDuringTradingSessions']:.1f}) | {e['count']} ({e['medianHours']:.1f} / {e['maxHours']:.1f}; {e['beganInTradingSession']} / {e['beganInClosure']}) |")
print("\n" + d["note"])
