import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worker as w  # noqa: E402

KEEPER = '0x' + '11' * 20


class PreflightTests(unittest.TestCase):
    def _patched(self, impls=(w.EXPECTED_VAULT_IMPL, w.EXPECTED_ADAPTER_IMPL), roles=None, chain='4663'):
        roles = roles or {}
        vault_impl, adapter_impl = impls

        def fake_cast(*args):
            if args[0] == 'chain-id':
                return chain
            if args[0] == 'storage':
                return '0x' + '0' * 24 + (vault_impl if args[1] == w.VAULT else adapter_impl)[2:].lower()
            if args[0] == 'call' and args[2] == '(bytes32)' if False else False:
                return ''
            if args[0] == 'call' and args[2].startswith('hasRole'):
                return 'true' if roles.get(args[3], False) else 'false'
            if args[0] == 'call':
                return '0x' + args[2].encode().hex().ljust(64, '0')[:64]  # role id derived from the getter name
            raise AssertionError(args)

        return mock.patch.multiple(w, cast=mock.DEFAULT, checksum=mock.DEFAULT, **{}) if False else self._multi(fake_cast)

    def _multi(self, fake_cast):
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(w, 'cast', side_effect=fake_cast))
        stack.enter_context(mock.patch.object(w, 'checksum', side_effect=lambda a: a))
        return stack

    def _role(self, name):
        return '0x' + (name + '()(bytes32)').encode().hex().ljust(64, '0')[:64]

    def test_monitor_mode_is_allowed_without_the_role(self):
        with self._patched():
            self.assertFalse(w.preflight(KEEPER, execute=False))

    def test_execute_requires_keeper_role(self):
        with self._patched():
            with self.assertRaisesRegex(RuntimeError, 'KEEPER_ROLE'):
                w.preflight(KEEPER, execute=True)

    def test_execute_with_keeper_role_passes(self):
        with self._patched(roles={self._role('KEEPER_ROLE'): True}):
            self.assertTrue(w.preflight(KEEPER, execute=True))

    def test_wallet_with_admin_power_is_refused_even_in_monitor_mode(self):
        for name in ('CONFIG_ROLE', 'GUARDIAN_ROLE'):
            with self._patched(roles={self._role('KEEPER_ROLE'): True, self._role(name): True}):
                with self.assertRaisesRegex(RuntimeError, name):
                    w.preflight(KEEPER, execute=True)
        with self._patched(roles={self._role('KEEPER_ROLE'): True, '0x' + '00' * 32: True}):
            with self.assertRaisesRegex(RuntimeError, 'DEFAULT_ADMIN_ROLE'):
                w.preflight(KEEPER, execute=True)

    def test_unexpected_implementation_stops_sending(self):
        other = '0x' + 'ab' * 20
        with self._patched(impls=(other, w.EXPECTED_ADAPTER_IMPL), roles={self._role('KEEPER_ROLE'): True}):
            with self.assertRaisesRegex(RuntimeError, 'vault implementation'):
                w.preflight(KEEPER, execute=True)
        with self._patched(impls=(w.EXPECTED_VAULT_IMPL, other), roles={self._role('KEEPER_ROLE'): True}):
            with self.assertRaisesRegex(RuntimeError, 'adapter implementation'):
                w.preflight(KEEPER, execute=True)

    def test_wrong_chain_is_refused(self):
        with self._patched(chain='1'):
            with self.assertRaisesRegex(RuntimeError, 'wrong chain'):
                w.preflight(KEEPER, execute=False)


class SignerTests(unittest.TestCase):
    def _env(self, keystore, password='pw'):
        return {'KEYSTORE_JSON_BASE64': base64.b64encode(json.dumps(keystore).encode()).decode(),
                'KEYSTORE_PASSWORD': password}

    def test_unprovisioned_signer_is_refused(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'not provisioned'):
                w.prepare_signer(d, KEEPER)

    def test_non_v3_keystore_is_refused(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, self._env({'version': 1}), clear=True):
            with self.assertRaisesRegex(RuntimeError, 'V3'):
                w.prepare_signer(d, KEEPER)

    def test_secrets_are_removed_from_the_environment_and_files_are_owner_only(self):
        env = self._env({'version': 3, 'crypto': {'cipher': 'x'}})
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(w.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=KEEPER + '\n')):
                key, secret = w.prepare_signer(d, KEEPER)
            self.assertNotIn('KEYSTORE_PASSWORD', os.environ)
            self.assertNotIn('KEYSTORE_JSON_BASE64', os.environ)
            for path in (key, secret):
                self.assertEqual(os.stat(path).st_mode & 0o077, 0)

    def test_keystore_for_another_address_is_refused(self):
        env = self._env({'version': 3, 'crypto': {'cipher': 'x'}})
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(w.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='0x' + '22' * 20 + '\n')):
                with self.assertRaisesRegex(RuntimeError, 'does not belong'):
                    w.prepare_signer(d, KEEPER)


class CycleTests(unittest.TestCase):
    def test_nothing_is_sent_when_unfunded_or_monitor_only(self):
        with mock.patch.object(w.rk, 'step', return_value=0) as step:
            w.cycle(KEEPER, execute=True, funded=False)
            step.assert_called_once_with(False)
        with mock.patch.object(w.rk, 'step', return_value=0) as step:
            w.cycle(KEEPER, execute=False, funded=True)
            step.assert_called_once_with(False)
        with mock.patch.object(w.rk, 'step', return_value=0) as step:
            w.cycle(KEEPER, execute=True, funded=True)
            step.assert_called_once_with(True)

    def test_keeper_address_is_required(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'KEEPER_ADDRESS'):
                w.main()


if __name__ == '__main__':
    unittest.main()
