"""Fixed-chain read-only state for the September 2026 remediation."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from verify_mainnet import rpc, cast

GOVERNOR = '0x94696d767e65a75581145646960FA0eC886cE5d2'
CONTROLLER = '0x6148183676e304dbe63a85c350c208da3ceac39c'
OLD_ORACLE = '0x266f014d1325774f1190f963df4369e07dda1d33'
MARKETS = {'pUSDG': '0x55aed0569c8f0d166d71face57b57c2f2624a563',
           'pNVDA': '0xa155cccb986774ae818b3f10f07d01d1b7a47b26'}


def call(target, signature, *args, block='latest', sender=None):
    tx = {'to': target, 'data': cast('calldata', signature, *map(str, args))}
    if sender:
        tx['from'] = sender
    return rpc('eth_call', [tx, block])


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')


def read_state():
    if int(rpc('eth_chainId', []), 16) != 4663:
        raise RuntimeError('Wrong chain')
    block = rpc('eth_getBlockByNumber', ['latest', False])
    tag = block['number']
    def uint(target, signature, *args):
        return int(call(target, signature, *args, block=tag), 16)
    def address(target, signature):
        return '0x' + call(target, signature, block=tag)[-40:]
    result = {'chainId': 4663, 'block': int(tag, 16), 'blockHash': block['hash'],
              'timestamp': int(block['timestamp'], 16), 'controller': CONTROLLER,
              'admin': address(CONTROLLER, 'admin()'),
              'pauseGuardian': address(CONTROLLER, 'pauseGuardian()'),
              'oracle': address(CONTROLLER, 'oracle()'),
              'seizePaused': bool(uint(CONTROLLER, 'seizeGuardianPaused()')), 'markets': {}}
    for name, market in MARKETS.items():
        asset = address(market, 'underlying()')
        result['markets'][name] = {
            'address': market, 'underlying': asset,
            'underlyingDecimals': uint(asset, 'decimals()'), 'pTokenDecimals': uint(market, 'decimals()'),
            'admin': address(market, 'admin()'),
            'borrowPaused': bool(uint(CONTROLLER, 'borrowGuardianPaused(address)', market)),
            'mintPaused': bool(uint(CONTROLLER, 'mintGuardianPaused(address)', market)),
            'totalBorrowsRaw': str(uint(market, 'totalBorrows()')),
            'totalSupplyRaw': str(uint(market, 'totalSupply()')),
            'localCashRaw': str(uint(asset, 'balanceOf(address)', market)),
            'exchangeRateStored': str(uint(market, 'exchangeRateStored()')),
            'controllerOraclePrice': str(uint(result['oracle'], 'getUnderlyingPrice(address)', market)),
        }
    if rpc('eth_getBlockByNumber', [tag, False])['hash'] != block['hash']:
        raise RuntimeError('Evidence block changed')
    return result


if __name__ == '__main__':
    report = read_state()
    save(ROOT / 'remediation/evidence/current-state.json', report)
    print(json.dumps(report, indent=2))
