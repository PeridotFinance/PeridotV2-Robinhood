"""Create or update the cloud Ramses keeper on DigitalOcean App Platform. Run locally by the operator.

`--deploy` (monitor-only, no secrets): creates/updates the app with EXECUTE=false.
`--enable` : uploads the dedicated wallet's encrypted keystore AND its password as RUN_TIME secrets and
sets EXECUTE=true. App operators of the cloud account can potentially read these; that is why the wallet
holds nothing but KEEPER_ROLE and a little gas. Refuses unless the wallet already holds KEEPER_ROLE,
holds no admin role, is funded, and the vault/adapter implementations are the reviewed ones.
Never prints secrets and never touches the governor key.
"""
import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'keeper'))
import worker  # noqa: E402

CONTEXT = 'ramses-keeper'
NAME = 'peridot-ramses-keeper'


def doctl(*args, payload=None):
    result = subprocess.run(['doctl', '--context', CONTEXT, '--http-retry-max', '0', *args],
                            input=payload, capture_output=True, text=True, timeout=180)
    if result.returncode:
        # API errors can echo submitted values; never print them.
        raise RuntimeError('DigitalOcean request failed; inspect the app before retrying')
    return result.stdout


def wallet():
    record = json.loads((ROOT / 'keeper' / 'wallet.json').read_text())
    return record['address']


def existing():
    apps = json.loads(doctl('apps', 'list', '-o', 'json') or '[]')
    found = [a for a in apps if a.get('spec', {}).get('name') == NAME]
    return found[0] if found else None


def base_spec(address):
    spec = json.loads((ROOT / 'keeper' / 'app.json').read_text().replace('__KEEPER_ADDRESS__', address))
    manifest = json.loads((ROOT / 'keeper' / 'manifest.json').read_text())
    worker.require(manifest['keeper'].lower() == address.lower(), 'Manifest keeper mismatch')
    set_env(spec['workers'][0], 'RAMSES_MANIFEST_JSON', json.dumps(manifest))
    return spec


def set_env(worker_spec, key, value, secret=False):
    envs = [e for e in worker_spec.get('envs', []) if e['key'] != key]
    envs.append({'key': key, 'value': value, 'scope': 'RUN_TIME', 'type': 'SECRET' if secret else 'GENERAL'})
    worker_spec['envs'] = envs


def deploy():
    address = wallet()
    spec = base_spec(address)
    app = existing()
    if app:
        # Every deployment returns to monitor mode; never silently re-enable or transfer old secrets.
        doctl('apps', 'update', app['id'], '--spec', '-', '-o', 'json', payload=json.dumps(spec))
        print('Updated ' + NAME + ' (monitor-only; review and enable separately).')
    else:
        out = json.loads(doctl('apps', 'create', '--spec', '-', '-o', 'json', payload=json.dumps(spec)))
        print('Created ' + NAME + ' (' + (out[0] if isinstance(out, list) else out)['id'] + '), monitor-only.')


def enable():
    address = wallet()
    manifest = json.loads((ROOT / 'keeper' / 'manifest.json').read_text())
    chain = worker.Chain(worker.RPC, address)
    if not worker.preflight(chain, manifest, execute=True):
        raise RuntimeError('wallet lacks KEEPER_ROLE')
    if chain.balance() < worker.MIN_GAS_WEI:
        raise RuntimeError('fund the wallet first (at least 0.0003 ETH, 0.002 recommended)')
    worker.require(chain.nonce() == chain.nonce('latest') == manifest['bootstrapNonce'], 'Signer already used: restore durable journal before enabling, do not reset bootstrap nonce')
    directory = ROOT / '.keeper-signer'
    key, secret = directory / 'keystore', directory / 'password'
    for path in (key, secret):
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise RuntimeError('Wallet files must be regular and owner-only')
    check = subprocess.run(['cast', 'wallet', 'address', '--keystore', str(key), '--password-file', str(secret)],
                           capture_output=True, text=True, timeout=60)
    if check.returncode or check.stdout.strip().lower() != address.lower():
        raise RuntimeError('Keystore does not match wallet.json')
    app = existing()
    if not app:
        raise RuntimeError('Run --deploy first')
    data = json.loads(key.read_bytes())
    if data.get('version') != 3 or not data.get('crypto'):
        raise RuntimeError('Expected an encrypted V3 keystore')
    data['address'] = address[2:].lower()
    spec = app['spec']
    w = spec['workers'][0]
    set_env(w, 'KEYSTORE_JSON_BASE64', base64.b64encode(json.dumps(data).encode()).decode(), secret=True)
    set_env(w, 'KEYSTORE_PASSWORD', secret.read_text(), secret=True)
    set_env(w, 'EXECUTE', 'true')
    doctl('apps', 'update', app['id'], '--spec', '-', '-o', 'json', payload=json.dumps(spec))
    print('Signer provisioned and EXECUTE=true. The governor wallet was not accessed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--deploy', action='store_true')
    group.add_argument('--enable', action='store_true')
    group.add_argument('--disable', action='store_true', help='set EXECUTE=false (keeps secrets; use --revoke-secrets via the console to delete)')
    args = parser.parse_args()
    if args.deploy:
        deploy()
    elif args.enable:
        enable()
    else:
        app = existing()
        if not app:
            raise RuntimeError('No app')
        spec = app['spec']
        set_env(spec['workers'][0], 'EXECUTE', 'false')
        doctl('apps', 'update', app['id'], '--spec', '-', '-o', 'json', payload=json.dumps(spec))
        print('EXECUTE=false: the worker only monitors.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Stopped: ' + (str(error) if type(error) is RuntimeError else type(error).__name__))
        raise SystemExit(1)
