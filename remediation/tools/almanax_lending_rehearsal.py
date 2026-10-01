"""Read-only diagnostic fork tests for the three HIGH Almanax claims."""
import hashlib
import os
import subprocess
from record_vault_queue import rpc
from state import ROOT, save


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    block = rpc('eth_getBlockByNumber', ['latest', False])
    tag = block['number']
    probe = '0x000000000000000000000000000000000000dead'
    native = int(rpc('eth_call', [{'to': probe, 'data': '0x'}, tag,
                                 {probe: {'code': '0x4360005260206000f3'}}]), 16)
    if not 0 < native <= int(tag, 16):
        raise RuntimeError('Unexpected native clock')
    test = ROOT / 'remediation/test/fork/AlmanaxLendingMainnet.t.sol'
    log = ROOT / 'remediation/evidence/almanax-lending-tests.txt'
    env = dict(os.environ, ROBINHOOD_RPC_URL='https://rpc.mainnet.chain.robinhood.com',
               REMEDIATION_FORK_BLOCK=str(int(tag, 16)), REMEDIATION_NATIVE_BLOCK=str(native))
    with log.open('w') as output:
        result = subprocess.run(['forge', 'test', '--match-path', str(test.relative_to(ROOT)), '-vv'],
                                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    report = {'chainId': 4663, 'stateBlock': int(tag, 16), 'blockHash': block['hash'],
              'nativeBlock': native, 'timestamp': int(block['timestamp'], 16),
              'exitCode': result.returncode,
              'canonicalAfterTests': rpc('eth_getBlockByNumber', [tag, False])['hash'] == block['hash'],
              'testSha256': hashlib.sha256(test.read_bytes()).hexdigest(),
              'logSha256': hashlib.sha256(log.read_bytes()).hexdigest(),
              'scope': 'Local fork of installed markets. Zero-price tests mock the oracle locally; mint tests use actual governor balances and installed code without funding or oracle overrides. Passing dust-mint test confirms a defect; it does not mean that defect is fixed. No signing or mainnet transactions.'}
    save(ROOT / 'remediation/evidence/almanax-lending-rehearsal.json', report)
    log.with_suffix('.txt.sha256').write_text(report['logSha256'] + '  ' + log.name + '\n')
    print(log.read_text())
    if result.returncode or not report['canonicalAfterTests']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
