"""One-run hedged-LP backtest on the live NVDA/USDG range vault, 2026-10-19 to 2026-11-06 (fee-apr.json window).

Method: handoff 00 section 3, one portfolio ledger in USD (USDG at $1), marked at the RHNVDA oracle at every checkpoint.
  LP leg:    the live pair exactly as it ran: value = PairCheckpoint stock and USDG at the feed price (pairUsd, fees in).
  Hedge leg: a short NVDA in the isolated margin market (borrow NVDA, sell it, hold USDG collateral), sized so the
             borrowed NVDA equals the LP's NVDA holding (the position's delta), rebalanced at a checkpoint when the drift
             exceeds BAND x LP value, and at the start and end.
  Ledger:    hedge cash C (USDG) starts at the posted margin M; each adjustment trades |dq| NVDA at the oracle price and
             pays SWAP_COST on the notional plus GAS_USD; borrowed NVDA grows at the NVDA borrow rate (paid in NVDA);
             all hedge cash earns the USDG supply rate. Equity = LP + C - debtNVDA x price. No outside flows.
  Benchmarks: holding the deposited tokens plus M in cash; the unhedged LP plus M in cash.
Inputs read once at the head (rates, fees, pool fee); see INPUTS. Not modelled: the live per-position caps ($2 gross,
$1 debt) that make this hedge impossible to open today; liquidation (the hedge, margin = half the notional, would be liquidated only by a rise of
about a third); RAM rewards (none on Uniswap); intra-checkpoint path.
"""
import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
FEE_APR = HERE.parent / 'fee-apr-2026-11-06' / 'fee-apr.json'
R = 'https://rpc.mainnet.chain.robinhood.com'
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
PAIR = 'e2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06'
BLOCKS_PER_YEAR = 2_628_000  # native (L1-derived) blocks, as the markets accrue
YEAR = 365 * 86400

INPUTS = {
    'nvdaBorrowRatePerBlock': 7631306231,   # pNVDA, read at the head 2026-11-06
    'usdgSupplyRatePerBlock': 693873118,    # pUSDG
    'openFeeBps': 0, 'closeFeeBps': 0,      # margin config 0x09F9
    'swapCost': 0.0030,                     # NVDA/USDG Uniswap v4 pool fee tier 3000 (price impact at this size ignored)
    'gasUsdPerAdjustment': 0.087,           # 2.23M gas (measured margin close) at the live keepers' measured gas price, ETH $2,260
    'marginFractionOfNotional': 0.5,        # 3x by the contract's measure (position / equity); the pair allows 5x
    'band': 0.05,                           # rebalance when |target - debt| x price > 5% of LP value
}


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


def compositions(samples):
    topic = subprocess.run(['cast', 'keccak', 'PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)'],
                           capture_output=True, text=True).stdout.strip()
    comp = {}
    blocks = sorted({s['block'] for s in samples})
    for a in range(blocks[0], blocks[-1] + 1, 200000):
        b = min(a + 199999, blocks[-1])
        for log in post({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs',
                         'params': [{'address': VAULT, 'fromBlock': hex(a), 'toBlock': hex(b), 'topics': [topic, '0x' + PAIR]}]}):
            h = log['data'][2:]
            comp[int(log['blockNumber'], 16)] = (int(h[0:64], 16) / 1e18, int(h[64:128], 16) / 1e6)
    return comp


def main():
    d = json.load(open(FEE_APR))
    s = d['samples']
    comp = compositions(s)
    for x in s:
        x['nvda'], x['usdg'] = comp[x['block']]
    r_b = INPUTS['nvdaBorrowRatePerBlock'] / 1e18 * BLOCKS_PER_YEAR  # APR
    r_s = INPUTS['usdgSupplyRatePerBlock'] / 1e18 * BLOCKS_PER_YEAR
    p0, lp0 = s[0]['price'], s[0]['pairUsd']
    M = INPUTS['marginFractionOfNotional'] * s[0]['nvda'] * p0
    led = {'swapCost': 0.0, 'gas': 0.0, 'borrowInterest': 0.0, 'supplyInterest': 0.0, 'adjustments': 0}
    cash, debt = M, 0.0

    def adjust(target, price):
        nonlocal cash, debt
        dq = target - debt  # > 0: borrow and sell more NVDA
        notional = abs(dq) * price
        cash += dq * price - notional * INPUTS['swapCost'] - INPUTS['gasUsdPerAdjustment']
        led['swapCost'] += notional * INPUTS['swapCost']
        led['gas'] += INPUTS['gasUsdPerAdjustment']
        led['adjustments'] += 1
        debt = target

    adjust(s[0]['nvda'], p0)
    eq_series = []
    for i in range(1, len(s)):
        a, b = s[i - 1], s[i]
        dt = b['time'] - a['time']
        grow = debt * (r_b * dt / YEAR)  # borrow interest accrues in NVDA
        led['borrowInterest'] += grow * b['price']
        debt += grow
        earn = cash * (r_s * dt / YEAR)
        led['supplyInterest'] += earn
        cash += earn
        last = i == len(s) - 1
        target = 0.0 if last else b['nvda']
        if last or abs(target - debt) * b['price'] > INPUTS['band'] * b['pairUsd']:
            adjust(target, b['price'])
        eq_series.append(b['pairUsd'] + cash - debt * b['price'])

    first, last = s[0], s[-1]
    start = lp0 + M
    hedged_end = last['pairUsd'] + cash  # hedge fully closed at the end
    hold_end = first['nvda'] * last['price'] + first['usdg'] + M
    unhedged_end = last['pairUsd'] + M
    days = (last['time'] - first['time']) / 86400
    hedge_pnl = hedged_end - unhedged_end
    res = {
        'window': [time.strftime('%Y-%m-%d %H:%M', time.gmtime(first['time'])), time.strftime('%Y-%m-%d %H:%M', time.gmtime(last['time']))],
        'days': round(days, 2), 'checkpoints': len(s), 'priceStart': round(p0, 4), 'priceEnd': round(last['price'], 4),
        'inputs': dict(INPUTS, nvdaBorrowAPR=round(r_b * 100, 3), usdgSupplyAPR=round(r_s * 100, 3)),
        'startEquityUsd': round(start, 4), 'marginPostedUsd': round(M, 4),
        'endUsd': {'hedged': round(hedged_end, 4), 'unhedgedLpPlusCash': round(unhedged_end, 4), 'holdTokensPlusCash': round(hold_end, 4)},
        'dollarPnlUsd': {'hedged': round(hedged_end - start, 4), 'unhedged': round(unhedged_end - start, 4), 'hold': round(hold_end - start, 4)},
        'returnPct': {k: round(v / start * 100, 3) for k, v in
                      {'hedged': hedged_end - start, 'unhedged': unhedged_end - start, 'hold': hold_end - start}.items()},
        'hedgedVsHoldPct': round((hedged_end / hold_end - 1) * 100, 3),
        'hedgeLegUsd': {'total': round(hedge_pnl, 4), 'swapCost': round(-led['swapCost'], 4), 'gas': round(-led['gas'], 4),
                        'borrowInterest': round(-led['borrowInterest'], 4), 'supplyInterest': round(led['supplyInterest'], 4),
                        'priceAndRebalance': round(hedge_pnl + led['swapCost'] + led['gas'] + led['borrowInterest'] - led['supplyInterest'], 4)},
        'adjustments': led['adjustments'],
        'lpFeesUsd': round(d['feesUSD'], 4),
        'worstHedgedEquityUsd': round(min(eq_series), 4), 'bestHedgedEquityUsd': round(max(eq_series), 4),
    }
    json.dump(res, open(HERE / 'hedged-lp.json', 'w'), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
