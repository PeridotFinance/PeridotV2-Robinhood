"""Match the correction's compiled creation/runtime templates to its installed archive."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    archived = json.loads((ROOT / 'remediation/artifacts/RobinhoodLendingPriceAdapter.json').read_text())
    built = json.loads((ROOT / 'remediation/out/RobinhoodLendingPriceAdapter.sol/RobinhoodLendingPriceAdapter.json').read_text())
    for key in ('bytecode', 'deployedBytecode'):
        if archived[key]['object'] != built[key]['object']:
            raise RuntimeError('Installed adapter artifact mismatch: ' + key)
    if archived['abi'] != built['abi']:
        raise RuntimeError('Installed adapter ABI mismatch')
    print('Installed adapter creation/runtime templates (including metadata) and ABI match.')


if __name__ == '__main__':
    main()
