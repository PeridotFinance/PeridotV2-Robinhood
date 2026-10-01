"""Read-only preparation for the exact canary recovery signing command."""
import hashlib
import os
import subprocess
from state import ROOT, GOVERNOR, call, save
from record_vault_queue import rpc
from vault_state import VAULT, STOCK, USDG, PAIRS, ADAPTER, RESERVE


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    journal = ROOT / 'broadcast/RecoverCanaryResidue.s.sol/4663/run-latest.json'
    if journal.exists():
        raise RuntimeError('Existing signing journal: reconcile it before replacing preparation')
    block = rpc('eth_getBlockByNumber', ['latest', False]); tag = block['number']
    pin = {'chainId': 4663, 'stateBlock': int(tag, 16), 'blockHash': block['hash'],
           'timestamp': int(block['timestamp'], 16), 'governor': GOVERNOR, 'before': {}}
    for name, pair in PAIRS.items():
        pin['before'][name] = {key: call(target, signature, pair, *args, block=tag)
                              for key, target, signature, args in (
            ('ledger', VAULT, 'ledger(bytes32)', []), ('config', VAULT, 'pairConfig(bytes32)', []),
            ('position', ADAPTER, 'positionState(bytes32)', []),
            ('reserveStock', RESERVE, 'available(bytes32,address)', [STOCK]),
            ('reserveUSDG', RESERVE, 'available(bytes32,address)', [USDG]))}
    pin['before']['governorStock'] = int(call(STOCK, 'balanceOf(address)', GOVERNOR, block=tag), 16)
    pin['before']['vaultStock'] = int(call(STOCK, 'balanceOf(address)', VAULT, block=tag), 16)
    env = dict(os.environ, FOUNDRY_PROFILE='vault_upgrade')
    script = ROOT / 'remediation/script/RecoverCanaryResidue.s.sol'
    log = ROOT / 'remediation/evidence/canary-recovery-simulation.txt'
    with log.open('w') as output:
        result = subprocess.run(['forge', 'script', str(script.relative_to(ROOT))+':RecoverCanaryResidue',
                                 '--rpc-url', 'https://rpc.mainnet.chain.robinhood.com',
                                 '--fork-block-number', str(pin['stateBlock']), '--sender', GOVERNOR, '-vvv'],
                                cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    pin.update(exitCode=result.returncode,
               canonicalAfterSimulation=rpc('eth_getBlockByNumber', [tag, False])['hash'] == block['hash'],
               scriptSha256=hashlib.sha256(script.read_bytes()).hexdigest(),
               logSha256=hashlib.sha256(log.read_bytes()).hexdigest(),
               scope='Read-only exact user signing script simulation, both Foundry simulation phases retained; no keys or mainnet transactions.')
    save(ROOT / 'remediation/evidence/canary-recovery-simulation.json', pin)
    log.with_suffix(log.suffix+'.sha256').write_text(pin['logSha256']+'  '+log.name+'\n')
    print(log.read_text())
    if result.returncode or not pin['canonicalAfterSimulation']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
