"""Read-only check of a DEPLOYED LendingMintRouter against the compiled `lending_candidate` artifact.

The expected runtime is the compiled code with the two market immutables filled in, so a deployment
that differs from the reviewed source cannot vouch for itself. No signing or broadcasting.
"""
import argparse
import hashlib
import json

from verify_lending_delegate_candidate import ROOT, cast, load

PSTOCK = '0xa155ccCB986774AE818b3F10F07d01D1b7A47b26'
PUSDG = '0x55aEd0569c8f0D166D71facE57B57C2f2624a563'
ROUTER_JSON = 'LendingMintRouter'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--router', required=True)
    parser.add_argument('--rpc', default='https://rpc.mainnet.chain.robinhood.com')
    parser.add_argument('--tx', default=None)
    parser.add_argument('--out', default='remediation/out-lending-candidate')
    parser.add_argument('--evidence', default='remediation/evidence/lending-mint-router-deployment-check.json')
    args = parser.parse_args()
    art = json.loads((ROOT / args.out / f'{ROUTER_JSON}.sol/{ROUTER_JSON}.json').read_text())
    router = cast('to-check-sum-address', args.router)
    code = bytearray(bytes.fromhex(art['deployedBytecode']['object'].removeprefix('0x')))
    refs = art['deployedBytecode']['immutableReferences']
    # Two immutables (marketA, marketB). Match each to its value through the declaration order of the ABI getters.
    values = {}
    for ref_id, positions in refs.items():
        values[ref_id] = positions
    assert len(values) == 2, 'Expected exactly two immutables'
    on_chain = bytes.fromhex(cast('code', router, '--rpc-url', args.rpc)[2:])
    market_a = cast('call', router, 'marketA()(address)', '--rpc-url', args.rpc)
    market_b = cast('call', router, 'marketB()(address)', '--rpc-url', args.rpc)
    word = lambda a: bytes.fromhex(a.removeprefix('0x').lower().rjust(64, '0'))
    # Try both assignments of the two ids; exactly the (A, B) one must reproduce the chain code.
    ids = sorted(values)
    matched = False
    for first, second in ((ids[0], ids[1]), (ids[1], ids[0])):
        trial = bytearray(code)
        for pos in values[first]:
            trial[pos['start']:pos['start'] + 32] = word(market_a)
        for pos in values[second]:
            trial[pos['start']:pos['start'] + 32] = word(market_b)
        if bytes(trial) == on_chain:
            matched = True
            expected = bytes(trial)
    checks = {
        'hasCode': len(on_chain) > 0,
        'marketAIsPNVDA': market_a.lower() == PSTOCK.lower(),
        'marketBIsPUSDG': market_b.lower() == PUSDG.lower(),
        'runtimeMatchesCompiledWithImmutables': matched,
    }
    report = {'scope': 'Read-only verification of a deployed router; not an installation record',
              'router': router, 'runtimeBytes': len(on_chain), 'runtimeSha256': hashlib.sha256(on_chain).hexdigest(),
              'codehash': cast('codehash', router, '--rpc-url', args.rpc), 'checks': checks}
    if args.tx:
        tx = json.loads(cast('tx', args.tx, '--json', '--rpc-url', args.rpc))
        receipt = json.loads(cast('receipt', args.tx, '--json', '--rpc-url', args.rpc))
        creation_ok = tx['input'].lower().startswith('0x' + art['bytecode']['object'].removeprefix('0x').lower())
        checks['txIsCreationOfThisRouter'] = tx.get('to') in (None, '') and \
            (receipt.get('contractAddress') or '').lower() == router.lower()
        checks['txCreationInputStartsWithCompiledCreationCode'] = creation_ok
        checks['txSucceeded'] = receipt.get('status') in (1, '1', '0x1')
        report['transaction'] = {'hash': args.tx, 'from': tx['from'], 'block': receipt.get('blockNumber')}
    report['allChecksPassed'] = all(checks.values())
    path = ROOT / args.evidence
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    print(json.dumps(report, indent=2))
    if not report['allChecksPassed']:
        raise SystemExit('Router does not match the reviewed build; do not use it')


if __name__ == '__main__':
    main()
