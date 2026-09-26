"""Pause both lending borrows and ordinary seizure; user signs locally. Default: simulate only."""
import argparse
import fcntl
import json
import re
import subprocess
import sys
import time

from state import ROOT, GOVERNOR, CONTROLLER, MARKETS, call, cast, read_state, save
sys.path.insert(0, str(ROOT / 'contracts/robinhood-vaults/margin-mainnet/tools'))
from rpc import rpc, RPC

RECORD = ROOT / 'remediation/evidence/containment-transactions.json'


def plan():
    return [(f'pauseBorrow{name}', '_setBorrowPaused(address,bool)', [market, 'true'],
             'borrowGuardianPaused(address)', [market]) for name, market in MARKETS.items()] + [
                 ('pauseSeize', '_setSeizePaused(bool)', ['true'], 'seizeGuardianPaused()', [])]


def reconcile(item):
    if not item.get('hash'):
        raise RuntimeError('Ambiguous submission: reconcile the recorded sender nonce before retrying')
    for _ in range(30):
        receipt = rpc('eth_getTransactionReceipt', [item['hash']])
        if receipt:
            break
        time.sleep(1)
    if not receipt:
        raise RuntimeError('Pending receipt: rerun to reconcile the same hash')
    tx = rpc('eth_getTransactionByHash', [item['hash']])
    if (not tx or tx['from'].lower() != GOVERNOR.lower() or tx['to'].lower() != CONTROLLER.lower()
            or tx['input'].lower() != item['data'].lower() or int(tx['value'], 16) != 0
            or int(tx['nonce'], 16) != item['nonce'] or int(tx['chainId'], 16) != 4663):
        raise RuntimeError('Transaction identity mismatch')
    block = rpc('eth_getBlockByNumber', [receipt['blockNumber'], False])
    if receipt['blockHash'] != block['hash'] or int(receipt['status'], 16) != 1:
        raise RuntimeError('Failed or noncanonical receipt; inspect before retry')
    item.update(state='confirmed', receipt=receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--broadcast', action='store_true')
    args = parser.parse_args()
    state = read_state()
    if GOVERNOR.lower() not in (state['admin'].lower(), state['pauseGuardian'].lower()):
        raise RuntimeError('Governor is no longer authorized; use current governance')
    for name, signature, values, _, _ in plan():
        if int(call(CONTROLLER, signature, *values, sender=GOVERNOR), 16) != 1:
            raise RuntimeError('Pause simulation did not return true: ' + name)
    print(json.dumps({'chainId': 4663, 'sender': GOVERNOR, 'target': CONTROLLER,
                      'transactions': [{'action': p[0], 'data': cast('calldata', p[1], *p[2])} for p in plan()],
                      'effect': 'Pause new borrowing in both markets and ordinary collateral seizure. Repayment stays enabled.',
                      'broadcast': args.broadcast}, indent=2))
    if not args.broadcast:
        return
    if not sys.stdin.isatty():
        raise RuntimeError('Run --broadcast yourself in a terminal; Foundry prompts locally')
    # Share the original workspace's governor lock with its deployment runners.
    lock_path = ROOT.parent / 'deployments/.robinhood-deployer-signing.lock'
    if not lock_path.parent.is_dir():
        lock_path = ROOT / 'remediation/evidence/.governor-signing.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = json.loads(RECORD.read_text()) if RECORD.exists() else {'chainId': 4663, 'transactions': []}
        for item in record['transactions']:
            if item['state'] != 'confirmed':
                reconcile(item)
                save(RECORD, record)
        for name, signature, values, getter, getter_args in plan():
            if int(call(CONTROLLER, getter, *getter_args), 16) == 1:
                continue
            nonce = int(rpc('eth_getTransactionCount', [GOVERNOR, 'latest']), 16)
            if int(rpc('eth_getTransactionCount', [GOVERNOR, 'pending']), 16) != nonce:
                raise RuntimeError('Pending governor transaction; reconcile it first')
            data = cast('calldata', signature, *values)
            item = {'action': name, 'state': 'intent', 'nonce': nonce, 'data': data}
            record['transactions'].append(item)
            save(RECORD, record)
            result = subprocess.run(['cast', 'send', '--rpc-url', RPC, '--account', 'robinhood-deployer',
                                     '--from', GOVERNOR, '--chain', '4663', '--nonce', str(nonce),
                                     '--gas-limit', '200000', '--gas-price', '100000000',
                                     '--priority-gas-price', '0', '--async', CONTROLLER, '--data', data],
                                    stdout=subprocess.PIPE, text=True)
            tx_hash = result.stdout.strip()
            if result.returncode or not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx_hash):
                raise RuntimeError('Ambiguous submission; inspect saved nonce before retry')
            item.update(state='submitted', hash=tx_hash)
            save(RECORD, record)
            reconcile(item)
            save(RECORD, record)
            if int(call(CONTROLLER, getter, *getter_args), 16) != 1:
                raise RuntimeError('Receipt succeeded but pause state was not applied')
        final = read_state()
        if not final['seizePaused'] or not all(m['borrowPaused'] for m in final['markets'].values()):
            raise RuntimeError('Containment incomplete')
        save(ROOT / 'remediation/evidence/contained-state.json', final)
        print('Verified both borrow pauses and ordinary seizure pause on chain.')


if __name__ == '__main__':
    main()
