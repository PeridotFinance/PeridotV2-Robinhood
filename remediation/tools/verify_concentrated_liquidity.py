"""Read-only check that DEPLOYED concentrated-liquidity implementations equal the reviewed build.

Run after the queue stage and before the timelock executes, and again after execution. The expected
bytecode comes from the compiled `vault_v3` artifacts, never from the deployed contracts, so a
deployment that differs from the reviewed build cannot vouch for itself. The only value read from
chain is the address of the linked `RangeLib`, which is itself checked against its own artifact.

No signing, no broadcasting.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import subprocess

SETTLEMENT_LIB = '0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB'
EIP170 = 24576
OUT = 'remediation/out-vault-v3'
GOVERNOR = '0x94696d767e65a75581145646960FA0eC886cE5d2'


def cast(*args):
    return subprocess.check_output(['cast', *args], text=True).strip()


def load(name, source=None):
    path = ROOT / OUT / ((source or name) + '.sol') / (name + '.json')
    return json.loads(path.read_text())


def link_refs(artifact):
    refs = []
    for file, libs in artifact['deployedBytecode'].get('linkReferences', {}).items():
        for lib, spots in libs.items():
            refs += [(lib, s['start'], s['length']) for s in spots]
    return refs


def strip_metadata(code):
    """Solidity appends CBOR metadata whose last two bytes give its length."""
    length = int.from_bytes(code[-2:], 'big') + 2
    return code[:-length], code[-length:]


def fill(artifact, libraries):
    """Compiled runtime with every library placeholder replaced by the given address."""
    text = artifact['deployedBytecode']['object'].removeprefix('0x')
    for lib, start, length in link_refs(artifact):
        address = libraries[lib].removeprefix('0x').lower()
        assert len(address) == 2 * length
        text = text[:2 * start] + address + text[2 * (start + length):]
    return bytes.fromhex(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adapter-impl', required=True)
    parser.add_argument('--vault-impl', required=True)
    parser.add_argument('--rpc', default='https://rpc.mainnet.chain.robinhood.com')
    parser.add_argument('--evidence', default=None)
    args = parser.parse_args()

    adapter_art = load('UniswapV4PairedAdapterV3')
    vault_art = load('RobinhoodBoostedVaultV3')
    lib_art = load('RangeLib')
    adapter = cast('to-check-sum-address', args.adapter_impl)
    vault = cast('to-check-sum-address', args.vault_impl)
    chain = lambda a: bytes.fromhex(cast('code', a, '--rpc-url', args.rpc)[2:])
    on_adapter, on_vault = chain(adapter), chain(vault)

    # The RangeLib address is read from the vault's own link positions and must be consistent.
    spots = [(s, l) for lib, s, l in link_refs(vault_art) if lib == 'RangeLib']
    found = {on_vault[s:s + l].hex() for s, l in spots}
    checks = {'rangeLibLinkedConsistently': len(found) == 1 and len(spots) > 0}
    range_lib = cast('to-check-sum-address', '0x' + next(iter(found))) if found else None
    on_lib = chain(range_lib) if range_lib else b''
    expected_lib = bytes.fromhex(lib_art['deployedBytecode']['object'].removeprefix('0x'))
    # A library embeds its own address as an immutable (`library_deploy_address`): mask it and
    # check it separately.
    refs = lib_art['deployedBytecode'].get('immutableReferences', {}).get('library_deploy_address', [])
    masked_chain, masked_expected = bytearray(on_lib), bytearray(expected_lib)
    self_address_ok = bool(refs) and bool(range_lib)
    for ref in refs:
        start, length = ref['start'], ref['length']
        word = bytes(on_lib[start:start + length])
        self_address_ok = self_address_ok and word == bytes(length - 20) + bytes.fromhex(range_lib[2:])
        masked_chain[start:start + length] = bytes(length)
        masked_expected[start:start + length] = bytes(length)
    checks['rangeLibRuntimeMatches'] = self_address_ok and bytes(masked_chain) == bytes(masked_expected)
    libs = {'SettlementLib': SETTLEMENT_LIB, 'RangeLib': range_lib or '0x' + '00' * 20}
    expected_vault = fill(vault_art, libs)
    expected_adapter = fill(adapter_art, {})

    def compare(on_chain, expected, label):
        exact = on_chain == expected
        body_equal = strip_metadata(on_chain)[0] == strip_metadata(expected)[0]
        checks[label + 'RuntimeExact'] = exact
        checks[label + 'RuntimeEqualIgnoringMetadata'] = body_equal
        checks[label + 'WithinEip170'] = 0 < len(on_chain) <= EIP170

    compare(on_vault, expected_vault, 'vault')
    compare(on_adapter, expected_adapter, 'adapter')
    report = {
        'scope': 'Read-only verification of deployed implementations against the reviewed vault_v3 build',
        'adapterImplementation': adapter, 'vaultImplementation': vault, 'rangeLib': range_lib,
        'settlementLib': SETTLEMENT_LIB,
        'adapterRuntimeBytes': len(on_adapter), 'vaultRuntimeBytes': len(on_vault),
        'expectedVaultCodehash': cast('keccak', '0x' + expected_vault.hex()),
        'expectedAdapterCodehash': cast('keccak', '0x' + expected_adapter.hex()),
        'checks': checks}
    print(json.dumps(report, indent=2))
    required = [k for k in checks if not k.endswith('IgnoringMetadata')]
    ok = all(checks[k] for k in required)
    if args.evidence:
        Path(args.evidence).write_text(json.dumps(report, indent=2) + '\n')
    if not ok:
        raise SystemExit('VERIFICATION FAILED: ' + ', '.join(k for k in required if not checks[k]))
    print('Deployed implementations match the reviewed build exactly.')


if __name__ == '__main__':
    main()
