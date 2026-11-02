"""Markdown table of the r33 -> USDG route comparison from evidence/pools/r33-routes.json (read-only formatting)."""
import json

from common import HERE

d = json.loads((HERE / "evidence" / "pools" / "r33-routes.json").read_text())
amts = d["amountsR33"]
ref = d["referenceMidUsdgPerR33"]
print(f"**r33 -> USDG route quotes** (reference mid ${ref:.5f} per r33 from the CL200 spot; nominal value of the four amounts: " + ", ".join(f"${n * ref:,.0f}" for n in amts) + "). USDG received (shortfall versus nominal in bp):\n")
print("| route | " + " | ".join(f"{n:,} r33" for n in amts) + " |")
print("| --- | " + " | ".join("---" for _ in amts) + " |")
best = {}
for row in d["routes"]:
    cells = []
    for i, x in enumerate(row["results"]):
        if x["usdgOut"] is None:
            cells.append(x["status"].replace("capacity_exhausted", "no capacity").replace("@", " at "))
        else:
            cells.append(f"${x['usdgOut']:,.0f} ({x['shortfallBpVsReferenceMid']:,.0f})")
            if i not in best or x["usdgOut"] > best[i][0]:
                best[i] = (x["usdgOut"], row["route"])
    print(f"| {row['route']} | " + " | ".join(cells) + " |")
print("\n**Best quoted route per amount**: " + "; ".join(f"{amts[i]:,} r33: {best[i][1]} (${best[i][0]:,.0f})" for i in sorted(best)))
print("\nPins (each pool at its own block): " + ", ".join(f"{k} {v['l2Block']:,}" for k, v in d["pins"].items()))
