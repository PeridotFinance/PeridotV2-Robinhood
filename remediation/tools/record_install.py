"""Archive independently checked public installation receipts; never read signing cache."""
import json
import sys
from state import ROOT, GOVERNOR, CONTROLLER, MARKETS, OLD_ORACLE, cast, save
sys.path.insert(0, str(ROOT / 'contracts/robinhood-vaults/margin-mainnet/tools'))
from rpc import rpc


def main():
    installed = json.loads((ROOT / 'remediation/evidence/installed-adapter.json').read_text())
    adapter = installed['adapter']
    broadcast = json.loads((ROOT / 'broadcast/InstallLendingPriceAdapter.s.sol/4663/run-latest.json').read_text())
    artifact = json.loads((ROOT / 'remediation/out/RobinhoodLendingPriceAdapter.sol/RobinhoodLendingPriceAdapter.json').read_text())
    creation = artifact['bytecode']['object'] + cast('abi-encode', 'f(address,address,address)',
                                                   OLD_ORACLE, MARKETS['pNVDA'], MARKETS['pUSDG'])[2:]
    setter = cast('calldata', '_setPriceOracle(address)', adapter)
    if int(rpc('eth_chainId', []), 16) != 4663 or len(broadcast['transactions']) != 2:
        raise RuntimeError('Unexpected chain or transaction count')
    records = []
    for index, entry in enumerate(broadcast['transactions']):
        tx_hash = entry['hash']
        tx = rpc('eth_getTransactionByHash', [tx_hash])
        receipt = rpc('eth_getTransactionReceipt', [tx_hash])
        if (not tx or not receipt or tx['from'].lower() != GOVERNOR.lower()
                or int(tx['chainId'], 16) != 4663 or int(tx['value'], 16) != 0
                or int(receipt['status'], 16) != 1):
            raise RuntimeError('Unconfirmed or unexpected installation transaction')
        block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
        if receipt['blockHash'] != block['hash']:
            raise RuntimeError('Receipt not canonical')
        if index == 0:
            if tx['to'] or tx['input'].lower() != creation.lower() or receipt['contractAddress'].lower() != adapter.lower():
                raise RuntimeError('Creation code/constructor/created address mismatch')
        elif tx['to'].lower() != CONTROLLER.lower() or tx['input'].lower() != setter.lower():
            raise RuntimeError('Oracle installation calldata mismatch')
        records.append({'action': 'deployAdapter' if index == 0 else 'setControllerOracle',
                        'hash': tx_hash, 'from': tx['from'], 'to': tx['to'],
                        'nonce': int(tx['nonce'], 16), 'valueWei': '0',
                        'inputKeccak': cast('keccak', tx['input']), 'receipt': receipt})
    save(ROOT / 'remediation/evidence/installation-transactions.json',
         {'chainId': 4663, 'adapter': adapter, 'transactions': records,
          'verificationBlock': installed['block'], 'scope': 'Canonical successful receipts and exact reviewed creation / installation calldata.'})
    print(json.dumps({'adapter': adapter, 'transactions': [r['hash'] for r in records]}, indent=2))


if __name__ == '__main__':
    main()
