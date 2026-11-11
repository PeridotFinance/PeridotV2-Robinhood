#!/usr/bin/env python3
"""Fails unless RamsesBoostedVault's storage layout still matches what src/inkind/ depends on.

InKindExtension writes `_ledger` by raw slot (LEDGER_SLOT = 4) and its PairLedger field order. Storage is append-only, so the
whole recorded prefix must be unchanged: same labels, slots and offsets, in order. Run from the repository root:
    FOUNDRY_PROFILE=ramses python3 ramses-vaults/tools/check_inkind_layout.py
"""
import json
import subprocess
import sys

EXPECTED = [  # slot, offset, label: recorded 2026-11-06 from `forge inspect`
    (0, 0, 'oracleGuard'), (1, 0, 'lossReserve'), (2, 0, 'liquidityAdapter'), (3, 0, '_pairConfig'), (4, 0, '_ledger'),
    (5, 0, 'aggregateUsdgPrincipal'), (6, 0, 'aggregateUsdgDepositCap'), (7, 0, 'deployLossBps'), (8, 0, 'externalRewards'),
    (9, 0, 'rangePolicy'), (10, 0, 'rangeState'),
]
LEDGER_FIELDS = ['stockPrincipal', 'usdgPrincipal', 'stockIdle', 'usdgIdle']


def main():
    out = subprocess.run(['forge', 'inspect', 'ramses-vaults/src/RamsesBoostedVault.sol:RamsesBoostedVault', 'storageLayout', '--json'],
                         capture_output=True, text=True, check=True).stdout
    layout = json.loads(out)
    got = [(int(s['slot']), s['offset'], s['label']) for s in layout['storage']]
    if got[:len(EXPECTED)] != EXPECTED:
        sys.exit('storage prefix changed:\n expected %s\n got      %s' % (EXPECTED, got[:len(EXPECTED)]))
    ledger = next(s for s in layout['storage'] if s['label'] == '_ledger')
    value = layout['types'][ledger['type']]['value']
    members = layout['types'][value]['members']
    order = [(m['label'], int(m['slot'])) for m in members]
    want = [(name, i) for i, name in enumerate(LEDGER_FIELDS)]
    if order[:len(want)] != want:
        sys.exit('PairLedger field order changed: %s' % order)
    print('ok: %d recorded slots unchanged, %d appended; _ledger at slot 4 with fields %s' % (len(EXPECTED), len(got) - len(EXPECTED), want))


if __name__ == '__main__':
    main()
