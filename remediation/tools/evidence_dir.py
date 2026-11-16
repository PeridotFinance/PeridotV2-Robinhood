"""Where remediation tools write and read evidence records.

Evidence is not committed (see remediation/evidence/README.md). New records go to
$PERIDOT_EVIDENCE_DIR, default .scratch/evidence/ at the repository root (git-ignored), and
then to the evidence store as a set with a README and SHA-256 manifest in the repository.
Dependency-free on purpose: tools that do not talk to the chain import it too.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(os.environ.get('PERIDOT_EVIDENCE_DIR', ROOT / '.scratch' / 'evidence'))


class MissingEvidence(FileNotFoundError):
    pass


def prior(path):
    """Return a prior record's path, or fail saying where it should come from."""
    path = Path(path)
    if not path.is_file():
        raise MissingEvidence(
            f'Prior evidence record not found: {path}. Evidence is no longer committed; copy it from its '
            'set in the evidence store (index: remediation/evidence/README.md) into PERIDOT_EVIDENCE_DIR '
            f'(currently {path.parent}).')
    return path


def writable(path):
    """Create the parent directory of a record about to be written, and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def require_explicit(action):
    """Signing paths refuse the scratch default: their 'already attempted' checks read prior intents."""
    if not os.environ.get('PERIDOT_EVIDENCE_DIR'):
        raise RuntimeError(
            f'{action} reads prior intents and journals to refuse repeats. Set PERIDOT_EVIDENCE_DIR to a '
            'directory holding the prior records from the evidence store (see remediation/evidence/README.md).')
