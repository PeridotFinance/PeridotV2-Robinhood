"""Read-only current-mainnet rehearsal. Never accepts keys or broadcasts."""
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
    test = ROOT / 'remediation/test/fork/VaultSettlementMainnet.t.sol'
    log = ROOT / 'remediation/evidence/settlement-rehearsal-tests.txt'
    env = dict(os.environ, FOUNDRY_PROFILE='vault_upgrade',
               ROBINHOOD_RPC_URL='https://rpc.mainnet.chain.robinhood.com',
               REMEDIATION_FORK_BLOCK=str(int(tag, 16)), REMEDIATION_NATIVE_BLOCK=str(native))
    with log.open('w') as output:
        result = subprocess.run(['forge', 'test', '--match-path', str(test.relative_to(ROOT)), '-vv'],
                                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    pin = {'chainId': 4663, 'stateBlock': int(tag, 16), 'blockHash': block['hash'],
           'nativeBlock': native, 'timestamp': int(block['timestamp'], 16),
           'exitCode': result.returncode,
           'canonicalAfterTests': rpc('eth_getBlockByNumber', [tag, False])['hash'] == block['hash'],
           'testSha256': hashlib.sha256(test.read_bytes()).hexdigest(),
           'logSha256': hashlib.sha256(log.read_bytes()).hexdigest(),
           'scope': 'Installed-contract local fork only. Actual timelock scheduling/execution with a local time advance; no injected balances, oracle overrides, keys or mainnet transactions. Tests both withdrawal orders separately; success alone does not assert equal economics between orders.'}
    save(ROOT / 'remediation/evidence/settlement-rehearsal.json', pin)
    log.with_suffix(log.suffix + '.sha256').write_text(pin['logSha256'] + '  ' + log.name + '\n')
    print(log.read_text())
    if result.returncode or not pin['canonicalAfterTests']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
