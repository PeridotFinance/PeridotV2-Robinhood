"""Read-only receipt, event, runtime and pinned-state verification of lending reactivation."""
import json
from state import ROOT, GOVERNOR, CONTROLLER, MARKETS, read_state, call, cast, save
from record_vault_queue import VAULT, SLOT, rpc
from vault_state import PAIRS, GUARD, ADAPTER


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    journal = json.loads((ROOT / 'broadcast/ReactivateLending.s.sol/4663/run-latest.json').read_text())
    actions = [('Seize', None)] + [(action, market) for action in ('Borrow', 'Mint')
                                  for market in (MARKETS['pNVDA'], MARKETS['pUSDG'])]
    if len(journal['transactions']) != len(actions):
        raise RuntimeError('Expected exactly five reactivation transactions')
    records = []
    for entry, (action, market) in zip(journal['transactions'], actions):
        sig = '_set' + action + 'Paused(' + ('address,bool)' if market else 'bool)')
        args = [market, 'false'] if market else ['false']
        expected = cast('calldata', sig, *args)
        tx_hash = entry['hash']
        tx = rpc('eth_getTransactionByHash', [tx_hash])
        receipt = rpc('eth_getTransactionReceipt', [tx_hash])
        if (not tx or not receipt or int(receipt['status'], 16) != 1
                or tx['from'].lower() != GOVERNOR.lower()
                or tx['to'].lower() != CONTROLLER.lower() or int(tx['chainId'], 16) != 4663
                or int(tx['value'], 16) != 0 or tx['input'].lower() != expected.lower()
                or receipt['transactionHash'].lower() != tx_hash.lower()):
            raise RuntimeError('Unexpected transaction or unsuccessful receipt: ' + action)
        block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
        if block['hash'] != receipt['blockHash'] or tx['blockHash'] != receipt['blockHash']:
            raise RuntimeError('Noncanonical receipt: ' + action)
        topic = cast('keccak', 'ActionPaused(' + ('address,string,bool)' if market else 'string,bool)'))
        event_data = cast('abi-encode', 'f(' + ('address,string,bool)' if market else 'string,bool)'),
                          *([market, action, 'false'] if market else [action, 'false']))
        matches = [log for log in receipt['logs'] if log['address'].lower() == CONTROLLER.lower()
                   and log['topics'] == [topic] and log['data'].lower() == event_data.lower()]
        if len(matches) != 1:
            raise RuntimeError('Expected exact controller pause event: ' + action)
        record = {'action': action, 'market': market, 'hash': tx_hash, 'nonce': int(tx['nonce'], 16),
                  'inputKeccak': cast('keccak', tx['input']), 'timestamp': int(block['timestamp'], 16),
                  'block': int(receipt['blockNumber'], 16), 'index': int(receipt['transactionIndex'], 16),
                  'exactEventMatched': True, 'receipt': receipt}
        if records and ((record['block'], record['index']) <= (records[-1]['block'], records[-1]['index'])
                        or record['nonce'] <= records[-1]['nonce']):
            raise RuntimeError('Reactivation transaction order changed')
        records.append(record)

    state = read_state()
    if state['block'] < records[-1]['block']:
        raise RuntimeError('State predates execution')
    if state['seizePaused'] or any(m['borrowPaused'] or m['mintPaused'] for m in state['markets'].values()):
        raise RuntimeError('Resulting state remains paused')
    tag = hex(state['block'])
    preflight = json.loads((ROOT / 'contracts/robinhood-vaults/deployments/robinhood-mainnet.margin-preflight.json').read_text())
    controller_implementation = '0x' + call(CONTROLLER, 'peridottrollerImplementation()', block=tag)[-40:]
    if controller_implementation.lower() != preflight['contracts']['controllerImplementation']['address'].lower():
        raise RuntimeError('Controller implementation changed')
    for role in ('controller', 'controllerImplementation'):
        expected_code = preflight['contracts'][role]
        if cast('keccak', rpc('eth_getCode', [expected_code['address'], tag])) != expected_code['codeHash']:
            raise RuntimeError('Controller runtime changed: ' + role)
    prior_runtimes = json.loads((ROOT / 'remediation/evidence/runtime-check.json').read_text())
    delegate = prior_runtimes['runtimeChecks']['replacementDelegate']
    if cast('keccak', rpc('eth_getCode', [delegate['address'], tag])) != delegate['runtimeCodeHash']:
        raise RuntimeError('Market delegate runtime changed')
    for market in MARKETS.values():
        actual = '0x' + call(market, 'implementation()', block=tag)[-40:]
        if actual.lower() != delegate['address'].lower():
            raise RuntimeError('Market delegate target changed')
    oracle = '0xe4e03c2fdaef915ace705d106b2660b1e342a2e4'
    if (state['oracle'].lower() != oracle or cast('keccak', rpc('eth_getCode', [oracle, tag]))
            != '0x8a44437ef2c35e187c49f54aa92fcc3c51c3a30c1dc795cc713f610eaf353404'):
        raise RuntimeError('Oracle identity changed')
    implementation = '0x' + rpc('eth_getStorageAt', [VAULT, SLOT, tag])[-40:]
    if (implementation != '0x17f0cf262fbbf27e44756dba6d852815695e9c4a'
            or cast('keccak', rpc('eth_getCode', [implementation, tag]))
            != '0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84'):
        raise RuntimeError('Vault identity changed')
    def words(target, signature, *args):
        data = call(target, signature, *args, block=tag)[2:]
        return [int(data[i:i+64], 16) for i in range(0, len(data), 64)]
    manifest = json.loads((ROOT / 'contracts/robinhood-vaults/frontend/margin-mainnet/manifest.json').read_text())
    config = words(VAULT, 'pairConfig(bytes32)', PAIRS['production'])
    if not config[-4] or not config[-3]:
        raise RuntimeError('LP allocation or settlement unexpectedly enabled')
    prices = words(GUARD, 'pricesUSD18(bytes32)', PAIRS['production'])
    priceable = {name: bool(words(manifest['marginAddresses']['oracle'], 'marketPriceable(address)', m)[0])
                 for name, m in MARKETS.items()}
    risk = {}
    historical = json.loads((ROOT / 'contracts/robinhood-vaults/deployments/margin-mainnet-5x-live/status.json').read_text())
    for direction, pair in manifest['pairs'].items():
        risk[direction] = words(manifest['marginAddresses']['config'], 'getPairRisk(address,address,address)',
                                pair['marginPToken'], pair['positionPToken'], pair['debtPToken'])
        if risk[direction] != historical['directions'][direction]['risk']:
            raise RuntimeError('Directional risk limits changed')
    finality = {}
    for name in ('safe', 'finalized'):
        try:
            block = rpc('eth_getBlockByNumber', [name, False])
            finality[name] = {'block': int(block['number'], 16), 'hash': block['hash'],
                             'coversAllReceipts': int(block['number'], 16) >= records[-1]['block']}
        except (RuntimeError, TypeError, KeyError, ValueError):
            finality[name] = {'available': False}
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != state['blockHash']:
        raise RuntimeError('Evidence block changed')
    report = {'status': 'LENDING_REACTIVATED_AND_VERIFIED', 'chainId': 4663,
              'block': state['block'], 'blockHash': state['blockHash'], 'state': state,
              'oracleRuntimeMatches': True, 'vaultImplementation': implementation,
              'controllerImplementation': controller_implementation, 'controllerRuntimeMatches': True,
              'marketDelegate': delegate['address'], 'marketDelegateMatches': True,
              'vaultRuntimeMatches': True, 'guardPricesUSD18': list(map(str, prices)),
              'marginMarketPriceable': priceable, 'unchangedDirectionalRisk': risk,
              'allocationPaused': True, 'settlementSwapsPaused': True,
              'positionStateRaw': list(map(str, words(ADAPTER, 'positionState(bytes32)', PAIRS['production']))),
              'providerReportedFinality': finality, 'transactions': records,
              'scope': 'Exact successful canonical receipts/events, ordered actions and pinned resulting state from one public RPC. Provider safe/finalized tags are not independent L1 settlement proof. No live user lifecycle, margin fills, resumed LP activity or independent adoption is established.'}
    save(ROOT / 'remediation/evidence/reactivation-execution-2026-09-29.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('state', 'transactions')}, indent=2))


if __name__ == '__main__':
    main()
