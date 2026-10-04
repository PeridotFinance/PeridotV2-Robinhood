"""Read-only yield tooling for the NVDA/USDG vault and its two pToken markets.

The vault has no APY function and the public RPC prunes state within about a day, so realized yield
is built from two things that never need old state:

  * `snapshot`  appends the CURRENT on-chain state (exchange rates, vault ledger, LP status, prices)
                to a JSONL file. Run it on a schedule (e.g. hourly) and keep the file.
  * `events`    decodes the vault's own events (fees, checkpoints, rebalances, withdrawals) from logs,
                which the RPC keeps far longer than state. Writes JSON and a Dune-uploadable CSV.
  * `report`    combines both into `frontend-yield.json`: LP status, lifetime fees and loss, the
                borrow-interest APY, and trailing exchange-rate APY with an explicit window.

Nothing here signs or sends a transaction. Honest-numbers rules baked in: a window shorter than
`MIN_WINDOW_SECONDS` yields `null` (the UI should show "n/a"), and event amounts are valued at the
CURRENT oracle price and labelled as such.

Usage:
    python3 remediation/tools/vault_yield.py snapshot
    python3 remediation/tools/vault_yield.py events [--from-block N]
    python3 remediation/tools/vault_yield.py report
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'remediation/evidence/vault-yield'
RPC = 'https://rpc.mainnet.chain.robinhood.com'
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'
ADAPTER = '0xadA73211711e4790bc83B5d6B39f47fE04D276f3'
GUARD = '0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741'
MARKETS = {'pNVDA': '0xa155ccCB986774AE818b3F10F07d01D1b7A47b26',
           'pUSDG': '0x55aEd0569c8f0D166D71facE57B57C2f2624a563'}
PAIR_LABEL = 'NVDA/USDG'
BLOCKS_PER_YEAR = 2_628_000          # L1-derived block number used by the rate model, NOT eth_blockNumber
SECONDS_PER_YEAR = 31_536_000
MIN_WINDOW_SECONDS = 3_600
DEFAULT_FROM_BLOCK = 60_000_000      # before the vault's first activity
LOG_STEP = 2_000_000
EVENTS = {
    'FeesProcessed': ('FeesProcessed(bytes32 indexed,uint256,uint256,uint256,uint256)',
                      ['stockFees', 'usdgFees', 'stockReserved', 'usdgReserved']),
    'PairCheckpoint': ('PairCheckpoint(bytes32 indexed,uint256,uint256,uint256,int256,uint256,uint256)',
                       ['stockAssets', 'usdgAssets', 'benchmarkUSDG', 'pnlUSDG', 'stockAccounted', 'usdgAccounted']),
    'LiquidityRebalanced': ('LiquidityRebalanced(bytes32 indexed,uint256,uint256,uint128)',
                            ['stockUsed', 'usdgUsed', 'liquidityAdded']),
}
SIGNED = {'pnlUSDG'}


def cast(*args, check=True):
    result = subprocess.run(['cast', *args, '--rpc-url', RPC], capture_output=True, text=True)
    if check and result.returncode:
        raise SystemExit(f'cast {" ".join(args)} failed: {result.stderr.strip()[:200]}')
    return result.stdout.strip()


def call_int(to, sig, *args, block=None):
    extra = ['--block', str(block)] if block is not None else []
    return int(cast('call', to, sig, *args, *extra).split()[0])


def pair_id():
    return subprocess.run(['cast', 'keccak', PAIR_LABEL], capture_output=True, text=True).stdout.strip()


def words(data):
    data = data[2:]
    return [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]


def signed(value):
    return value - (1 << 256) if value >> 255 else value


def prices():
    raw = cast('call', GUARD, 'pricesUSD18(bytes32)(uint256,uint256)', pair_id(), check=False)
    parts = raw.split()
    if len(parts) < 2:
        return None, None  # guard rejects (stale feed): record that, never invent a price
    return int(parts[0]), int(parts[2]) if len(parts) > 3 else int(parts[1])


def snapshot():
    OUT.mkdir(parents=True, exist_ok=True)
    pid = pair_id()
    block = int(cast('block-number'))
    ts = int(cast('block', str(block), '--field', 'timestamp'))
    ledger = cast('call', VAULT, 'ledger(bytes32)((uint256,uint256,uint256,uint256,uint256,uint256))', pid)
    stock_principal, usdg_principal, stock_idle, usdg_idle, loss, last_ckpt = [
        int(x.strip('()').split()[0]) for x in ledger.strip('()').split(',')]
    position = cast('call', ADAPTER, 'positionState(bytes32)', pid)
    pw = words(position) if position.startswith('0x') else [0, 0, 0, 0]
    stock_price, usdg_price = prices()
    row = {'timestamp': ts, 'block': block, 'stockPriceUsd18': stock_price, 'usdgPriceUsd18': usdg_price,
           'lp': {'tokenId': pw[0], 'liquidity': pw[1], 'stockAmount': pw[2], 'usdgAmount': pw[3]},
           'ledger': {'stockPrincipal': stock_principal, 'usdgPrincipal': usdg_principal,
                      'stockIdle': stock_idle, 'usdgIdle': usdg_idle, 'cumulativeLossUsd18': loss,
                      'lastCheckpoint': last_ckpt},
           'markets': {}}
    for name, market in MARKETS.items():
        row['markets'][name] = {
            'exchangeRateStored': call_int(market, 'exchangeRateStored()(uint256)'),
            'totalSupply': call_int(market, 'totalSupply()(uint256)'),
            'totalBorrows': call_int(market, 'totalBorrows()(uint256)'),
            'cash': call_int(market, 'getCash()(uint256)'),
            'supplyRatePerBlock': call_int(market, 'supplyRatePerBlock()(uint256)'),
            'borrowRatePerBlock': call_int(market, 'borrowRatePerBlock()(uint256)'),
            'vaultAccountedAssets': call_int(market, 'vaultAccountedAssets()(uint256)')}
    with (OUT / 'snapshots.jsonl').open('a') as handle:
        handle.write(json.dumps(row) + '\n')
    print(json.dumps({'snapshot': block, 'timestamp': ts, 'lpLiquidity': pw[1]}))


def decode_events(from_block):
    OUT.mkdir(parents=True, exist_ok=True)
    pid = pair_id()
    head = int(cast('block-number'))
    rows, stamps = [], {}
    for name, (sig, fields) in EVENTS.items():
        start = from_block
        while start <= head:
            end = min(start + LOG_STEP - 1, head)
            raw = cast('logs', '--address', VAULT, '--from-block', str(start), '--to-block', str(end),
                       '--json', sig, pid, check=False)
            for log in (json.loads(raw) if raw else []):
                block = int(log['blockNumber'], 16)
                if block not in stamps:
                    stamps[block] = int(cast('block', str(block), '--field', 'timestamp'))
                values = words(log['data'])
                row = {'event': name, 'block': block, 'timestamp': stamps[block], 'txHash': log['transactionHash']}
                row.update({f: (signed(v) if f in SIGNED else v) for f, v in zip(fields, values)})
                rows.append(row)
            start = end + 1
    rows.sort(key=lambda r: (r['block'], r['event']))
    (OUT / 'events.json').write_text(json.dumps(rows, indent=2) + '\n')
    columns = ['event', 'block', 'timestamp', 'txHash'] + sorted({k for r in rows for k in r} - {'event', 'block', 'timestamp', 'txHash'})
    with (OUT / 'events.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({'events': len(rows), 'fromBlock': from_block, 'toBlock': head}))
    return rows


def apy_from_rate(rate_per_block):
    apr = rate_per_block / 1e18 * BLOCKS_PER_YEAR
    return {'aprPercent': apr * 100, 'apyPercent': ((1 + apr / 365) ** 365 - 1) * 100}


def trailing(snaps, name, since_lp=False):
    series = [s for s in snaps if not since_lp or s['lp']['liquidity'] > 0]
    if len(series) < 2:
        return None
    old, new = series[0], series[-1]
    dt = new['timestamp'] - old['timestamp']
    if dt < MIN_WINDOW_SECONDS:
        return None
    growth = new['markets'][name]['exchangeRateStored'] / old['markets'][name]['exchangeRateStored']
    return {'windowSeconds': dt, 'windowHours': round(dt / 3600, 2), 'fromBlock': old['block'], 'toBlock': new['block'],
            'growthPercent': (growth - 1) * 100,
            'annualizedApyPercent': (growth ** (SECONDS_PER_YEAR / dt) - 1) * 100,
            'note': 'Realized exchange-rate growth, annualized. Short windows are noisy; show the window next to the number.'}


def report():
    snap_file = OUT / 'snapshots.jsonl'
    snaps = [json.loads(line) for line in snap_file.read_text().splitlines()] if snap_file.exists() else []
    if not snaps:
        raise SystemExit('No snapshots yet. Run `snapshot` first (and then on a schedule).')
    events_file = OUT / 'events.json'
    events = json.loads(events_file.read_text()) if events_file.exists() else []
    latest = snaps[-1]
    stock_price, usdg_price = latest['stockPriceUsd18'], latest['usdgPriceUsd18']

    def usd(stock_wei=0, usdg_raw=0):
        if stock_price is None or usdg_price is None:
            return None
        return stock_wei / 1e18 * stock_price / 1e18 + usdg_raw / 1e6 * usdg_price / 1e18

    fees = [e for e in events if e['event'] == 'FeesProcessed']
    checkpoints = [e for e in events if e['event'] == 'PairCheckpoint']
    totals = {'stockFeesWei': sum(e['stockFees'] for e in fees), 'usdgFeesRaw': sum(e['usdgFees'] for e in fees),
              'stockReservedWei': sum(e['stockReserved'] for e in fees), 'usdgReservedRaw': sum(e['usdgReserved'] for e in fees)}
    out = {
        'generatedAt': int(time.time()), 'latestSnapshotBlock': latest['block'], 'latestSnapshotTimestamp': latest['timestamp'],
        'vault': {
            'lpOpen': latest['lp']['liquidity'] > 0, 'lpLiquidity': latest['lp']['liquidity'],
            'ledger': latest['ledger'],
            'cumulativeLossUsd': latest['ledger']['cumulativeLossUsd18'] / 1e18,
            'feesLifetime': {**totals, 'valuedAtCurrentOraclePriceUsd': usd(totals['stockFeesWei'], totals['usdgFeesRaw']),
                             'eventCount': len(fees)},
            'checkpointCount': len(checkpoints),
            'lastCheckpointPnlUsd': (checkpoints[-1]['pnlUSDG'] / 1e18) if checkpoints else None,
            'note': 'The vault has no APY function. Realized supplier yield is the growth of the pToken exchange rate below.'},
        'markets': {}}
    for name in MARKETS:
        m = latest['markets'][name]
        out['markets'][name] = {
            'exchangeRate': m['exchangeRateStored'], 'totalBorrows': m['totalBorrows'], 'cash': m['cash'],
            'borrowInterestSupplyApy': apy_from_rate(m['supplyRatePerBlock']),
            'borrowApr': apy_from_rate(m['borrowRatePerBlock'])['aprPercent'],
            'trailingExchangeRateApy': trailing(snaps, name),
            'sinceLpOpenedApy': trailing(snaps, name, since_lp=True),
            'blocksPerYearUsed': BLOCKS_PER_YEAR}
    (OUT / 'frontend-yield.json').write_text(json.dumps(out, indent=2) + '\n')
    with (OUT / 'snapshots.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['block', 'timestamp', 'stock_price_usd18', 'usdg_price_usd18', 'lp_liquidity',
                         'stock_principal', 'usdg_principal', 'stock_idle', 'usdg_idle', 'cumulative_loss_usd18',
                         'pnvda_exchange_rate', 'pusdg_exchange_rate', 'pnvda_total_supply', 'pusdg_total_supply'])
        for sn in snaps:
            writer.writerow([sn['block'], sn['timestamp'], sn['stockPriceUsd18'], sn['usdgPriceUsd18'], sn['lp']['liquidity'],
                             sn['ledger']['stockPrincipal'], sn['ledger']['usdgPrincipal'], sn['ledger']['stockIdle'],
                             sn['ledger']['usdgIdle'], sn['ledger']['cumulativeLossUsd18'],
                             sn['markets']['pNVDA']['exchangeRateStored'], sn['markets']['pUSDG']['exchangeRateStored'],
                             sn['markets']['pNVDA']['totalSupply'], sn['markets']['pUSDG']['totalSupply']])
    print(json.dumps(out, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['snapshot', 'events', 'report'])
    parser.add_argument('--from-block', type=int, default=DEFAULT_FROM_BLOCK)
    args = parser.parse_args()
    {'snapshot': snapshot, 'events': lambda: decode_events(args.from_block), 'report': report}[args.command]()


if __name__ == '__main__':
    sys.exit(main())
