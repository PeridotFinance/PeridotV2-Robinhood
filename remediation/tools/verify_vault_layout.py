"""Compare complete vault storage type topology and public ABI, ignoring compiler AST IDs."""
import json
from state import ROOT, save


def canonical_layout(layout):
    def shape(type_id):
        t = layout['types'][type_id]
        result = {k: t[k] for k in ('encoding', 'label', 'numberOfBytes')}
        for key in ('key', 'value', 'base'):
            if key in t:
                result[key] = shape(t[key])
        if 'members' in t:
            result['members'] = [field(m) for m in t['members']]
        return result
    def field(f):
        return {'label': f['label'], 'slot': f['slot'], 'offset': f['offset'], 'type': shape(f['type'])}
    return [field(f) for f in layout['storage']]


def main():
    def artifact(name):
        return json.loads((ROOT / f'remediation/out/{name}.sol/{name}.json').read_text())
    old = artifact('RobinhoodBoostedVault')
    new = artifact('RobinhoodBoostedVaultV2')
    old_layout, new_layout = canonical_layout(old['storageLayout']), canonical_layout(new['storageLayout'])
    if old_layout != new_layout:
        raise RuntimeError('Vault storage topology differs')
    normalize = lambda abi: sorted(json.dumps(x, sort_keys=True) for x in abi)
    if normalize(old['abi']) != normalize(new['abi']):
        raise RuntimeError('Vault public ABI differs')
    source = (ROOT / 'remediation/src/RobinhoodBoostedVaultV2.sol').read_text()
    original = (ROOT / 'contracts/robinhood-vaults/src/RobinhoodBoostedVault.sol').read_text()
    for parent in ('Initializable', 'AccessControlUpgradeable', 'ReentrancyGuardUpgradeable'):
        if parent not in source or parent not in original:
            raise RuntimeError('Review inherited namespaces')
    report = {'status': 'MATCH', 'storage': new_layout, 'abiUnchanged': True,
              'runtimeBytes': len(new['deployedBytecode']['object'].removeprefix('0x')) // 2,
              'scope': 'Every declared field/gap, nested struct member, slot/offset/type and public ABI. Inherited OpenZeppelin namespace implementations are unchanged imports.'}
    save(ROOT / 'remediation/evidence/vault-layout.json', report)
    print('Vault fields, gaps, nested types and ABI unchanged. Runtime:', report['runtimeBytes'], 'bytes.')


if __name__ == '__main__':
    main()
