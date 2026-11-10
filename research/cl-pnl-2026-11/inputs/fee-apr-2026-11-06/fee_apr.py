"""Read-only: fee APR of the live NVDA/USDG ranged position since the 2400-tick policy (L2 block 92,657,111)."""
import json, subprocess, time, sys
R = 'https://rpc.mainnet.chain.robinhood.com'
ADAPTER = '0xadA73211711e4790bc83B5d6B39f47fE04D276f3'
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
FEED = '0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15'
PAIR = 'e2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06'
START = 92657111
TOPIC = subprocess.run(['cast', 'keccak', 'FeesCollected(bytes32,uint256,uint256)'], capture_output=True, text=True).stdout.strip()

def post(body):
    for i in range(8):
        out = subprocess.run(['curl', '-s', '-m', '60', '-A', 'curl/8', '-H', 'content-type: application/json', '-d', json.dumps(body), R],
                             capture_output=True, text=True).stdout
        try:
            d = json.loads(out)
            if isinstance(d, list) or 'result' in d:
                return d
        except ValueError:
            pass
        time.sleep(2 + 2 * i)
    raise SystemExit('rpc failed: ' + out[:200])

def one(method, params):
    return post({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})['result']

def batch(calls):
    out = []
    for s in range(0, len(calls), 20):
        rs = post([{'jsonrpc': '2.0', 'id': j, 'method': m, 'params': p} for j, (m, p) in enumerate(calls[s:s + 20])])
        rs = sorted(rs, key=lambda r: r['id'])
        out += [r.get('result') for r in rs]
    return out

def words(h):
    h = h[2:]
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]

tip = int(one('eth_blockNumber', []), 16)
logs = []
for a in range(START, tip + 1, 200000):
    b = min(a + 199999, tip)
    logs += one('eth_getLogs', [{'address': ADAPTER, 'fromBlock': hex(a), 'toBlock': hex(b), 'topics': [TOPIC, '0x' + PAIR]}])
print('fee events', len(logs), file=sys.stderr)

CP = subprocess.run(['cast', 'keccak', 'PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)'], capture_output=True, text=True).stdout.strip()
cps = []
for a in range(START, tip + 1, 200000):
    b = min(a + 199999, tip)
    cps += one('eth_getLogs', [{'address': VAULT, 'fromBlock': hex(a), 'toBlock': hex(b), 'topics': [CP, '0x' + PAIR]}])
print('checkpoint events', len(cps), file=sys.stderr)

# Feed history from round data at the latest block (historical state is pruned on the public RPC).
latest = words(one('eth_call', [{'to': FEED, 'data': '0xfeaf968c'}, 'latest']))
rid, rounds = latest[0], [(latest[3], latest[1] / 1e8)]
t_start = None
while True:
    ids = [rid - k for k in range(1, 21)]
    rs = batch([('eth_call', [{'to': FEED, 'data': '0x9a6fc8f5' + format(x, '064x')}, 'latest']) for x in ids])
    stop = False
    for r in rs:
        if not r or len(r) < 3:
            stop = True
            break
        w = words(r)
        rounds.append((w[3], w[1] / 1e8))
    rid -= 20
    if stop or rounds[-1][0] < int(one('eth_getBlockByNumber', [hex(START), False])['timestamp'], 16) - 86400:
        break
rounds.sort()
def price_at(t):
    best = rounds[0][1]
    for ts, px in rounds:
        if ts <= t:
            best = px
        else:
            break
    return best
print('feed rounds', len(rounds), 'from', rounds[0][0], file=sys.stderr)

allb = sorted({l['blockNumber'] for l in logs} | {c['blockNumber'] for c in cps}, key=lambda x: int(x, 16))
heads = dict(zip(allb, batch([('eth_getBlockByNumber', [b, False]) for b in allb])))
tm = lambda b: int(heads[b]['timestamp'], 16)
events = []
for l in logs:
    s_, u_ = words(l['data'])[:2]
    t = tm(l['blockNumber']); px = price_at(t)
    events.append({'block': int(l['blockNumber'], 16), 'time': t, 'nvda': s_ / 1e18, 'usdg': u_ / 1e6, 'price': px, 'usd': s_ / 1e18 * px + u_ / 1e6})
samples = []
for c in cps:
    w = words(c['data']); t = tm(c['blockNumber']); px = price_at(t)
    samples.append({'block': int(c['blockNumber'], 16), 'time': t, 'price': px, 'pairUsd': w[0] / 1e18 * px + w[1] / 1e6, 'pnlUSDG': (w[3] - (1 << 256) if w[3] >> 255 else w[3]) / 1e18})
samples.sort(key=lambda x: x['time'])
t0 = int(one('eth_getBlockByNumber', [hex(START), False])['timestamp'], 16)
t1 = int(one('eth_getBlockByNumber', [hex(tip), False])['timestamp'], 16)
# time-weighted average pair value between checkpoints
tw = sum(samples[i]['pairUsd'] * (samples[i + 1]['time'] - samples[i]['time']) for i in range(len(samples) - 1))
avg_pair = tw / (samples[-1]['time'] - samples[0]['time'])
days = (t1 - t0) / 86400
fees = sum(e['usd'] for e in events)
first, last = samples[0], samples[-1]
result = {'fromBlock': START, 'toBlock': tip, 'days': round(days, 2), 'feeEvents': len(events), 'checkpoints': len(samples),
          'feedRounds': len(rounds), 'feesNVDA': sum(e['nvda'] for e in events), 'feesUSDG': sum(e['usdg'] for e in events),
          'feesUSD': round(fees, 6), 'avgPairUsd': round(avg_pair, 4), 'feeAprOnPair': round(fees / avg_pair / days * 365 * 100, 2),
          'priceStart': first['price'], 'priceEnd': last['price'], 'pairUsdStart': round(first['pairUsd'], 4), 'pairUsdEnd': round(last['pairUsd'], 4),
          'samples': samples, 'events': events}
json.dump(result, open('fee-apr.json', 'w'), indent=1)
print(json.dumps({k: v for k, v in result.items() if k not in ('samples', 'events')}, indent=1))
