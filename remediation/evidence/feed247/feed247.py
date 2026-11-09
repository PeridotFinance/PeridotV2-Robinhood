"""Read-only: the Chainlink "Robinhood NVDA (24/7)" feed against the 24/5 RHNVDA feed and the Uniswap v4 NVDA/USDG pool.

Rerunnable (writes feed247.json): every round of both feeds since START, every v4 Swap on the vault's NVDA/USDG pool
(price from the event's sqrtPriceX96), the token's pause flags now, and per-event comparisons:
  * at each 24/7 answer, the latest 24/5 answer and the latest pool price before it;
  * at each US session open in the window (Sunday 20:00 ET overnight open, 09:30 ET regular open), both feeds' answers
    in force, their gap, and how long each took to post its first answer after the open;
  * over each weekend (Friday 20:00 ET to Sunday 20:00 ET), the 24/7 answer in force against every pool swap;
  * the longest interval between 24/7 answers (heartbeat 86,400 s, deviation 0.5%) and any answer older than 24 h.
"""
import datetime
import json
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

R = 'https://rpc.mainnet.chain.robinhood.com'
F7 = '0x321A36e441DfBadA01Aa6f9884F14a344E133D1F'
F5 = '0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15'
NVDA = '0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC'
USDG = '0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168'
POOL_MANAGER = '0x8366a39CC670B4001A1121B8F6A443A643e40951'
START = int(sys.argv[1]) if len(sys.argv) > 1 else 1793318400  # 2026-10-30 00:00 UTC: covers the previous weekend from Friday
NY = ZoneInfo('America/New_York')
HEARTBEAT = 86400


def cast(*a):
    return subprocess.run(['cast', *a, '--rpc-url', R], capture_output=True, text=True).stdout.strip()


def post(body):
    for i in range(8):
        out = subprocess.run(['curl', '-s', '-m', '60', '-A', 'curl/8', '-H', 'content-type: application/json', '-d', json.dumps(body), R],
                             capture_output=True, text=True).stdout
        try:
            d = json.loads(out)
            if 'result' in d:
                return d['result']
        except ValueError:
            pass
        time.sleep(2 + 2 * i)
    raise SystemExit('rpc failed')


def rounds(feed, since):
    latest = int(cast('call', feed, 'latestRound()(uint256)').split()[0])
    phase = latest >> 64
    out = []
    for rid in range(latest, (phase << 64), -1):
        o = cast('call', feed, 'getRoundData(uint80)(uint80,int256,uint256,uint256,uint80)', str(rid)).split('\n')
        if len(o) < 5:
            break
        t = int(o[3].split()[0])
        out.append({'round': rid - (phase << 64), 'answer': int(o[1].split()[0]) / 1e8, 'updatedAt': t})
        if t < since:
            break
    return sorted(out, key=lambda r: r['updatedAt'])


def block_at(ts):
    """First L2 block with timestamp >= ts (binary search on the public RPC)."""
    hi = int(post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_blockNumber', 'params': []}), 16)
    lo = max(0, hi - 40_000_000)
    bt = lambda n: int(post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getBlockByNumber', 'params': [hex(n), False]})['timestamp'], 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if bt(mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def pool_id():
    enc = subprocess.run(['cast', 'abi-encode', 'f(address,address,uint24,int24,address)', USDG, NVDA, '3000', '60',
                          '0x0000000000000000000000000000000000000000'], capture_output=True, text=True).stdout.strip()
    pid = subprocess.run(['cast', 'keccak', enc], capture_output=True, text=True).stdout.strip()
    assert pid == '0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1', pid  # the vault's pool (preflight record)
    return pid


def swaps(since_block):
    topic = subprocess.run(['cast', 'keccak', 'Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)'],
                           capture_output=True, text=True).stdout.strip()
    pid = pool_id()
    head = int(post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_blockNumber', 'params': []}), 16)
    out, step = [], 500_000
    for a in range(since_block, head + 1, step):
        b = min(a + step - 1, head)
        for log in post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs',
                         'params': [{'address': POOL_MANAGER, 'fromBlock': hex(a), 'toBlock': hex(b), 'topics': [topic, pid]}]}):
            h = log['data'][2:]
            sqrt = int(h[128:192], 16)
            # token0 USDG (6), token1 NVDA (18): NVDA in USD = 1e12 / (sqrt / 2^96)^2
            price = 1e12 / (sqrt / 2 ** 96) ** 2
            out.append({'block': int(log['blockNumber'], 16), 'price': price})
    # timestamps: sample block times sparsely and interpolate linearly (0.1 s blocks; exact enough at minute scale)
    if out:
        ref = sorted({out[0]['block'], out[-1]['block']} | {o['block'] for o in out[::max(1, len(out) // 40)]})
        tmap = {n: int(post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getBlockByNumber', 'params': [hex(n), False]})['timestamp'], 16) for n in ref}
        keys = sorted(tmap)
        for o in out:
            n = o['block']
            k = max(i for i in range(len(keys)) if keys[i] <= n) if n >= keys[0] else 0
            k2 = min(k + 1, len(keys) - 1)
            a, b = keys[k], keys[k2]
            o['time'] = tmap[a] if a == b else tmap[a] + (tmap[b] - tmap[a]) * (n - a) / (b - a)
    return out, pid


def in_force(rows, t):
    prev = [r for r in rows if r['updatedAt'] <= t]
    return prev[-1] if prev else None


def session_opens(t0, t1):
    out = []
    day = datetime.datetime.fromtimestamp(t0, NY).replace(hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp() <= t1:
        if day.weekday() == 6:  # Sunday 20:00 ET: the overnight session opens
            out.append(('overnight open (Sun 20:00 ET)', int(day.replace(hour=20).timestamp())))
        if day.weekday() < 5:
            out.append(('regular open (09:30 ET)', int(day.replace(hour=9, minute=30).timestamp())))
        day += datetime.timedelta(days=1)
    return [(n, t) for n, t in out if t0 <= t <= t1]


def weekends(t0, t1):
    out = []
    day = datetime.datetime.fromtimestamp(t0, NY).replace(hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp() <= t1:
        if day.weekday() == 4:
            out.append((int(day.replace(hour=20).timestamp()), int((day + datetime.timedelta(days=2)).replace(hour=20).timestamp())))
        day += datetime.timedelta(days=1)
    return out


def main():
    now = int(cast('block', 'latest', '--field', 'timestamp'))
    r7, r5 = rounds(F7, START), rounds(F5, START)
    sw, pid = swaps(block_at(START))
    iso = lambda t: datetime.datetime.utcfromtimestamp(t).strftime('%a %m-%d %H:%M:%S UTC')
    res = {'readAt': iso(now), 'feed247': F7, 'feed245': F5, 'poolId': pid,
           'feed247FirstAnswer': iso(r7[0]['updatedAt']) if r7 else None, 'rounds247': len(r7), 'rounds245InWindow': len(r5),
           'swapsInWindow': len(sw)}
    # 24/7 behaviour: every answer, against the 24/5 answer in force and the last pool swap before it
    rows = []
    for r in r7:
        f5 = in_force(r5, r['updatedAt'])
        p = [s for s in sw if s['time'] <= r['updatedAt']]
        rows.append({'utc': iso(r['updatedAt']), 'answer247': r['answer'],
                     'answer245': f5['answer'] if f5 else None, 'gapTo245Pct': round((r['answer'] / f5['answer'] - 1) * 100, 3) if f5 else None,
                     'pool': round(p[-1]['price'], 4) if p else None,
                     'gapToPoolPct': round((r['answer'] / p[-1]['price'] - 1) * 100, 3) if p else None})
    res['answers247'] = rows
    gaps = [b['updatedAt'] - a['updatedAt'] for a, b in zip(r7, r7[1:])] + ([now - r7[-1]['updatedAt']] if r7 else [])
    res['longestIntervalBetween247AnswersS'] = max(gaps) if gaps else None
    res['currentAge247S'] = now - r7[-1]['updatedAt'] if r7 else None
    res['ever247OlderThanHeartbeat'] = any(g > HEARTBEAT for g in gaps)
    res['ever247OlderThan12h'] = any(g > 43200 for g in gaps)
    # session opens since the 24/7 feed exists
    opens = []
    for name, t in session_opens(r7[0]['updatedAt'] if r7 else now, now):
        a7, a5 = in_force(r7, t), in_force(r5, t)
        n7 = [r for r in r7 if r['updatedAt'] > t]
        n5 = [r for r in r5 if r['updatedAt'] > t]
        opens.append({'open': name, 'utc': iso(t), 'inForce247': a7 and a7['answer'], 'inForce245': a5 and a5['answer'],
                      'ageAtOpen247S': a7 and t - a7['updatedAt'], 'ageAtOpen245S': a5 and t - a5['updatedAt'],
                      'gapPct': round((a7['answer'] / a5['answer'] - 1) * 100, 3) if a7 and a5 else None,
                      'first247After': n7 and iso(n7[0]['updatedAt']), 'first245After': n5 and iso(n5[0]['updatedAt']),
                      'first247AfterAnswer': n7 and n7[0]['answer'], 'first245AfterAnswer': n5 and n5[0]['answer']})
    res['sessionOpens'] = opens
    # weekends: 24/7 (and 24/5) answer in force against every pool swap
    wk = []
    for a, b in weekends(START, now):
        ss = [s for s in sw if a <= s['time'] < b]
        g7 = [abs(s['price'] / in_force(r7, s['time'])['answer'] - 1) * 100 for s in ss if in_force(r7, s['time'])]
        g5 = [abs(s['price'] / in_force(r5, s['time'])['answer'] - 1) * 100 for s in ss if in_force(r5, s['time'])]
        pct = lambda v, q: round(sorted(v)[min(len(v) - 1, int(q * len(v)))], 3) if v else None
        wk.append({'from': iso(a), 'to': iso(min(b, now)), 'complete': b <= now, 'swaps': len(ss),
                   'feed247Existed': bool(r7) and r7[0]['updatedAt'] < b,
                   'pool_vs_247_absPct': {'median': pct(g7, .5), 'p90': pct(g7, .9), 'max': pct(g7, 1)} if g7 else None,
                   'pool_vs_245_absPct': {'median': pct(g5, .5), 'p90': pct(g5, .9), 'max': pct(g5, 1)} if g5 else None,
                   'feed245UpdatesInWeekend': len([r for r in r5 if a <= r['updatedAt'] < b]),
                   'feed247UpdatesInWeekend': len([r for r in r7 if a <= r['updatedAt'] < b])})
    res['weekends'] = wk
    flags = {}
    for f in ('paused()', 'oraclePaused()'):
        flags[f] = cast('call', NVDA, f + '(bool)')
    res['tokenFlagsNow'] = flags
    json.dump(res, open('feed247.json', 'w'), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
