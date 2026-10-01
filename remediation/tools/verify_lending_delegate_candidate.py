"""Offline release gates for a compiled, not deployed, lending delegate candidate.

Two builds are compared:

* ``lending_upgrade``  (anchor): the captured deployment's compiler settings. The frozen original
  must reproduce the installed bytecode here; the candidate does NOT fit EIP-170 under it.
* ``lending_candidate`` (release): identical except trailing metadata is omitted. The candidate
  must fit EIP-170 under it.

Nothing here signs, deploys or broadcasts. The anchor comparison against installed code uses
read-only RPC calls and ``cast`` for hashing.
"""
import argparse
import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EIP170 = 24576
LAST_DECLARED_SLOT = 28  # keep in step with UpgradeLendingDelegate.s.sol
ORIGINAL = 'RobinhoodBoostedDelegate'
CANDIDATE = 'RobinhoodBoostedDelegateV2'
FROZEN_SOURCE = ROOT / 'contracts/peridot-contracts-2-5/contracts/contracts/boosted/RobinhoodBoostedDelegate.sol'
CANDIDATE_SOURCE = ROOT / 'remediation/src/RobinhoodBoostedDelegateV2.sol'
INSTALLED_MARKETS = {'pNVDA': '0xa155ccCB986774AE818b3F10F07d01D1b7A47b26',
                     'pUSDG': '0x55aEd0569c8f0D166D71facE57B57C2f2624a563'}
INSTALLED_CODEHASH = '0xa6913bd52087e56926b3f17fd131b7f331af194aaa77321e452582e75fb8cc34'
ALLOWED_ABI_ADDITIONS = [{'type': 'error', 'name': 'ZeroSharesMinted', 'inputs': []}]
EXPECTED_IMMUTABLES = {'BORROW_ACCOUNTING_MODULE': 'PToken.sol', 'SELF': 'BorrowAccountingModule.sol'}


def load(out, name):
    return json.loads((out / f'{name}.sol/{name}.json').read_text())


def canonical_type(layout, key):
    item = layout['types'][key]
    result = {k: item[k] for k in ('encoding', 'label', 'numberOfBytes')}
    for name in ('key', 'value', 'base'):
        if name in item:
            result[name] = canonical_type(layout, item[name])
    if 'members' in item:
        result['members'] = [canonical_field(layout, field) for field in item['members']]
    return result


def canonical_field(layout, item):
    return {**{k: item[k] for k in ('label', 'slot', 'offset')},
            'type': canonical_type(layout, item['type'])}


def canonical_layout(artifact):
    layout = artifact['storageLayout']
    return [canonical_field(layout, f) for f in layout['storage']]


def normalized_abi(item):
    # internalType names Solidity contracts, not the external ABI.
    if isinstance(item, dict):
        return {k: normalized_abi(v) for k, v in item.items() if k != 'internalType'}
    if isinstance(item, list):
        return [normalized_abi(v) for v in item]
    return item


def runtime(artifact):
    return bytes.fromhex(artifact['deployedBytecode']['object'].removeprefix('0x'))


def immutable_shape(artifact):
    """Ids move between builds, so compare structure: how many immutables, and each one's slots."""
    refs = artifact['deployedBytecode'].get('immutableReferences', {})
    return sorted(sorted((r['length'] for r in v)) for v in refs.values())


def declared_immutables(artifact):
    """Name every `immutable` declaration reachable from the contract's source graph."""
    found = {}
    for source in artifact['metadata']['sources']:
        path = ROOT / source if not Path(source).is_absolute() else Path(source)
        if not path.exists():
            continue
        for match in re.finditer(r'\b(?:address|uint\d*|bytes\d*|bool|[A-Z]\w+)\s+(?:(?:private|internal|public)\s+)?'
                                 r'immutable\s+(\w+)', path.read_text()):
            found[match.group(1)] = path.name
    return found


def strip_cbor(code):
    length = int.from_bytes(code[-2:], 'big')
    return code[:len(code) - length - 2]


def fill(artifact, address):
    code = bytearray(runtime(artifact))
    word = bytes.fromhex(address.removeprefix('0x').lower().rjust(64, '0'))
    for refs in artifact['deployedBytecode'].get('immutableReferences', {}).values():
        for r in refs:
            code[r['start']:r['start'] + r['length']] = word
    return bytes(code)


def cast(*args):
    return subprocess.run(['cast', *args], capture_output=True, text=True, check=True).stdout.strip()


def anchor_against_installed(anchor_original, anchor_module, rpc):
    """Fill the compiled original's immutable with the installed module and compare to chain."""
    results = {}
    for label, market in INSTALLED_MARKETS.items():
        impl = cast('call', market, 'implementation()(address)', '--rpc-url', rpc)
        module = cast('compute-address', impl, '--nonce', '1').split()[-1]
        chain_impl = bytes.fromhex(cast('code', impl, '--rpc-url', rpc)[2:])
        chain_mod = bytes.fromhex(cast('code', module, '--rpc-url', rpc)[2:])
        comp_impl, comp_mod = fill(anchor_original, module), fill(anchor_module, module)
        results[label] = {
            'market': market, 'implementation': impl, 'implementationCodehash': cast('codehash', impl, '--rpc-url', rpc),
            'moduleAddress': module, 'runtimeBytes': len(chain_impl), 'moduleRuntimeBytes': len(chain_mod),
            'sameLength': len(chain_impl) == len(comp_impl) and len(chain_mod) == len(comp_mod),
            'bodyIdenticalExcludingMetadata': strip_cbor(chain_impl) == strip_cbor(comp_impl),
            'moduleBodyIdenticalExcludingMetadata': strip_cbor(chain_mod) == strip_cbor(comp_mod),
            'byteForByteIdentical': chain_impl == comp_impl and chain_mod == comp_mod,
            'codehashMatchesRecordedInstalled': cast('codehash', impl, '--rpc-url', rpc) == INSTALLED_CODEHASH,
        }
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--anchor-out', default='remediation/out-lending-upgrade')
    parser.add_argument('--out', default='remediation/out-lending-candidate')
    parser.add_argument('--rpc', default=None, help='Read-only RPC for the installed-bytecode anchor check')
    args = parser.parse_args()
    anchor_dir, release_dir = ROOT / args.anchor_out, ROOT / args.out
    a_orig, a_cand, a_mod = (load(anchor_dir, n) for n in (ORIGINAL, CANDIDATE, 'BorrowAccountingModule'))
    r_orig, r_cand, r_mod = (load(release_dir, n) for n in (ORIGINAL, CANDIDATE, 'BorrowAccountingModule'))
    delegator = load(release_dir, 'PErc20Delegator')

    # 1. Storage layout: identical in BOTH builds.
    for tag, o, c in (('anchor', a_orig, a_cand), ('release', r_orig, r_cand)):
        assert canonical_layout(o) == canonical_layout(c), f'Storage changed ({tag})'

    # The install script fingerprints slots 0..LAST_DECLARED_SLOT; the layout must not outgrow it.
    for art in (r_cand, a_cand, delegator):
        assert max(int(f['slot']) for f in art['storageLayout']['storage']) <= LAST_DECLARED_SLOT, \
            'Layout exceeds the slots the install script fingerprints'

    # 2. External surface: no selector added, none changed, none intercepted by the delegator.
    old_methods, new_methods = r_orig['methodIdentifiers'], r_cand['methodIdentifiers']
    assert all(new_methods.get(k) == v for k, v in old_methods.items()), 'Existing selector changed'
    selector_additions = {k: v for k, v in new_methods.items() if k not in old_methods}
    assert not selector_additions, f'Unexpected new selectors: {selector_additions}'
    assert len(set(new_methods.values())) == len(new_methods), 'Selector collision'

    # 3. ABI: everything old is preserved; the only addition is the new revert reason.
    old_abi, new_abi = normalized_abi(r_orig['abi']), normalized_abi(r_cand['abi'])
    assert all(entry in new_abi for entry in old_abi), 'Existing ABI changed'
    abi_additions = [entry for entry in new_abi if entry not in old_abi]
    assert abi_additions == ALLOWED_ABI_ADDITIONS, f'Unexpected ABI additions: {abi_additions}'

    # 4. Immutables. The inherited PToken constructor creates a BorrowAccountingModule and stores
    #    it in an immutable, so the ORIGINAL has one too: the candidate must have the same shape,
    #    the same declarations, and no library links.
    assert immutable_shape(r_orig) == immutable_shape(r_cand) == immutable_shape(a_orig), 'Immutable shape changed'
    assert len(immutable_shape(r_cand)) == 1, 'Expected exactly one delegate immutable'
    assert immutable_shape(r_mod) == immutable_shape(a_mod) and len(immutable_shape(r_mod)) == 1
    declared = declared_immutables(r_cand)
    assert declared == EXPECTED_IMMUTABLES, f'Unexpected immutable declarations: {declared}'
    assert declared_immutables(r_orig) == declared, 'Candidate immutables differ from the original'
    for art in (r_cand, a_cand):
        assert not art['deployedBytecode'].get('linkReferences'), 'Unreviewed library links'

    # 5. Compiler settings: the release build differs from the anchor ONLY by omitted metadata.
    def settings(art):
        s = dict(art['metadata']['settings'])
        s.pop('metadata', None)
        s.pop('compilationTarget', None)
        return s
    assert settings(r_cand) == settings(a_cand) == settings(r_orig), 'Compiler settings differ beyond metadata'
    metadata_settings = {'anchor': a_cand['metadata']['settings'].get('metadata'),
                         'release': r_cand['metadata']['settings'].get('metadata')}
    assert metadata_settings['release'] == {'bytecodeHash': 'none', 'appendCBOR': False}, 'Release must omit metadata'

    # 6. The module the delegate constructor deploys must be the same logic as the original's.
    assert strip_cbor(runtime(a_mod)) == runtime(r_mod), 'Module logic differs between builds'
    assert runtime(a_mod) == runtime(load(anchor_dir, 'BorrowAccountingModule'))

    # 7. Size. The candidate is NOT deployable under the anchor settings; it must fit the release build.
    sizes = {'original_anchor': len(runtime(a_orig)), 'candidate_anchor': len(runtime(a_cand)),
             'original_release': len(runtime(r_orig)), 'candidate_release': len(runtime(r_cand))}
    assert sizes['original_anchor'] == 24511, 'Anchor build no longer reproduces the captured size'
    within = sizes['candidate_release'] <= EIP170
    runtime_bytes = runtime(r_cand)

    # 8. Exact source delta against the frozen original.
    diff = ''.join(difflib.unified_diff(
        FROZEN_SOURCE.read_text().splitlines(True), CANDIDATE_SOURCE.read_text().splitlines(True),
        'contracts/.../boosted/RobinhoodBoostedDelegate.sol', 'remediation/src/RobinhoodBoostedDelegateV2.sol'))
    diff_path = ROOT / 'remediation/evidence/lending-delegate-candidate-source.diff'
    diff_path.write_text(diff)

    report = {
        'scope': 'Offline candidate release gates; no deployment or mainnet acceptance implied',
        'storageLayoutUnchanged': True, 'existingAbiPreserved': True,
        'newSelectors': [], 'abiAdditions': abi_additions,
        'immutables': {'declared': declared, 'delegateShape': immutable_shape(r_cand),
                       'sameAsOriginal': True,
                       'note': 'PToken creates a BorrowAccountingModule in its constructor and stores it as an '
                               'immutable; the original delegate has the identical immutable.'},
        'moduleLogicIdenticalAcrossBuilds': True,
        'runtimeBytes': sizes, 'eip170Limit': EIP170, 'withinEip170Limit': within,
        'headroomBytes': EIP170 - sizes['candidate_release'],
        'runtimeSha256': hashlib.sha256(runtime_bytes).hexdigest(),
        'creationCodeKeccak': cast('keccak', '0x' + r_cand['bytecode']['object'].removeprefix('0x')),
        'creationBytes': len(bytes.fromhex(r_cand['bytecode']['object'].removeprefix('0x'))),
        'candidateSourceSha256': hashlib.sha256(CANDIDATE_SOURCE.read_bytes()).hexdigest(),
        'frozenOriginalSha256': hashlib.sha256(FROZEN_SOURCE.read_bytes()).hexdigest(),
        'sourceDiffSha256': hashlib.sha256(diff.encode()).hexdigest(),
        'compilerMetadata': metadata_settings,
        'compiler': r_cand['metadata']['compiler'],
        'compilerSettings': r_cand['metadata']['settings'],
    }
    if args.rpc:
        # Separate file: the offline gates must not overwrite (or be overwritten by) the chain comparison.
        anchor = {'scope': 'Read-only: compiled original (lending_upgrade) versus installed delegate and module code',
                  'installed': anchor_against_installed(a_orig, a_mod, args.rpc)}
        for label, res in anchor['installed'].items():
            assert res['sameLength'] and res['bodyIdenticalExcludingMetadata'] \
                and res['moduleBodyIdenticalExcludingMetadata'], f'Anchor build does not reproduce installed {label}'
        apath = ROOT / 'remediation/evidence/lending-delegate-installed-anchor.json'
        apath.write_text(json.dumps(anchor, indent=2) + '\n')
        apath.with_suffix('.sha256').write_text(hashlib.sha256(apath.read_bytes()).hexdigest() + '  ' + apath.name + '\n')
        report['installedBytecodeAnchorFile'] = apath.name
    path = ROOT / 'remediation/evidence/lending-delegate-candidate-gates.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    diff_path.with_suffix('.diff.sha256').write_text(hashlib.sha256(diff_path.read_bytes()).hexdigest() + '  ' + diff_path.name + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('compilerSettings',)}, indent=2))
    if not within:
        raise SystemExit('Candidate exceeds EIP-170 size limit; not deployable')


if __name__ == '__main__':
    main()
