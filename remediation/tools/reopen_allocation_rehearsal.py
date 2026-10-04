"""Rehearse the LP reopen sequence on a LOCAL fork of live state. No keys, no broadcasts."""
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
    native = int(rpc('eth_call', [{'to': probe, 'data': '0x'}, tag, {probe: {'code': '0x4360005260206000f3'}}]), 16)
    if not 0 < native <= int(tag, 16):
        raise RuntimeError('Unexpected native clock')
    log = ROOT / 'remediation/evidence/reopen-allocation-fork.txt'
    env = dict(os.environ, FOUNDRY_PROFILE='lending_candidate', ROBINHOOD_RPC_URL='https://rpc.mainnet.chain.robinhood.com',
               REMEDIATION_FORK_BLOCK=str(int(tag, 16)), REMEDIATION_NATIVE_BLOCK=str(native))
    with log.open('w') as output:
        result = subprocess.run(['forge', 'test', '--match-path', 'remediation/test/fork/ReopenAllocation.t.sol', '-vv', '--threads', '3'],
                                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    src = [ROOT / 'remediation/script/ReopenAllocation.s.sol', ROOT / 'remediation/test/fork/ReopenAllocation.t.sol']
    report = {'chainId': 4663, 'stateBlock': int(tag, 16), 'blockHash': block['hash'], 'nativeBlock': native,
              'timestamp': int(block['timestamp'], 16), 'exitCode': result.returncode,
              'canonicalAfterTests': rpc('eth_getBlockByNumber', [tag, False])['hash'] == block['hash'],
              'sourceSha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src},
              'logSha256': hashlib.sha256(log.read_bytes()).hexdigest(),
              'scope': 'Reopen sequence on a local fork of live state: no keys, signing or broadcasts. Not a mainnet change.'}
    save(ROOT / 'remediation/evidence/reopen-allocation-fork.json', report)
    log.with_suffix('.txt.sha256').write_text(report['logSha256'] + '  ' + log.name + '\n')
    text = log.read_text()
    print(text[text.find('Ran '):] if 'Ran ' in text else text[-4000:])
    if result.returncode or not report['canonicalAfterTests']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
