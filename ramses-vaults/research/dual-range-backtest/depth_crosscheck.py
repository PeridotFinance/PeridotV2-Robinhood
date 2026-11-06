"""Cross-check the fork-read initialized-tick nets against the Mint/Burn history, and derive the depth table in USD."""
import collections, json, re, sys
from common import *
import engine as E

log = open(RESULTS / "fork-depth-all.log").read()
block_l2 = None
ticks = collections.defaultdict(dict)
for m in re.finditer(r"TICK,(\w+),(-?\d+),(-?\d+)", log):
    ticks[m.group(1)][int(m.group(2))] = int(m.group(3))
depth = collections.defaultdict(list)
for m in re.finditer(r"DEPTH,(\w+),(\w+),(\d+),(\d+),(\d+)", log):
    depth[m.group(1)].append((m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))))
cur_tick = {m.group(1): int(m.group(2)) for m in re.finditer(r"DEPTH_POOL: (\w+)\n\s+DEPTH_BLOCK: \d+\n\s+DEPTH_TICK: (-?\d+)", log)}
liq_now = {m.group(1): int(m.group(2)) for m in re.finditer(r"DEPTH_POOL: (\w+)\n\s+DEPTH_BLOCK: \d+\n\s+DEPTH_TICK: -?\d+\n\s+DEPTH_LIQ: (\d+)", log)}
blk = int(re.search(r"DEPTH_BLOCK: (\d+)", log).group(1))
# the fork's L2 block is not printed (block.number is the native block); take the pin from the log name via pin file if present
r = rpc()
import subprocess
out = {}
for name in ("SPY", "GLD"):
    raw = json.load(open(CACHE.parent / f"ramses-group-pool-{name}.json"))
    last_loaded = raw["head"]
    pin = int(sys.argv[1]) if len(sys.argv) > 1 else r.head()
    net = collections.Counter()
    def add(logs, sign_lo, sign_hi, liqidx):
        for l in logs:
            w = words(l["data"]); L = w[liqidx]
            lo = topic_int(l["topics"][2], 24); hi = topic_int(l["topics"][3], 24)
            net[lo] += sign_lo * L; net[hi] += sign_hi * L
    add(raw["mint"], +1, -1, 2)
    add(raw["burn"], -1, +1, 0)
    # extend from the loaded head to the pinned block
    ext_m = r.logs(ADDR[name]["pool"], [T("Mint(address,address,uint256,int24,int24,uint128,uint256,uint256)")], last_loaded + 1, pin, None)
    ext_b = r.logs(ADDR[name]["pool"], [T("Burn(address,int24,int24,uint128,uint256,uint256)")], last_loaded + 1, pin, None)
    add(ext_m, +1, -1, 2); add(ext_b, -1, +1, 0)
    mism = []
    for t, n in sorted(ticks[name].items()):
        if net.get(t, 0) != n:
            mism.append((t, n, net.get(t, 0)))
    # active liquidity at the current tick from logs: sum of nets at ticks <= current
    ct = cur_tick[name]
    L_logs = sum(v for t, v in net.items() if t <= ct)
    out[name] = {"ticks_checked": len(ticks[name]), "mismatches": mism[:10], "n_mismatch": len(mism),
                 "active_liquidity_from_logs": L_logs, "active_liquidity_pool": liq_now[name], "extended_events": len(ext_m) + len(ext_b)}
    print(name, json.dumps(out[name]))
json.dump(out, open(RESULTS / "depth_crosscheck.json", "w"), indent=1)

# USD depth table
price = {"SPY": None, "GLD": None, "NVDA": None}
st = json.load(open(RESULTS / "pools_static.json"))
tab = {}
for name in ("SPY", "GLD", "NVDA"):
    s = st[name]["slot0"]["sqrtPriceX96"] / 2 ** 96
    stock0 = ADDR[name]["stock_is_token0"]
    p = s * s * E.Q if stock0 else E.Q / (s * s)
    rows = []
    for kind, pct, amt_in, amt_out in depth[name]:
        down = kind.startswith("token0_in")
        # token0 in: for SPY token0=stock (stock sold, price falls); for GLD/NVDA token0=USDG (stock bought, price rises)
        if stock0:
            side = "sell_stock_price_down" if down else "buy_stock_price_up"
            in_usd = amt_in / 1e18 * p if down else amt_in / 1e6
            out_usd = amt_out / 1e6 if down else amt_out / 1e18 * p
        else:
            side = "buy_stock_price_up" if down else "sell_stock_price_down"
            in_usd = amt_in / 1e6 if down else amt_in / 1e18 * p
            out_usd = amt_out / 1e18 * p if down else amt_out / 1e6
        rows.append({"side": side, "move_pct": pct / 10_000, "input_usd": in_usd, "output_usd": out_usd})
    tab[name] = rows
    print(name, "price", round(p, 2))
    for r_ in sorted(rows, key=lambda x: (x["side"], x["move_pct"])):
        print("   %-24s %4.0f%%  in $%10.0f  out $%10.0f" % (r_["side"], r_["move_pct"], r_["input_usd"], r_["output_usd"]))
json.dump(tab, open(RESULTS / "depth_table.json", "w"), indent=1)
