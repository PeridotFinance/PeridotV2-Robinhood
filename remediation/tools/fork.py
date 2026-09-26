"""Pin a fresh state block and run fork-only oracle migration regressions."""
import os
import argparse
import subprocess
from state import ROOT, rpc, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', action='store_true', help='Test the candidate vault upgrade instead of the installed oracle')
    args = parser.parse_args()
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    block = rpc('eth_getBlockByNumber', ['latest', False])
    pin = {'chainId': 4663, 'stateBlock': int(block['number'], 16), 'blockHash': block['hash'],
           'timestamp': int(block['timestamp'], 16),
           'scope': 'Read-only mainnet fork. Governance changes and synthetic account snapshots are local only.'}
    env = dict(os.environ, ROBINHOOD_RPC_URL='https://rpc.mainnet.chain.robinhood.com',
               REMEDIATION_FORK_BLOCK=str(pin['stateBlock']))
    env['FOUNDRY_PROFILE'] = 'vault_upgrade'
    prefix = 'vault-fork' if args.vault else 'fork'
    path = ROOT / ('remediation/evidence/' + prefix + '-tests.txt')
    test_path = '*/fork/VaultUpgradeMainnet.t.sol' if args.vault else '*/fork/LendingMainnet.t.sol'
    with path.open('w') as log:
        result = subprocess.run(['forge', 'test', '--match-path', test_path, '-vv'],
                                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    pin['exitCode'] = result.returncode
    pin['canonicalAfterTests'] = rpc('eth_getBlockByNumber', [block['number'], False])['hash'] == block['hash']
    pin['scope'] = 'Local-only vault implementation upgrade / state and market integration checks.' if args.vault else pin['scope']
    save(ROOT / ('remediation/evidence/' + prefix + '-pin.json'), pin)
    print(path.read_text())
    if result.returncode or not pin['canonicalAfterTests']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
