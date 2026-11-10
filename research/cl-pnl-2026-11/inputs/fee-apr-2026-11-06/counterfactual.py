"""Read-only counterfactual for the live NVDA/USDG range pilot, 2026-10-19 to 2026-11-06 (fee-apr.json window).

Actual: the pair as it ran (checkpoints every hour while the keeper ran; a 10.8-day outage 10-22 to 11-02).
Counterfactual: identical, except liquidity is withdrawn (held as the tokens it was at that moment) over
  (a) every weekend, from 30 min before Friday's 16:00 New York close to 30 min after Monday's 9:30 open, and
  (b) the keeper outage, 2026-10-22 16:29 UTC to 2026-11-02 (the first checkpoint after it).
Mechanics, using only on-chain data already collected:
  * prices: the RHNVDA feed's round history (fee-apr.json events/samples carry it per checkpoint and fee collection);
  * pair composition: PairCheckpoint stockAssets / usdgAssets (re-read from logs here);
  * while deployed, the counterfactual earns the actual pair's own return between consecutive checkpoints;
  * while withdrawn, it holds the composition of the last checkpoint before withdrawal, valued at the feed price;
  * fees collected after a withdrawn window are reduced pro rata for the part that accrued inside it.
Costs not modelled: two extra keeper transactions per weekend (about $0.003 each at measured gas).
"""
import datetime
import json
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

R = 'https://rpc.mainnet.chain.robinhood.com'
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
PAIR = 'e2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06'
NY = ZoneInfo('America/New_York')


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


def words(h):
    h = h[2:]
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def withdrawn_windows(t0, t1, outage):
    """(start, end) unix intervals with liquidity withdrawn."""
    out = [outage]
    day = datetime.datetime.fromtimestamp(t0, NY).replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.datetime.fromtimestamp(t1, NY)
    while day <= end:
        if day.weekday() == 4:  # Friday
            start = day.replace(hour=15, minute=30)
            monday = day + datetime.timedelta(days=3)
            stop = monday.replace(hour=10, minute=0)
            out.append((int(start.timestamp()), int(stop.timestamp())))
        day += datetime.timedelta(days=1)
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return [(max(a, t0), min(b, t1)) for a, b in merged if b > t0 and a < t1]


def overlap(a, b, windows):
    return sum(max(0, min(b, y) - max(a, x)) for x, y in windows)


def inside(t, windows):
    return any(a <= t < b for a, b in windows)


def main():
    d = json.load(open('fee-apr.json'))
    samples = d['samples']
    # Re-read the composition at each checkpoint (stockAssets, usdgAssets are the first two event words).
    topic = subprocess.run(['cast', 'keccak', 'PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)'],
                           capture_output=True, text=True).stdout.strip()
    comp = {}
    blocks = sorted({s['block'] for s in samples})
    for a in range(blocks[0], blocks[-1] + 1, 200000):
        b = min(a + 199999, blocks[-1])
        for log in post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs',
                         'params': [{'address': VAULT, 'fromBlock': hex(a), 'toBlock': hex(b), 'topics': [topic, '0x' + PAIR]}]}):
            w = words(log['data'])
            comp[int(log['blockNumber'], 16)] = (w[0] / 1e18, w[1] / 1e6)
    for s in samples:
        s['nvda'], s['usdg'] = comp[s['block']]
    t0, t1 = samples[0]['time'], samples[-1]['time']
    gaps = sorted(((samples[i + 1]['time'] - samples[i]['time'], i) for i in range(len(samples) - 1)), reverse=True)
    gi = gaps[0][1]
    outage = (samples[gi]['time'], samples[gi + 1]['time'])
    windows = withdrawn_windows(t0, t1, outage)

    # Fees: remove the share that accrued inside withdrawn windows (accrual interval = since the previous collection).
    events = sorted(d['events'], key=lambda e: e['time'])
    fee_removed = 0.0
    prev = t0
    fee_inside_by_time = {}
    for e in events:
        span = max(1, e['time'] - prev)
        share = overlap(prev, e['time'], windows) / span
        fee_removed += e['usd'] * share
        fee_inside_by_time[e['time']] = fee_inside_by_time.get(e['time'], 0) + e['usd'] * share
        prev = e['time']

    # Walk checkpoints. Actual value is the checkpoint pair value at the oracle. The counterfactual compounds the actual
    # step return while deployed, holds frozen tokens while withdrawn, and gives back the fees attributed to withdrawals.
    v_cf = samples[0]['pairUsd']
    frozen = None
    removed_so_far = 0.0
    for i in range(len(samples) - 1):
        a, b = samples[i], samples[i + 1]
        mid = (a['time'] + b['time']) / 2
        withdrawn_now = inside(mid, windows)
        if withdrawn_now:
            if frozen is None:  # withdraw at the last checkpoint before the window, at its composition
                scale = v_cf / a['pairUsd']
                frozen = (a['nvda'] * scale, a['usdg'] * scale)
            v_cf = frozen[0] * b['price'] + frozen[1]
        else:
            if frozen is not None:
                frozen = None  # redeployed at this checkpoint's composition and price (no swap: matched deploy)
            step = b['pairUsd'] / a['pairUsd']
            fees_in_step = sum(v for t, v in fee_inside_by_time.items() if a['time'] < t <= b['time'])
            v_cf = v_cf * step - fees_in_step
            removed_so_far += fees_in_step

    first, last = samples[0], samples[-1]
    hold = first['nvda'] * last['price'] + first['usdg']
    actual = last['pairUsd']
    days = (t1 - t0) / 86400
    withdrawn_days = sum(b - a for a, b in windows) / 86400
    res = {
        'window': [time.strftime('%Y-%m-%d %H:%M', time.gmtime(t0)), time.strftime('%Y-%m-%d %H:%M', time.gmtime(t1))],
        'days': round(days, 2),
        'withdrawnDays': round(withdrawn_days, 2),
        'withdrawnWindows': [[time.strftime('%m-%d %H:%M', time.gmtime(a)), time.strftime('%m-%d %H:%M', time.gmtime(b))] for a, b in windows],
        'priceStart': round(first['price'], 4), 'priceEnd': round(last['price'], 4),
        'holdUsd': round(hold, 4), 'actualUsd': round(actual, 4), 'counterfactualUsd': round(v_cf, 4),
        'actualVsHoldPct': round((actual / hold - 1) * 100, 3),
        'counterfactualVsHoldPct': round((v_cf / hold - 1) * 100, 3),
        'feesTotalUsd': round(d['feesUSD'], 6), 'feesGivenUpUsd': round(removed_so_far, 6),
    }
    gap = actual - hold
    res['shareOfShortfallRecoveredPct'] = round((v_cf - actual) / -gap * 100, 1) if gap < 0 else None
    json.dump(res, open('counterfactual.json', 'w'), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
