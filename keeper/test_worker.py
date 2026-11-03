import base64
import contextlib
import io
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


class FakeChain:
    """The contract's ordering for this pair, driven through the real range_keeper.step."""
    STOCK_PRICE = 205 * 10 ** 18
    GAS_PRICE = 20_000_000  # 0.02 gwei
    ETH_ANSWER = 2_000 * 10 ** 8  # so one rebalance costs 750k * 0.02 gwei * $2000 = $0.03

    def __init__(self, now=1_800_000_000, oracle_ok=True, recenter_due=False, stock_idle=10 ** 18, usdg_idle=0):
        self.now, self.oracle_ok, self.recenter_due = now, oracle_ok, recenter_due
        self.stock_idle, self.usdg_idle = stock_idle, usdg_idle
        self.last_checkpoint = now
        self.sent = []
        self.send_output = '{"status":"0x1"}'
        self.send_code = 0
        self.broken_read = None
        self.reads = []

    def simulate(self, fn='recenter'):
        if fn == 'recenter':
            if self.recenter_due:
                return 'due', False, ''
            return ('RecenterNotNeeded', True, '') if self.oracle_ok else ('StaleOracle', True, 'x')
        if fn == 'rebalance':
            if self.now - self.last_checkpoint > 3600:
                return 'CheckpointStale', False, ''
            return 'due', False, ''
        return ('due', False, '') if self.oracle_ok else ('StaleOracle', True, 'x')

    def send(self, fn='recenter'):
        self.sent.append(fn)
        if fn == 'checkpoint' and self.send_code == 0:
            self.last_checkpoint = self.now
        result = mock.Mock(returncode=self.send_code, stdout=self.send_output, stderr='')
        result.elapsed = 1
        return result

    def cast(self, *args, check=True):
        key = args[1] if args[0] == 'call' else args[0]
        self.reads.append(key)
        if self.broken_read and self.broken_read in ' '.join(args):
            raise RuntimeError('rpc failed')
        if args[0] == 'gas-price':
            return mock.Mock(stdout=str(self.GAS_PRICE) + '\n')
        if 'ledger(' in args[2]:
            row = [1, 2, self.stock_idle, self.usdg_idle, 3, self.last_checkpoint]
            return mock.Mock(stdout=''.join(str(v) + ' [1e0]\n' for v in row))
        if 'oracleGuard' in args[2]:
            return mock.Mock(stdout='0x' + 'aa' * 20 + '\n')
        if 'pricesUSD18' in args[2]:
            return mock.Mock(stdout=str(self.STOCK_PRICE) + ' [2.05e20]\n1000000000000000000 [1e18]\n')
        if 'latestRoundData' in args[2]:
            row = [7, self.ETH_ANSWER, self.now - 60, self.now - 60, 7]
            return mock.Mock(stdout=''.join(str(v) + '\n' for v in row))
        raise AssertionError(args)

    def patches(self):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(w.rk, 'simulate', side_effect=self.simulate))
        stack.enter_context(mock.patch.object(w.rk, 'send', side_effect=self.send))
        stack.enter_context(mock.patch.object(w.rk, 'cast', side_effect=self.cast))
        stack.enter_context(mock.patch.object(w.rk, 'gas_price_ok', return_value=(True, self.GAS_PRICE)))
        stack.enter_context(mock.patch.object(w.rk, 'journal'))
        stack.enter_context(mock.patch.object(w.rk, 'last_sent', return_value=0))
        stack.enter_context(mock.patch.object(w.rk.time, 'time', side_effect=lambda: self.now))
        return stack


COST_USD = 3 * 10 ** 16  # FakeChain's rebalance cost: $0.03
USDG = 10 ** 12  # USD18 per raw USDG


class RebalanceValueTests(unittest.TestCase):
    def run_cycle(self, chain, execute=True):
        out = io.StringIO()
        with chain.patches(), contextlib.redirect_stdout(out):
            code = w.rk.step(execute)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def stale(self, **kwargs):
        chain = FakeChain(**kwargs)
        chain.now += 3601
        return chain

    def test_dust_is_skipped_and_the_checkpoint_is_still_sent(self):
        chain = self.stale(usdg_idle=371)  # the real last rebalance: 371 raw USDG
        code, logs = self.run_cycle(chain)
        self.assertEqual(code, 0)
        self.assertEqual(chain.sent, ['checkpoint'])
        self.assertEqual(logs[-1]['event'], 'rebalance_below_value_threshold')
        self.assertEqual(logs[-1]['gasCostUsd'], 0.03)

    def test_one_sided_idle_stock_has_no_matched_value(self):
        chain = FakeChain(stock_idle=30 * 10 ** 15, usdg_idle=0)  # about $6 of stock, the live state
        code, logs = self.run_cycle(chain)
        self.assertEqual((code, chain.sent), (0, []))
        self.assertEqual(logs[-1]['matchedUsd'], 0)

    def test_deployable_value_gets_checkpoint_then_rebalance(self):
        chain = self.stale(usdg_idle=10 * COST_USD // USDG + 1000)
        self.assertEqual(self.run_cycle(chain)[0], 0)
        self.assertEqual(chain.sent, ['checkpoint', 'rebalance'])

    def test_threshold_boundary(self):
        at = FakeChain(usdg_idle=10 * COST_USD // USDG)
        self.run_cycle(at)
        self.assertEqual(at.sent, ['rebalance'])
        below = FakeChain(usdg_idle=10 * COST_USD // USDG - 1)
        self.run_cycle(below)
        self.assertEqual(below.sent, [])

    def test_matched_value_is_limited_by_the_stock_side_too(self):
        chain = FakeChain(stock_idle=10 ** 12, usdg_idle=10 ** 9)  # $0.0002 of stock against $1000
        self.run_cycle(chain)
        self.assertEqual(chain.sent, [])

    def test_multiple_comes_from_the_optional_env(self):
        chain = FakeChain(usdg_idle=2 * COST_USD // USDG)
        with mock.patch.dict(os.environ, {'REBALANCE_MIN_VALUE_MULTIPLE': '2'}):
            self.run_cycle(chain)
        self.assertEqual(chain.sent, ['rebalance'])
        for bad in ('nonsense', '0', '-1', 'nan', 'inf'):
            with mock.patch.dict(os.environ, {'REBALANCE_MIN_VALUE_MULTIPLE': bad}):
                self.assertEqual(w.rk.rebalance_min_value_multiple(), 10.0)

    def test_checkpoint_cadence_over_a_day_with_no_rebalance_following(self):
        chain = FakeChain(usdg_idle=100)
        times = []
        for _ in range(int(86400 / 306)):
            chain.now += 306
            before = len(chain.sent)
            self.run_cycle(chain)
            if len(chain.sent) > before:
                times.append(chain.now)
        self.assertEqual(set(chain.sent), {'checkpoint'})
        self.assertEqual({b - a for a, b in zip(times, times[1:])}, {12 * 306})  # first cycle past 3600 s
        self.assertEqual(len(times), 23)

    def test_recent_rebalance_does_not_hold_back_a_checkpoint(self):
        chain = self.stale(usdg_idle=100)
        with chain.patches(), contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(w.rk, 'last_sent', return_value=chain.now - 10):
                w.rk.step(True)
        self.assertEqual(chain.sent, ['checkpoint'])

    def test_stale_oracle_sends_nothing_and_reads_nothing_new(self):
        chain = self.stale(oracle_ok=False, usdg_idle=10 ** 9)
        code, logs = self.run_cycle(chain)
        self.assertEqual((code, chain.sent, chain.reads), (0, [], []))
        self.assertEqual(logs[-1]['step'], 'checkpoint')

    def test_failed_checkpoint_send_path_is_unchanged(self):
        chain = self.stale(usdg_idle=10 ** 9)
        chain.send_code = 1
        self.assertEqual(self.run_cycle(chain)[0], 3)
        self.assertEqual((chain.sent, chain.reads), (['checkpoint'], []))

    def test_mined_revert_of_a_checkpoint_stops_the_cycle(self):
        chain = self.stale(usdg_idle=10 ** 9)
        chain.send_output = '{"status":"0x0"}'
        self.assertEqual(self.run_cycle(chain)[0], 3)
        self.assertEqual(chain.sent, ['checkpoint'])

    def test_failed_rebalance_send_path_is_unchanged(self):
        chain = FakeChain(usdg_idle=10 ** 9)
        chain.send_code = 1
        code, logs = self.run_cycle(chain)
        self.assertEqual((code, logs[-1]['action']), (3, 'SEND FAILED'))

    def test_low_gas_path_sends_nothing(self):
        chain = self.stale(usdg_idle=10 ** 9)
        with chain.patches(), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.cycle(KEEPER, execute=True, funded=False), 0)
        self.assertEqual(chain.sent, [])

    def test_each_failed_read_skips_only_the_rebalance(self):
        for broken in ('ledger(', 'oracleGuard', 'pricesUSD18', 'latestRoundData', 'gas-price'):
            with self.subTest(broken):
                chain = self.stale(usdg_idle=10 ** 9)
                chain.broken_read = broken
                code, logs = self.run_cycle(chain)
                self.assertEqual((code, chain.sent), (0, ['checkpoint']))
                self.assertEqual(logs[-1]['event'], 'rebalance_value_read_failed')
                self.assertEqual(logs[-1]['errorType'], 'RuntimeError')
                self.assertNotIn('rpc failed', json.dumps(logs))

    def test_unusable_reads_also_fail_safe(self):
        cases = {'stale eth price': {'latestRoundData': '7\n200000000000\n1\n1\n7\n'},
                 'zero eth price': {'latestRoundData': '7\n0\n1800000000\n1800000000\n7\n'},
                 'garbage': {'ledger(': 'not a number\n'},
                 'short answer': {'ledger(': '1\n2\n'},
                 'zero gas price': {'gas-price': '0\n'}}
        for name, override in cases.items():
            with self.subTest(name):
                chain = FakeChain(usdg_idle=10 ** 9)
                real = chain.cast

                def fake(*args, check=True, real=real, override=override):
                    for key, stdout in override.items():
                        if key in ' '.join(args):
                            return mock.Mock(stdout=stdout)
                    return real(*args, check=check)
                chain.cast = fake
                code, logs = self.run_cycle(chain)
                self.assertEqual((code, chain.sent), (0, []))
                self.assertEqual(logs[-1]['event'], 'rebalance_value_read_failed')

    def test_recenter_keeps_priority_and_never_consults_the_gate(self):
        chain = self.stale(recenter_due=True, usdg_idle=0)
        self.assertEqual(self.run_cycle(chain)[0], 0)
        self.assertEqual((chain.sent, chain.reads), (['recenter'], []))

    def test_monitor_mode_never_sends(self):
        for chain in (self.stale(usdg_idle=10 ** 9), FakeChain(usdg_idle=10 ** 9)):
            self.run_cycle(chain, execute=False)
            self.assertEqual(chain.sent, [])


if __name__ == '__main__':
    unittest.main()
