"""Pin a fresh state block and run fork-only oracle migration regressions."""
import os
import subprocess
from state import ROOT, rpc, save


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    block = rpc('eth_getBlockByNumber', ['latest', False])
    pin = {'chainId': 4663, 'stateBlock': int(block['number'], 16), 'blockHash': block['hash'],
           'timestamp': int(block['timestamp'], 16),
           'scope': 'Read-only mainnet fork. Governance changes and synthetic account snapshots are local only.'}
    env = dict(os.environ, ROBINHOOD_RPC_URL='https://rpc.mainnet.chain.robinhood.com',
               REMEDIATION_FORK_BLOCK=str(pin['stateBlock']))
    path = ROOT / 'remediation/evidence/fork-tests.txt'
    with path.open('w') as log:
        result = subprocess.run(['forge', 'test', '--match-path', '*/fork/LendingMainnet.t.sol', '-vv'],
                                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    pin['exitCode'] = result.returncode
    pin['canonicalAfterTests'] = rpc('eth_getBlockByNumber', [block['number'], False])['hash'] == block['hash']
    save(ROOT / 'remediation/evidence/fork-pin.json', pin)
    print(path.read_text())
    if result.returncode or not pin['canonicalAfterTests']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
