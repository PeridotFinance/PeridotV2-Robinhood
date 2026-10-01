"""Require an exact reproduction of the vault candidate linked to the existing mainnet library."""
import json
from state import ROOT, cast

EXPECTED_RUNTIME = '0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84'


def main():
    archived = json.loads((ROOT / 'remediation/artifacts/RobinhoodBoostedVaultV2.json').read_text())
    built = json.loads((ROOT / 'remediation/out-vault-upgrade/RobinhoodBoostedVaultV2.sol/RobinhoodBoostedVaultV2.json').read_text())
    for key in ('bytecode', 'deployedBytecode'):
        if archived[key]['object'] != built[key]['object']:
            raise RuntimeError('Vault candidate artifact mismatch: ' + key)
    if archived['abi'] != built['abi']:
        raise RuntimeError('Vault candidate ABI mismatch')
    if cast('keccak', built['deployedBytecode']['object']) != EXPECTED_RUNTIME:
        raise RuntimeError('Unexpected linked vault runtime hash')
    print('Vault candidate linked creation/runtime (including metadata) and ABI reproduce exactly.')


if __name__ == '__main__':
    main()
