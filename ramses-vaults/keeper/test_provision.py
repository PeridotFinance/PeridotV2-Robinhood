"""provision.py with doctl and cast mocked: nothing reaches DigitalOcean or the chain and the real keystore is never read."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import provision

KEEPER = provision.WALLET['address']
VAULT = '0x2222222222222222222222222222222222222222'
ROUTER = '0x3333333333333333333333333333333333333333'
PAIR = '0x' + 'ab' * 32
APP_ID = 'c83f52ec-73b2-460a-acf5-b71e145752e2'


def spec_with(*envs):
    spec = provision.base_spec()
    spec['workers'][0]['envs'] += [provision.env(k, v) for k, v in envs]
    return spec


class FakeDoctl:
    """Records doctl calls; answers spec get / update / create-deployment. Captures the uploaded spec."""

    def __init__(self, spec):
        self.spec, self.calls, self.uploaded, self.spec_files = spec, [], None, []

    def __call__(self, *args, stdin=None):
        self.calls.append(args)
        if args[:3] == ('apps', 'spec', 'get'):
            return json.dumps(self.spec)
        if args[:2] == ('apps', 'update'):
            path = args[args.index('--spec') + 1]
            self.spec_files.append(path)
            self.mode = os.stat(path).st_mode & 0o777
            self.uploaded = json.loads(Path(path).read_text())
            return ''
        if args[:2] == ('apps', 'create-deployment'):
            return 'dep-123 PENDING_BUILD\n'
        raise AssertionError('unexpected doctl ' + repr(args))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        record = Path(self.tmp.name, 'app.json')
        record.write_text(json.dumps({'id': APP_ID, 'name': provision.APP_NAME}))
        # Point the wallet at paths that do not exist: an accidental read fails loudly instead of touching the real files.
        missing = Path(self.tmp.name, 'no-such-dir')
        for p in (mock.patch.object(provision, 'APP_RECORD', record),
                  mock.patch.dict(provision.WALLET, {'keystorePath': str(missing / 'keystore'),
                                                     'passwordPath': str(missing / 'password')}),
                  mock.patch.object(provision.subprocess, 'run', side_effect=AssertionError('no real subprocess')),
                  mock.patch('builtins.print')):
            p.start()
            self.addCleanup(p.stop)

    def keys(self, spec):
        return {e['key']: e for e in spec['workers'][0]['envs']}


class SetEnvs(Base):
    def test_replaces_removes_and_keeps(self):
        spec = spec_with(('RAMSES_VAULT', VAULT), ('OTHER', 'kept'))
        secret = {'key': 'KEYSTORE_PASSWORD', 'value': 'EV[1:abc:def]', 'scope': 'RUN_TIME', 'type': 'SECRET'}
        blob = {'key': 'KEYSTORE_JSON_BASE64', 'value': 'EV[1:ghi:jkl]', 'scope': 'RUN_TIME', 'type': 'SECRET'}
        spec['workers'][0]['envs'] += [secret, blob]
        provision.set_envs(spec, {'EXECUTE': provision.env('EXECUTE', 'true'),
                                  'NEW': provision.env('NEW', '1')}, remove=('RAMSES_VAULT',))
        k = self.keys(spec)
        self.assertEqual(k['EXECUTE']['value'], 'true')
        self.assertEqual(k['NEW']['value'], '1')
        self.assertNotIn('RAMSES_VAULT', k)
        self.assertEqual(k['OTHER']['value'], 'kept')
        self.assertEqual(k['KEYSTORE_PASSWORD'], secret)  # encrypted value passed back untouched
        self.assertEqual(k['KEYSTORE_JSON_BASE64'], blob)
        self.assertEqual([e['key'] for e in spec['workers'][0]['envs']].count('EXECUTE'), 1)

    def test_remove_secrets(self):
        spec = spec_with(('KEYSTORE_PASSWORD', 'EV[1:x:y]'), ('KEYSTORE_JSON_BASE64', 'EV[1:z:w]'), ('INTERVAL_SECONDS', '300'))
        provision.set_envs(spec, {}, remove=provision.SECRET_KEYS)
        k = self.keys(spec)
        self.assertNotIn('KEYSTORE_PASSWORD', k)
        self.assertNotIn('KEYSTORE_JSON_BASE64', k)
        self.assertIn('INTERVAL_SECONDS', k)


class Update(Base):
    def test_force_rebuild_and_temp_file_deleted(self):
        fake = FakeDoctl(provision.base_spec())
        with mock.patch.object(provision, 'doctl', fake):
            provision.update(provision.base_spec())
        self.assertEqual(fake.calls[0][:2], ('apps', 'update'))
        self.assertEqual(fake.calls[-1][:3], ('apps', 'create-deployment', APP_ID))
        self.assertIn('--force-rebuild', fake.calls[-1])
        self.assertEqual(fake.mode, 0o600)
        self.assertFalse(Path(fake.spec_files[0]).exists())

    def test_temp_file_deleted_when_update_fails(self):
        seen = []

        def failing(*args, stdin=None):
            seen.append(args[args.index('--spec') + 1])
            raise RuntimeError('doctl apps update failed')
        with mock.patch.object(provision, 'doctl', failing):
            with self.assertRaises(RuntimeError):
                provision.update(provision.base_spec())
        self.assertFalse(Path(seen[0]).exists())

    def test_disable_removes_secrets_and_rebuilds(self):
        fake = FakeDoctl(spec_with(('EXECUTE', 'true'), ('KEYSTORE_PASSWORD', 'EV[1:x:y]'),
                                   ('KEYSTORE_JSON_BASE64', 'EV[1:z:w]'), ('RAMSES_VAULT', VAULT)))
        with mock.patch.object(provision, 'doctl', fake):
            provision.cmd_disable()
        k = self.keys(fake.uploaded)
        self.assertEqual(k['EXECUTE']['value'], 'false')
        self.assertNotIn('KEYSTORE_PASSWORD', k)
        self.assertNotIn('KEYSTORE_JSON_BASE64', k)
        self.assertEqual(k['RAMSES_VAULT']['value'], VAULT)
        self.assertIn('--force-rebuild', fake.calls[-1])


class SetVault(Base):
    def journal(self, **over):
        path = Path(self.tmp.name, 'journal.json')
        path.write_text(json.dumps(dict({'chainId': 4663, 'keeper': KEEPER.lower(), 'vault': VAULT, 'pair': PAIR,
                                         'router': ROUTER}, **over)))
        return str(path)

    def test_refuses_other_keeper(self):
        fake = FakeDoctl(provision.base_spec())
        with mock.patch.object(provision, 'doctl', fake):
            with self.assertRaises(RuntimeError):
                provision.cmd_set_vault(self.journal(keeper='0x1111111111111111111111111111111111111111'))
        self.assertEqual(fake.calls, [])

    def test_refuses_other_chain(self):
        fake = FakeDoctl(provision.base_spec())
        for chain in (46630, 1, '4663', None):
            with mock.patch.object(provision, 'doctl', fake):
                with self.assertRaises(RuntimeError, msg=str(chain)):
                    provision.cmd_set_vault(self.journal(chainId=chain))
        self.assertEqual(fake.calls, [])

    def test_sets_addresses_and_rebuilds(self):
        fake = FakeDoctl(provision.base_spec())
        with mock.patch.object(provision, 'doctl', fake):
            provision.cmd_set_vault(self.journal())
        k = self.keys(fake.uploaded)
        self.assertEqual((k['RAMSES_VAULT']['value'], k['RAMSES_PAIR']['value'], k['RAMSES_ROUTER']['value']),
                         (VAULT, PAIR, ROUTER))
        self.assertEqual(k['EXECUTE']['value'], 'false')
        self.assertIn('--force-rebuild', fake.calls[-1])

    def test_refuses_foreign_app(self):
        spec = provision.base_spec()
        spec['name'] = 'peridot-range-keeper'
        fake = FakeDoctl(spec)
        with mock.patch.object(provision, 'doctl', fake):
            with self.assertRaises(RuntimeError):
                provision.cmd_set_vault(self.journal())
        self.assertFalse([c for c in fake.calls if c[:2] == ('apps', 'update')])


class Enable(Base):
    def fake_cast(self, roles=('KEEPER_ROLE',), balance=10 ** 16):
        hashes = {'KEEPER_ROLE': '0x' + '01' * 32, 'CONFIG_ROLE': '0x' + '02' * 32, 'GUARDIAN_ROLE': '0x' + '03' * 32,
                  'IN_KIND_OPERATOR_ROLE': '0x' + '04' * 32, 'DEFAULT_ADMIN_ROLE': '0x' + '00' * 32}
        held = {hashes[r] for r in roles}

        self._hashes = hashes
        patcher = mock.patch.object(provision, 'keccak', lambda name: hashes[name])
        patcher.start()
        self.addCleanup(patcher.stop)

        def run(*args):
            if args[0] == 'keccak':
                raise AssertionError('keccak must not go through cast() (that adds --rpc-url)')
            if args[0] == 'call':
                self.assertEqual(args[1:3], (VAULT, 'hasRole(bytes32,address)(bool)'))
                self.assertEqual(args[4], KEEPER)
                return 'true' if args[3] in held else 'false'
            if args[0] == 'balance':
                return str(balance)
            raise AssertionError('unexpected cast ' + repr(args))
        return run

    def run_enable(self, **cast_kwargs):
        fake = FakeDoctl(spec_with(('RAMSES_VAULT', VAULT), ('RAMSES_PAIR', PAIR), ('RAMSES_ROUTER', ROUTER)))
        with mock.patch.object(provision, 'doctl', fake), mock.patch.object(provision, 'cast', self.fake_cast(**cast_kwargs)):
            provision.cmd_enable()
        return fake

    def assert_refused(self, **cast_kwargs):
        fake = FakeDoctl(spec_with(('RAMSES_VAULT', VAULT)))
        with mock.patch.object(provision, 'doctl', fake), mock.patch.object(provision, 'cast', self.fake_cast(**cast_kwargs)):
            with self.assertRaises(RuntimeError) as ctx:
                provision.cmd_enable()
        self.assertFalse([c for c in fake.calls if c[:2] == ('apps', 'update')])
        return str(ctx.exception)

    def test_refuses_without_vault(self):
        fake = FakeDoctl(provision.base_spec())
        with mock.patch.object(provision, 'doctl', fake), mock.patch.object(provision, 'cast', self.fake_cast()):
            with self.assertRaises(RuntimeError):
                provision.cmd_enable()

    def test_keccak_runs_offline_without_rpc_url(self):
        seen = []
        def run(cmd, **kw):
            seen.append(cmd)
            return mock.Mock(returncode=0, stdout='0x' + 'ab' * 32 + '\n')
        with mock.patch.object(provision.subprocess, 'run', run):
            self.assertEqual(provision.keccak('KEEPER_ROLE'), '0x' + 'ab' * 32)
        self.assertEqual(seen, [['cast', 'keccak', 'KEEPER_ROLE']])

    def test_refuses_without_keeper_role(self):
        self.assertIn('KEEPER_ROLE', self.assert_refused(roles=()))

    def test_refuses_forbidden_roles(self):
        for role in ('DEFAULT_ADMIN_ROLE', 'CONFIG_ROLE', 'GUARDIAN_ROLE', 'IN_KIND_OPERATOR_ROLE'):
            self.assertIn(role, self.assert_refused(roles=('KEEPER_ROLE', role)))

    def test_refuses_without_gas(self):
        self.assertIn('fund', self.assert_refused(balance=provision.MIN_GAS_WEI - 1))

    def test_refuses_mismatched_local_keystore(self):
        provision.subprocess.run.side_effect = lambda args, **kw: subprocess.CompletedProcess(
            args, 0, '0x1111111111111111111111111111111111111111\n', '')
        self.assertIn('does not match', self.assert_refused())

    def test_uploads_secrets_with_fake_keystore(self):
        d = Path(self.tmp.name, 'signer')
        d.mkdir()
        (d / 'keystore').write_bytes(b'{"version":3,"crypto":{}}')
        (d / 'password').write_text('fake-password\n')
        provision.WALLET.update(keystorePath=str(d / 'keystore'), passwordPath=str(d / 'password'))
        provision.subprocess.run.side_effect = lambda args, **kw: subprocess.CompletedProcess(args, 0, KEEPER + '\n', '')
        fake = self.run_enable()
        k = self.keys(fake.uploaded)
        self.assertEqual(k['EXECUTE']['value'], 'true')
        self.assertEqual(k['KEYSTORE_PASSWORD'], {'key': 'KEYSTORE_PASSWORD', 'value': 'fake-password',
                                                  'scope': 'RUN_TIME', 'type': 'SECRET'})
        self.assertEqual(k['KEYSTORE_JSON_BASE64']['type'], 'SECRET')
        self.assertEqual(k['RAMSES_VAULT']['value'], VAULT)
        self.assertIn('--force-rebuild', fake.calls[-1])
        self.assertFalse(Path(fake.spec_files[0]).exists())
        import builtins
        printed = ' '.join(str(c) for c in builtins.print.call_args_list)  # builtins.print is mocked in Base
        self.assertNotIn('fake-password', printed)


class Main(Base):
    def test_usage(self):
        self.assertEqual(provision.main([]), 2)
        self.assertEqual(provision.main(['set-vault']), 2)
        self.assertEqual(provision.main(['enable', 'extra']), 2)
        self.assertEqual(provision.main(['bogus']), 2)


if __name__ == '__main__':
    unittest.main()
