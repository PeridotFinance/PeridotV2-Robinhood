"""Pinned read-only pair balances, reserve balances and oracle availability."""
from state import ROOT, call, rpc, save
from governance import VAULT

ADAPTER = '0xadA73211711e4790bc83B5d6B39f47fE04D276f3'
GUARD = '0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741'
RESERVE = '0x806b182B050f7EcF908758dD6bBF91DB8B2212aF'
PAIRS = {'production': '0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06',
         'historicalCanary': '0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee'}
STOCK = '0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC'
USDG = '0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168'


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    block = rpc('eth_getBlockByNumber', ['latest', False])
    tag = block['number']
    def words(target, signature, *args):
        data = call(target, signature, *args, block=tag)[2:]
        return [int(data[i:i+64], 16) for i in range(0, len(data), 64)]
    report = {'chainId': 4663, 'block': int(tag, 16), 'blockHash': block['hash'], 'pairs': {}}
    for name, pair in PAIRS.items():
        ledger = dict(zip(('stockPrincipal', 'usdgPrincipal', 'stockIdle', 'usdgIdle', 'cumulativeLossUSD18', 'lastCheckpoint'),
                          map(str, words(VAULT, 'ledger(bytes32)', pair))))
        config = words(VAULT, 'pairConfig(bytes32)', pair)
        item = {'pairId': pair, 'ledger': ledger,
                'stockAccount': '0x' + format(config[2], '040x'),
                'usdgAccount': '0x' + format(config[3], '040x'),
                'allocationPaused': bool(config[-4]), 'swapsPaused': bool(config[-3]), 'emergencyMode': bool(config[-2]),
                'positionStateRaw': list(map(str, words(ADAPTER, 'positionState(bytes32)', pair))),
                'availableReserveStockRaw': str(words(RESERVE, 'available(bytes32,address)', pair, STOCK)[0]),
                'availableReserveUSDGRaw': str(words(RESERVE, 'available(bytes32,address)', pair, USDG)[0])}
        try:
            item['guardPricesUSD18'] = list(map(str, words(GUARD, 'pricesUSD18(bytes32)', pair)))
            item['guardPriceAvailable'] = True
        except RuntimeError:
            item['guardPriceAvailable'] = False
        report['pairs'][name] = item
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != block['hash']:
        raise RuntimeError('Evidence block changed')
    save(ROOT / 'remediation/evidence/vault-state.json', report)
    import json
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
