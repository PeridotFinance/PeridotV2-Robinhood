"""Create the dedicated range-keeper wallet as an encrypted Foundry keystore, locally.

Prints only the PUBLIC address. The random password and the keystore are written to the owner-only,
git-ignored directory `.range-keeper-signer/` and are never printed. This wallet must hold only
KEEPER_ROLE on the vault (granted through the timelock) and a little gas; never fund it with anything else.
"""
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.umask(0o077)
    directory = ROOT / '.range-keeper-signer'
    record = ROOT / 'keeper' / 'wallet.json'
    if directory.exists() or record.exists():
        raise RuntimeError('A range-keeper wallet already exists; inspect it, never overwrite')
    directory.mkdir(mode=0o700)
    password = secrets.token_urlsafe(48)
    secret = directory / 'password'
    secret.write_text(password)
    secret.chmod(0o600)
    made = subprocess.run(['cast', 'wallet', 'new', str(directory), 'keystore'],
                          env=dict(os.environ, CAST_PASSWORD=password), capture_output=True, text=True, timeout=60)
    if made.returncode:
        raise RuntimeError('Keystore creation failed; inspect the directory before retrying')
    key = directory / 'keystore'
    key.chmod(0o600)
    shown = subprocess.run(['cast', 'wallet', 'address', '--keystore', str(key), '--password-file', str(secret)],
                           capture_output=True, text=True, timeout=60)
    address = shown.stdout.strip()
    if shown.returncode or not (address.startswith('0x') and len(address) == 42):
        raise RuntimeError('Keystore verification failed')
    int(address[2:], 16)
    record.write_text(json.dumps({
        'address': address, 'chainId': 4663,
        'purpose': 'Range keeper: KEEPER_ROLE on the vault only, plus gas. No admin roles.',
        'keystorePath': '.range-keeper-signer/keystore', 'passwordPath': '.range-keeper-signer/password',
        'fundingTargetWei': str(2 * 10**15)}, indent=2) + '\n')
    print(json.dumps({'address': address, 'chainId': 4663, 'credentialsPrinted': False}, indent=2))


if __name__ == '__main__':
    main()
