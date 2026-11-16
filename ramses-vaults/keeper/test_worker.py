"""worker.py without network: every `cast` call is scripted, nothing is sent and no real keystore is read."""
import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import worker

KEEPER = '0x9eaF3552449c26b95a55CcDc363258345DEA031E'
OTHER = '0x1111111111111111111111111111111111111111'
VAULT = '0x2222222222222222222222222222222222222222'
ROUTER = '0x3333333333333333333333333333333333333333'
PAIR = '0x' + 'ab' * 32
JOURNAL = {'vault': VAULT, 'pair': PAIR, 'router': ROUTER}


def fake_cast(chain_id='4663', codesize='1234', impl=ROUTER, balance=str(10 ** 16)):
    def run(rpc, *args):
        if args[0] == 'chain-id':
            return chain_id
        if args[0] == 'codesize':
            return codesize
        if args[0] == 'storage':
            assert args[2] == worker.IMPLEMENTATION_SLOT
            return '0x' + '00' * 12 + impl[2:].lower()
        if args[0] == 'balance':
            return balance
        raise AssertionError('unexpected cast ' + repr(args))
    return run


class ConfiguredJournal(unittest.TestCase):
    def test_unset_is_none(self):
        self.assertIsNone(worker.configured_journal({}))
        self.assertIsNone(worker.configured_journal({'RAMSES_VAULT': '', 'RAMSES_PAIR': '', 'RAMSES_ROUTER': ''}))

    def test_complete(self):
        env = {'RAMSES_VAULT': VAULT, 'RAMSES_PAIR': PAIR, 'RAMSES_ROUTER': ROUTER}
        self.assertEqual(worker.configured_journal(env), JOURNAL)

    def test_partial_is_error(self):
        for env in ({'RAMSES_VAULT': VAULT}, {'RAMSES_VAULT': VAULT, 'RAMSES_PAIR': PAIR},
                    {'RAMSES_PAIR': PAIR, 'RAMSES_ROUTER': ROUTER}, {'RAMSES_ROUTER': ROUTER}):
            with self.assertRaises(RuntimeError):
                worker.configured_journal(env)

    def test_malformed_is_error(self):
        good = {'RAMSES_VAULT': VAULT, 'RAMSES_PAIR': PAIR, 'RAMSES_ROUTER': ROUTER}
        for key, bad in (('RAMSES_VAULT', VAULT[:-1]), ('RAMSES_VAULT', 'x' + VAULT[1:]), ('RAMSES_ROUTER', ROUTER + '0'),
                         ('RAMSES_PAIR', PAIR[:-2]), ('RAMSES_PAIR', 'ab' * 33), ('RAMSES_PAIR', VAULT)):
            with self.assertRaises(RuntimeError, msg=key + '=' + bad):
                worker.configured_journal(dict(good, **{key: bad}))


class Readiness(unittest.TestCase):
    def test_unconfigured_waits_without_rpc(self):
        with mock.patch.object(worker, 'cast', side_effect=AssertionError('no rpc expected')):
            self.assertEqual(worker.readiness('rpc', None, KEEPER)[0], 'awaiting_vault')

    def test_no_code_waits(self):
        with mock.patch.object(worker, 'cast', fake_cast(codesize='0')):
            self.assertEqual(worker.readiness('rpc', JOURNAL, KEEPER), ('awaiting_vault', 'vault has no code yet'))

    def test_wrong_implementation_raises(self):
        with mock.patch.object(worker, 'cast', fake_cast(impl=OTHER)):
            with self.assertRaises(RuntimeError):
                worker.readiness('rpc', JOURNAL, KEEPER)

    def test_wrong_chain_raises(self):
        with mock.patch.object(worker, 'cast', fake_cast(chain_id='1')):
            with self.assertRaises(RuntimeError):
                worker.readiness('rpc', JOURNAL, KEEPER)

    def test_ready_with_case_insensitive_router(self):
        journal = dict(JOURNAL, router='0x' + 'AbCd' * 10)
        with mock.patch.object(worker, 'cast', fake_cast(impl='0x' + 'abcd' * 10)):
            self.assertEqual(worker.readiness('rpc', journal, KEEPER), ('ready', ''))


class FakeKeeper:
    """Stands in for ramses_keeper.Keeper inside the worker loop."""
    roles_answer = None
    steps = []

    def __init__(self, *args, **kwargs):
        pass

    def check_roles(self):
        return FakeKeeper.roles_answer

    def step(self, execute):
        FakeKeeper.steps.append(execute)
        return [], 0


class MainLoop(unittest.TestCase):
    def run_main(self, readiness_results, env=None, roles=None, balance=str(10 ** 16)):
        """Runs worker.main for len(readiness_results) cycles. Each result is a (state, detail) tuple or an exception."""
        emitted = []
        results = list(readiness_results)

        def readiness(rpc, journal, keeper):
            item = results.pop(0)
            if not results:
                worker.STOP = True  # finish this cycle, then leave the loop
            if isinstance(item, Exception):
                raise item
            return item

        FakeKeeper.roles_answer = roles
        FakeKeeper.steps = []
        base = {'KEEPER_ADDRESS': KEEPER, 'EXECUTE': 'false'}
        worker.STOP = False
        with mock.patch.dict(os.environ, dict(base, **(env or {})), clear=True), \
                mock.patch.object(worker, 'readiness', readiness), \
                mock.patch.object(worker, 'cast', fake_cast(balance=balance)), \
                mock.patch.object(worker, 'emit', lambda status, **f: emitted.append(dict(f, status=status))), \
                mock.patch.object(worker.rk, 'Keeper', FakeKeeper), \
                mock.patch.object(worker.signal, 'signal'), mock.patch.object(worker.os, 'umask'), \
                mock.patch.object(worker.time, 'sleep'):
            try:
                worker.main()
            finally:
                worker.STOP = False
        return emitted

    def test_awaiting_states_do_not_count_as_errors(self):
        env = {'RAMSES_VAULT': VAULT, 'RAMSES_PAIR': PAIR, 'RAMSES_ROUTER': ROUTER}
        emitted = self.run_main([('awaiting_vault', 'vault has no code yet'), RuntimeError('rpc read failed'),
                                 ('awaiting_vault', 'vault has no code yet'), RuntimeError('rpc read failed'),
                                 ('ready', ''), RuntimeError('rpc read failed')],
                                env=env, roles='missing KEEPER_ROLE')
        errors = [e for e in emitted if e['status'] == 'error']
        self.assertEqual([e['consecutive'] for e in errors], [1, 1, 1])
        beats = [e['state'] for e in emitted if e['status'] == 'heartbeat']
        self.assertEqual(beats, ['awaiting_vault', 'awaiting_vault', 'awaiting_role'])
        self.assertEqual(FakeKeeper.steps, [])  # never stepped without the role

    def test_consecutive_errors_accumulate_and_stop_at_ten(self):
        with self.assertRaises(RuntimeError):
            self.run_main([RuntimeError('rpc read failed')] * 10)

    def test_unconfigured_vault_heartbeats(self):
        emitted = self.run_main([('awaiting_vault', 'journal addresses not configured')] * 3)
        self.assertFalse([e for e in emitted if e['status'] == 'error'])
        self.assertEqual(emitted[0]['vaultConfigured'], False)
        self.assertEqual(len([e for e in emitted if e['status'] == 'heartbeat']), 3)

    def test_forbidden_role_is_an_error(self):
        env = {'RAMSES_VAULT': VAULT, 'RAMSES_PAIR': PAIR, 'RAMSES_ROUTER': ROUTER}
        emitted = self.run_main([('ready', '')] * 2, env=env, roles='keeper wallet holds CONFIG_ROLE; refusing')
        errors = [e for e in emitted if e['status'] == 'error']
        self.assertEqual([e['consecutive'] for e in errors], [1, 2])
        self.assertIn('CONFIG_ROLE', errors[0]['reason'])
        self.assertFalse([e for e in emitted if e['status'] == 'heartbeat'])
        self.assertEqual(FakeKeeper.steps, [])

    def test_ready_steps_dry_run(self):
        env = {'RAMSES_VAULT': VAULT, 'RAMSES_PAIR': PAIR, 'RAMSES_ROUTER': ROUTER}
        emitted = self.run_main([('ready', '')] * 2, env=env, roles=None)
        self.assertEqual(FakeKeeper.steps, [False, False])
        self.assertTrue(all(e['state'] == 'ready' for e in emitted if e['status'] == 'heartbeat'))

    def test_rpc_override_refused_for_remote_hosts(self):
        for url in ('https://evil.example', 'http://127.0.0.1.evil.example:8545', 'http://localhost.evil.example',
                    'http://localhost@evil.example', 'https://localhost', 'http://10.0.0.1:8545'):
            with self.assertRaises(RuntimeError, msg=url):
                self.run_main([('awaiting_vault', '')], env={'RPC_URL': url})

    def test_rpc_override_allowed_for_local_node(self):
        seen = []

        def readiness(rpc, journal, keeper):
            seen.append(rpc)
            worker.STOP = True
            return 'awaiting_vault', ''
        worker.STOP = False
        with mock.patch.dict(os.environ, {'KEEPER_ADDRESS': KEEPER, 'RPC_URL': 'http://127.0.0.1:8545'}, clear=True), \
                mock.patch.object(worker, 'readiness', readiness), mock.patch.object(worker, 'cast', fake_cast()), \
                mock.patch.object(worker, 'emit'), mock.patch.object(worker.signal, 'signal'), \
                mock.patch.object(worker.os, 'umask'), mock.patch.object(worker.time, 'sleep'):
            try:
                worker.main()
            finally:
                worker.STOP = False
        self.assertEqual(seen, ['http://127.0.0.1:8545'])

    def test_rpc_override_allows_localhost_name(self):
        emitted = self.run_main([('awaiting_vault', '')], env={'RPC_URL': 'http://localhost:8545'})
        self.assertEqual(emitted[0]['status'], 'started')

    def test_missing_keeper_address_refused(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(worker.signal, 'signal'), \
                mock.patch.object(worker.os, 'umask'):
            with self.assertRaises(RuntimeError):
                worker.main()


class PrepareSigner(unittest.TestCase):
    KEYSTORE = {'version': 3, 'crypto': {'cipher': 'aes-128-ctr'}, 'id': 'test'}

    def secrets(self, data=None):
        return {'KEYSTORE_JSON_BASE64': base64.b64encode(json.dumps(data or self.KEYSTORE).encode()).decode(),
                'KEYSTORE_PASSWORD': 'not-a-real-password'}

    def run_prepare(self, env, derived):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, derived + '\n', '')
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(worker.subprocess, 'run', run):
            try:
                return worker.prepare_signer(d, KEEPER), calls, dict(os.environ)
            finally:
                self.assertTrue(all(Path(d, n).stat().st_mode & 0o077 == 0 for n in os.listdir(d)))

    def test_rejects_other_address(self):
        with self.assertRaises(RuntimeError):
            self.run_prepare(self.secrets(), OTHER)

    def test_accepts_matching_address_and_clears_env(self):
        signer, calls, env_after = self.run_prepare(self.secrets(), KEEPER.lower())
        self.assertEqual(signer[0], '--keystore')
        self.assertEqual(calls[0][:3], ['cast', 'wallet', 'address'])
        self.assertNotIn('KEYSTORE_JSON_BASE64', env_after)
        self.assertNotIn('KEYSTORE_PASSWORD', env_after)

    def test_rejects_missing_or_unencrypted(self):
        with self.assertRaises(RuntimeError):
            self.run_prepare({}, KEEPER)
        with self.assertRaises(RuntimeError):
            self.run_prepare(self.secrets({'version': 3, 'privateKey': '00'}), KEEPER)


if __name__ == '__main__':
    unittest.main()
