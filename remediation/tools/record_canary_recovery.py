"""Read-only verification of the two user-signed canary residue recovery calls."""
import json
from state import ROOT, GOVERNOR, call, cast, save
from record_vault_queue import rpc, SLOT
from vault_state import VAULT, STOCK, USDG, PAIRS, ADAPTER, RESERVE

RESIDUE = 24_697_449_583


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def main():
    require(int(rpc('eth_chainId', []), 16) == 4663, 'Wrong chain')
    pin = json.loads((ROOT / 'remediation/evidence/canary-recovery-simulation.json').read_text())
    source = ROOT / 'broadcast/RecoverCanaryResidue.s.sol/4663/run-latest.json'
    archive = ROOT / 'remediation/evidence/canary-recovery-broadcast.json'
    journal = json.loads((archive if archive.exists() else source).read_text())
    require(len(journal['transactions']) == 2, 'Expected exactly checkpoint and withdrawal')
    records = []
    deadline = None
    for i, entry in enumerate(journal['transactions']):
        require(entry.get('hash'), 'Unsigned or interrupted attempt; inspect before retrying')
        tx = rpc('eth_getTransactionByHash', [entry['hash']])
        receipt = rpc('eth_getTransactionReceipt', [entry['hash']])
        require(tx and receipt and int(receipt['status'], 16) == 1, 'Missing successful receipt')
        require(tx['from'].lower() == GOVERNOR.lower() and tx['to'].lower() == VAULT.lower()
                and int(tx['chainId'], 16) == 4663 and int(tx['value'], 16) == 0
                and receipt['transactionHash'] == entry['hash'], 'Wrong transaction identity')
        signature = ('checkpoint(bytes32,uint256)' if i == 0
                     else 'withdrawForSide(bytes32,address,uint256,address,uint256)')
        params = json.loads(cast('decode-calldata', signature, tx['input'], '--json'))
        if i == 0:
            deadline = int(params[-1])
            args = [PAIRS['historicalCanary'], str(deadline)]
        else:
            args = [PAIRS['historicalCanary'], STOCK, str(RESIDUE), GOVERNOR, str(deadline)]
            require(int(tx['nonce'], 16) == records[0]['nonce'] + 1, 'Nonce gap or reordered calls')
        require(tx['input'].lower() == cast('calldata', signature, *args).lower(), 'Wrong calldata')
        block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
        require(receipt['blockHash'] == tx['blockHash'] == block['hash'], 'Noncanonical receipt')
        require(int(block['number'], 16) >= pin['stateBlock'], 'Receipt predates preparation')
        if i == 0:
            require(int(block['timestamp'], 16) <= deadline <= int(block['timestamp'], 16) + 300,
                    'Checkpoint deadline outside configured window')
            signature = 'PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)'
            topics = [cast('keccak', signature), PAIRS['historicalCanary']]
            matches = [l for l in receipt['logs'] if l['address'].lower() == VAULT.lower()
                       and l['topics'] == topics]
            require(len(matches) == 1, 'Missing exact canary checkpoint event')
            data = matches[0]['data'][2:]
            words = [int(data[j:j+64], 16) for j in range(0, len(data), 64)]
            require(len(words) == 6 and words[:3] == [RESIDUE, 0, 0]
                    and 0 < words[3] < 2**255 and words[4:] == [RESIDUE, 0], 'Wrong residue attribution')
        else:
            signature = 'Withdrawal(bytes32,address,address,uint256,uint256,uint256)'
            topics = [cast('keccak', signature), PAIRS['historicalCanary'],
                      '0x' + STOCK[2:].lower().zfill(64), '0x' + GOVERNOR[2:].lower().zfill(64)]
            expected = cast('abi-encode', 'f(uint256,uint256,uint256)', str(RESIDUE), str(RESIDUE), '0')
            require(sum(l['address'].lower() == VAULT.lower() and l['topics'] == topics
                        and l['data'] == expected for l in receipt['logs']) == 1,
                    'Missing exact canary withdrawal event')
            transfer_topics = [cast('keccak', 'Transfer(address,address,uint256)'),
                               '0x' + VAULT[2:].lower().zfill(64), '0x' + GOVERNOR[2:].lower().zfill(64)]
            require(sum(l['address'].lower() == STOCK.lower() and l['topics'] == transfer_topics
                        and int(l['data'], 16) == RESIDUE for l in receipt['logs']) == 1,
                    'Missing exact underlying token transfer')
        records.append({'hash': entry['hash'], 'nonce': int(tx['nonce'], 16), 'receipt': receipt})
    block = rpc('eth_getBlockByNumber', ['latest', False]); tag = block['number']
    require(int(tag, 16) >= int(records[-1]['receipt']['blockNumber'], 16), 'Post-state behind receipt')
    implementation = '0x' + rpc('eth_getStorageAt', [VAULT, SLOT, tag])[-40:]
    require(cast('keccak', rpc('eth_getCode', [implementation, tag])) ==
            '0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84', 'Implementation changed')
    current = {}
    for name, pair in PAIRS.items():
        item = {key: call(target, sig, pair, *args, block=tag) for key, target, sig, args in (
            ('ledger', VAULT, 'ledger(bytes32)', []), ('config', VAULT, 'pairConfig(bytes32)', []),
            ('position', ADAPTER, 'positionState(bytes32)', []),
            ('reserveStock', RESERVE, 'available(bytes32,address)', [STOCK]),
            ('reserveUSDG', RESERVE, 'available(bytes32,address)', [USDG]))}
        for key in ('config', 'position', 'reserveStock', 'reserveUSDG'):
            require(item[key] == pin['before'][name][key], 'Changed pair config, position or reserve: ' + name)
        if name == 'production':
            require(item['ledger'] == pin['before'][name]['ledger'], 'Production ledger changed; review intervening activity')
        else:
            require(int(item['ledger'][2:2+64*4], 16) == 0, 'Canary principals or idle remain')
            require(item['ledger'][2+64*4:2+64*5] == pin['before'][name]['ledger'][2+64*4:2+64*5],
                    'Canary cumulative loss changed')
        current[name] = item
    owner_balance = int(call(STOCK, 'balanceOf(address)', GOVERNOR, block=tag), 16)
    vault_balance = int(call(STOCK, 'balanceOf(address)', VAULT, block=tag), 16)
    require(owner_balance == pin['before']['governorStock'] + RESIDUE
            and vault_balance + RESIDUE == pin['before']['vaultStock'],
            'Balance delta differs from preparation; review intervening transfers')
    require(rpc('eth_getBlockByNumber', [tag, False])['hash'] == block['hash'], 'Evidence block changed')
    save(archive, journal)
    save(ROOT / 'remediation/evidence/canary-recovery-verified.json', {
        'chainId': 4663, 'block': int(tag, 16), 'blockHash': block['hash'], 'verified': True,
        'returnedStockRaw': RESIDUE, 'receiver': GOVERNOR, 'after': current, 'transactions': records,
        'scope': 'Exact canonical receipts/events and pinned post-state. Balances and production/reserve state compared to recorded preparation; unrelated intervening activity requires separate review. Single-RPC L2 proof; no production settlement, LP reopening or L1 finality claim.'})
    print('CANARY RECOVERY VERIFIED: exact residue returned; production and reserves unchanged')


if __name__ == '__main__':
    main()
