"""Read-only check of a DEPLOYED lending delegate candidate against the compiled artifacts.

Run after stage 1 (DeployLendingDelegate) and before stage 2. It prints the runtime codehash that
stage 2 must be given as EXPECTED_LENDING_DELEGATE_CODEHASH. That hash is derived from the compiled
`lending_candidate` artifact with the immutable filled in, never from the deployed contract, so a
deployment that differs from the reviewed build cannot vouch for itself.

No signing, no broadcasting. Only eth_getCode / eth_getTransactionByHash style reads via `cast`.
"""
import argparse
import hashlib
import json
from pathlib import Path

from verify_lending_delegate_candidate import ROOT, CANDIDATE, cast, fill, load

GOVERNOR = '0x94696d767e65a75581145646960FA0eC886cE5d2'
EIP170 = 24576


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--delegate', required=True)
    parser.add_argument('--rpc', default='https://rpc.mainnet.chain.robinhood.com')
    parser.add_argument('--tx', default=None, help='Deployment transaction hash (checks creation input and sender)')
    parser.add_argument('--out', default='remediation/out-lending-candidate')
    parser.add_argument('--evidence', default='remediation/evidence/lending-delegate-deployment-check.json')
    args = parser.parse_args()
    release = ROOT / args.out
    cand, module_art = load(release, CANDIDATE), load(release, 'BorrowAccountingModule')
    delegate = cast('to-check-sum-address', args.delegate)
    module = cast('compute-address', delegate, '--nonce', '1').split()[-1]

    on_chain = bytes.fromhex(cast('code', delegate, '--rpc-url', args.rpc)[2:])
    module_chain = bytes.fromhex(cast('code', module, '--rpc-url', args.rpc)[2:])
    expected = fill(cand, module)
    expected_module = fill(module_art, module)
    checks = {
        'delegateHasCode': len(on_chain) > 0,
        'withinEip170': len(on_chain) <= EIP170,
        'delegateRuntimeMatchesCompiledWithModuleImmutable': on_chain == expected,
        'moduleHasCode': len(module_chain) > 0,
        'moduleRuntimeMatchesCompiledWithSelfImmutable': module_chain == expected_module,
    }
    expected_hash = cast('keccak', '0x' + expected.hex())
    checks['onChainCodehashEqualsExpected'] = cast('codehash', delegate, '--rpc-url', args.rpc) == expected_hash
    report = {'scope': 'Read-only verification of a deployed candidate; not an installation record',
              'delegate': delegate, 'module': module, 'runtimeBytes': len(on_chain),
              'expectedRuntimeCodehash': expected_hash, 'checks': checks}
    if args.tx:
        tx = json.loads(cast('tx', args.tx, '--json', '--rpc-url', args.rpc))
        receipt = json.loads(cast('receipt', args.tx, '--json', '--rpc-url', args.rpc))
        creation = '0x' + load(release, CANDIDATE)['bytecode']['object'].removeprefix('0x')
        report['transaction'] = {
            'hash': args.tx, 'from': tx['from'], 'to': tx.get('to'),
            'creationInputMatchesCompiled': tx['input'].lower() == creation.lower(),
            'contractAddress': receipt.get('contractAddress'),
            'receiptStatus': receipt.get('status'), 'blockNumber': receipt.get('blockNumber')}
        t = report['transaction']
        checks['txFromGovernor'] = t['from'].lower() == GOVERNOR.lower()
        checks['txIsContractCreation'] = t['to'] in (None, '')
        checks['txCreationInputMatchesCompiled'] = t['creationInputMatchesCompiled']
        checks['txCreatedThisDelegate'] = (t['contractAddress'] or '').lower() == delegate.lower()
        checks['txSucceeded'] = t['receiptStatus'] in (1, '1', '0x1')
    ok = all(checks.values())
    report['allChecksPassed'] = ok
    path = ROOT / args.evidence
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    print(json.dumps(report, indent=2))
    if ok:
        print('\nStage 2 environment:')
        print(f'  export NEW_LENDING_DELEGATE={delegate}')
        print(f'  export EXPECTED_LENDING_DELEGATE_CODEHASH={expected_hash}')
    else:
        raise SystemExit('Deployment does not match the reviewed candidate; do not install')


if __name__ == '__main__':
    main()
