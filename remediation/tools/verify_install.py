"""Verify the installed adapter's runtime, immutable wiring, prices and paused state; read-only."""
import json
from state import ROOT, OLD_ORACLE, MARKETS, call, cast, rpc, read_state, save


def masked_runtime(code, references):
    data = bytearray.fromhex(code.removeprefix('0x'))
    for entries in references.values():
        for item in entries:
            start, length = item['start'], item['length']
            if length != 32 or start < 0 or start + length > len(data):
                raise RuntimeError('Unexpected immutable reference')
            data[start:start+length] = bytes(length)
    return bytes(data)


def verify_immutables(code, artifact, expected):
    names = {}
    def walk(node):
        if isinstance(node, dict):
            if node.get('nodeType') == 'VariableDeclaration' and node.get('mutability') == 'immutable':
                names[str(node['id'])] = node['name']
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(artifact['ast'])
    references = artifact['deployedBytecode']['immutableReferences']
    if {names.get(key) for key in references} != set(expected):
        raise RuntimeError('Unexpected immutable variable set')
    data = bytes.fromhex(code.removeprefix('0x'))
    for key, entries in references.items():
        value = int(expected[names[key]], 16).to_bytes(32, 'big')
        for item in entries:
            if data[item['start']:item['start'] + item['length']] != value:
                raise RuntimeError('Immutable runtime value differs: ' + names[key])


def main():
    state = read_state()
    if state['oracle'].lower() == OLD_ORACLE.lower():
        raise RuntimeError('Adapter not installed: controller still points to original oracle')
    if not state['seizePaused'] or not all(m['borrowPaused'] for m in state['markets'].values()):
        raise RuntimeError('Expected containment pauses during installation verification')
    tag = hex(state['block'])
    adapter = state['oracle']
    artifact_path = ROOT / 'remediation/out/RobinhoodLendingPriceAdapter.sol/RobinhoodLendingPriceAdapter.json'
    artifact = json.loads(artifact_path.read_text())
    bytecode = artifact['deployedBytecode']
    code = rpc('eth_getCode', [adapter, tag])
    refs = bytecode['immutableReferences']
    if masked_runtime(code, refs) != masked_runtime(bytecode['object'], refs):
        raise RuntimeError('Adapter runtime differs from locally compiled reviewed source')
    expected = {'source': OLD_ORACLE, 'stockMarket': MARKETS['pNVDA'], 'dollarMarket': MARKETS['pUSDG'],
                'stock': state['markets']['pNVDA']['underlying'], 'dollar': state['markets']['pUSDG']['underlying']}
    verify_immutables(code, artifact, expected)
    for getter, address in expected.items():
        actual = '0x' + call(adapter, getter + '()', block=tag)[-40:]
        if actual.lower() != address.lower():
            raise RuntimeError('Wrong immutable wiring: ' + getter)
    prices = {}
    for name, multiplier in (('pUSDG', 10**12), ('pNVDA', 1)):
        market = state['markets'][name]
        source_price = int(call(OLD_ORACLE, 'assetPrices(address)', market['underlying'], block=tag), 16)
        compatible = int(call(adapter, 'assetPrices(address)', market['underlying'], block=tag), 16)
        if not source_price or compatible != source_price or int(market['controllerOraclePrice']) != source_price * multiplier:
            raise RuntimeError('Wrong price units: ' + name)
        prices[name] = {'assetPriceUSD18': str(source_price), 'controllerPrice': market['controllerOraclePrice']}
    manifest = json.loads((ROOT / 'contracts/robinhood-vaults/frontend/margin-mainnet/manifest.json').read_text())
    guarded = manifest['marginAddresses']['guardedSource']
    backing = '0x' + call(guarded, 'lendingSource()', block=tag)[-40:]
    if backing.lower() != OLD_ORACLE.lower():
        raise RuntimeError('Margin backing source changed')
    incentive = int(call(state['controller'], 'liquidationIncentiveMantissa()', block=tag), 16)
    quotes = {}
    for debt_name, collateral_name, amount in (('pUSDG', 'pNVDA', 10**6), ('pNVDA', 'pUSDG', 10**16)):
        debt, collateral = state['markets'][debt_name], state['markets'][collateral_name]
        output = call(state['controller'], 'liquidateCalculateSeizeTokens(address,address,uint256)',
                      debt['address'], collateral['address'], amount, block=tag)[2:]
        err, shares = int(output[:64], 16), int(output[64:], 16)
        expected_shares = (amount * int(prices[debt_name]['assetPriceUSD18']) * incentive * 10**collateral['underlyingDecimals']
                           // (10**debt['underlyingDecimals'] * int(prices[collateral_name]['assetPriceUSD18']) * int(collateral['exchangeRateStored'])))
        # Controller fixed-point intermediate truncation is <1 share for these canary quote sizes.
        if err or shares == 0 or abs(shares - expected_shares) > 1:
            raise RuntimeError('Unexpected normalized liquidation quote: ' + debt_name)
        quotes[debt_name] = {'repayRaw': str(amount), 'collateralSharesRaw': str(shares), 'expectedSharesRaw': str(expected_shares)}
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != state['blockHash']:
        raise RuntimeError('Verification block changed')
    report = {'status': 'INSTALLED_AND_VERIFIED_PAUSED', 'chainId': 4663, 'block': state['block'],
              'blockHash': state['blockHash'], 'adapter': adapter, 'runtimeCodeHash': cast('keccak', code),
              'runtimeTemplateMatches': True, 'immutableWiring': expected, 'prices': prices,
              'liquidationQuotes': quotes, 'marginBackingSourceUnchanged': True, 'state': state,
              'scope': 'Runtime, immutable wiring, price units, quotes and pause flags. No unpause or fresh-price/depeg guarantee.'}
    save(ROOT / 'remediation/evidence/installed-adapter.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
