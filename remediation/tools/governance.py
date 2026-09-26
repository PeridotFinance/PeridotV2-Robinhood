"""Read-only inventory for the user-designated Safe; never transfers control."""
import json
from state import ROOT, GOVERNOR, CONTROLLER, MARKETS, OLD_ORACLE, call, cast, rpc, save

SAFE = '0x80f4207e0810EA2C39B6C8387E5ffC6FF34dfB12'
TIMELOCK = '0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498'
VAULT = '0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f'


def main():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    block = rpc('eth_getBlockByNumber', ['latest', False])
    tag = block['number']
    def read(target, signature, *args):
        return call(target, signature, *args, block=tag)
    def address(target, signature):
        return '0x' + read(target, signature)[-40:]
    safe = {'address': SAFE, 'codePresent': rpc('eth_getCode', [SAFE, tag]) != '0x'}
    if safe['codePresent']:
        safe['threshold'] = int(read(SAFE, 'getThreshold()'), 16)
        encoded = read(SAFE, 'getOwners()')[2:]
        words = [encoded[i:i+64] for i in range(0, len(encoded), 64)]
        start = int(words[0], 16) // 32
        count = int(words[start], 16)
        safe['owners'] = ['0x' + w[-40:] for w in words[start+1:start+1+count]]
        safe['quorumAtLeastTwo'] = 2 <= safe['threshold'] <= len(safe['owners'])
        safe['outgoingGovernorIsOwner'] = GOVERNOR.lower() in [a.lower() for a in safe['owners']]
    manifest = json.loads((ROOT / 'contracts/robinhood-vaults/frontend/margin-mainnet/manifest.json').read_text())
    margin = manifest['marginAddresses']
    owners = {}
    for name in ('config', 'flashVault', 'insuranceFund', 'feeDistributor', 'oracle', 'router'):
        owners[name] = {'address': margin[name], 'owner': address(margin[name], 'owner()')}
    admins = {'controller': {'address': CONTROLLER, 'admin': address(CONTROLLER, 'admin()')}}
    for name, market in MARKETS.items():
        admins[name] = {'address': market, 'admin': address(market, 'admin()'),
                        'pendingAdmin': address(market, 'pendingAdmin()')}
    # Owner slot 4 follows four mappings in the nonproxy StockSimplePriceOracle source.
    oracle = {'address': OLD_ORACLE,
              'ownerFromStorageSlot4': '0x' + rpc('eth_getStorageAt', [OLD_ORACLE, '0x4', tag])[-40:],
              'outgoingIsPriceAdmin': bool(int(read(OLD_ORACLE, 'admin(address)', GOVERNOR), 16)),
              'safeIsPriceAdmin': bool(int(read(OLD_ORACLE, 'admin(address)', SAFE), 16))}
    roles = {}
    for contract_name, target, names in (
        ('timelock', TIMELOCK, ('PROPOSER_ROLE', 'EXECUTOR_ROLE', 'CANCELLER_ROLE', 'DEFAULT_ADMIN_ROLE')),
        ('vault', VAULT, ('KEEPER_ROLE', 'GUARDIAN_ROLE', 'CONFIG_ROLE', 'DEFAULT_ADMIN_ROLE')),
    ):
        roles[contract_name] = {}
        for name in names:
            role = read(target, name + '()')
            roles[contract_name][name] = {'id': role,
                'outgoing': bool(int(read(target, 'hasRole(bytes32,address)', role, GOVERNOR), 16)),
                'safe': bool(int(read(target, 'hasRole(bytes32,address)', role, SAFE), 16)),
                'timelock': bool(int(read(target, 'hasRole(bytes32,address)', role, TIMELOCK), 16)),
                'openToEveryone': bool(int(read(target, 'hasRole(bytes32,address)', role, '0x' + '00'*20), 16))}
    admin_slot = hex(int(cast('keccak', 'eip1967.proxy.admin'), 16) - 1)
    proxy_admins = {}
    for name in ('config', 'executor', 'liquidator', 'riskEngine', 'marginVault', 'insuranceFund', 'feeDistributor'):
        admin = '0x' + rpc('eth_getStorageAt', [margin[name], admin_slot, tag])[-40:]
        proxy_admins[name] = {'proxy': margin[name], 'proxyAdmin': admin, 'owner': address(admin, 'owner()')}
    report = {'chainId': 4663, 'block': int(tag, 16), 'blockHash': block['hash'], 'safe': safe,
              'owners': owners, 'admins': admins, 'sourceOracle': oracle, 'roles': roles,
              'marginProxyAdmins': proxy_admins,
              'routerManager': address(margin['router'], 'manager()'),
              'outgoingRouterOperator': bool(int(read(margin['router'], 'operators(address)', GOVERNOR), 16)),
              'factoryExecutor': address(margin['accountFactory'], 'executor()'),
              'factoryConfigurator': address(margin['accountFactory'], 'configurator()'),
              'controllerPauseGuardian': address(CONTROLLER, 'pauseGuardian()'),
              'controllerBorrowCapGuardian': address(CONTROLLER, 'borrowCapGuardian()'),
              'scope': 'Known deployed roles and outgoing governor. Unenumerable oracle admins require transaction-history review. No transfers or funding performed.'}
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != block['hash']:
        raise RuntimeError('Inventory block changed')
    save(ROOT / 'remediation/evidence/governance-inventory.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
