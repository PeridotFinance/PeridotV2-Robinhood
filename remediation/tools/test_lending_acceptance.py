"""Receipt validation rejects soft failures and incomplete/mismatched operator transactions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import lending_acceptance as runner


class AcceptanceVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.before = {'stockWallet': 10**16, 'dollarWallet': 1000000, 'stockShares': 100,
                       'pNVDA': {'debt': 0, 'member': False}, 'pUSDG': {'debt': 0, 'member': False}}
        self.after = dict(self.before, block=110, stockWallet=9*10**15, dollarWallet=1050000,
                          stockShares=110, stockAllowance=0, dollarAllowance=0,
                          pNVDA={'debt': 0, 'member': True}, pUSDG={'debt': 50000, 'member': True})
        self.pin = {'nonceBefore': 20, 'before': self.before}
        stock, dollar = runner.MARKETS['pNVDA'], runner.MARKETS['pUSDG']
        plans = [(runner.STOCK, 'approve(address,uint256)', [stock, '1000000000000000']),
                 (stock, 'mint(uint256)', ['1000000000000000']),
                 (runner.CONTROLLER, 'enterMarkets(address[])', ['['+stock+']']),
                 (dollar, 'borrow(uint256)', ['50000'])]
        self.transactions, self.receipts = {}, {}
        entries = []
        for i, (target, signature, values) in enumerate(plans):
            h = '0x'+str(i+1).zfill(64)
            entries.append({'hash': h})
            self.transactions[h] = {'from': runner.GOVERNOR, 'to': target, 'input': runner.cast('calldata', signature, *values),
                                    'chainId': hex(4663), 'value': '0x0', 'nonce': hex(20+i), 'blockHash': '0xabc'}
            logs = []
            if i in (1, 3):
                sig = 'Mint(address,uint256,uint256)' if i == 1 else 'Borrow(address,uint256,uint256,uint256)'
                logs = [{'address': target, 'topics': [runner.cast('keccak', sig)]}]
            self.receipts[h] = {'transactionHash': h, 'status': '0x1', 'blockNumber': hex(100+i), 'blockHash': '0xabc', 'logs': logs}
        path = self.root/'broadcast/LendingAcceptance.s.sol/4663/supplyAndBorrow-latest.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'transactions': entries}))
        self.journal_path = path

    def rpc(self, method, args):
        if method == 'eth_getTransactionByHash':
            return self.transactions[args[0]]
        if method == 'eth_getTransactionReceipt':
            return self.receipts[args[0]]
        if method == 'eth_getBlockByNumber':
            return {'hash': '0xabc'}
        raise AssertionError(method)

    def verify(self, stage="open"):
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'rpc', self.rpc), patch.object(runner, 'snapshot', return_value=self.after):
            runner.verify(stage, copy.deepcopy(self.pin))

    def close_fixture(self):
        stock, dollar = runner.MARKETS['pNVDA'], runner.MARKETS['pUSDG']
        plans = [(runner.USDG, 'approve(address,uint256)', [dollar, '51000']),
                 (dollar, 'repayBorrow(uint256)', [str(2**256-1)]),
                 (runner.USDG, 'approve(address,uint256)', [dollar, '0']),
                 (stock, 'redeemUnderlying(uint256)', ['1000000000000000']),
                 (runner.CONTROLLER, 'exitMarket(address)', [dollar]),
                 (runner.CONTROLLER, 'exitMarket(address)', [stock])]
        entries = []
        for i, (target, signature, values) in enumerate(plans):
            h = '0x'+str(i+1).zfill(64); entries.append({'hash': h})
            self.transactions[h] = {'from': runner.GOVERNOR, 'to': target, 'input': runner.cast('calldata', signature, *values),
                                    'chainId': hex(4663), 'value': '0x0', 'nonce': hex(20+i), 'blockHash': '0xabc'}
            logs = []
            if i in (1, 3):
                sig = 'RepayBorrow(address,address,uint256,uint256,uint256)' if i == 1 else 'Redeem(address,uint256,uint256)'
                logs = [{'address': target, 'topics': [runner.cast('keccak', sig)]}]
            self.receipts[h] = {'transactionHash': h, 'status': '0x1', 'blockNumber': hex(100+i), 'blockHash': '0xabc', 'logs': logs}
        self.journal_path = self.journal_path.with_name('repayAndRedeem-latest.json')
        self.journal_path.write_text(json.dumps({'transactions': entries}))
        self.pin['before'] = copy.deepcopy(self.after)
        self.after.update(stockWallet=10**16, dollarWallet=999999, stockAllowance=0, dollarAllowance=0,
                          pNVDA={'debt': 0, 'member': False}, pUSDG={'debt': 0, 'member': False})

    def test_valid_close_with_interest(self):
        self.close_fixture()
        self.verify('close')

    def test_residual_debt_rejected(self):
        self.close_fixture()
        self.after['pUSDG']['debt'] = 1
        with self.assertRaisesRegex(RuntimeError, 'final balances'):
            self.verify('close')

    def test_stale_generic_journal_is_ignored(self):
        self.journal_path.with_name('run-latest.json').write_text('{"transactions": []}')
        self.verify()

    def test_missing_stage_journal_does_not_fall_back(self):
        self.journal_path.rename(self.journal_path.with_name('run-latest.json'))
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_verify_cli_never_signs_or_simulates(self):
        intent = self.root/'remediation/evidence/lending-acceptance-open-intent.json'
        intent.parent.mkdir(parents=True)
        intent.write_text(json.dumps(dict(self.pin, chainId=4663, stage='open', broadcast=True)))
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'rpc', return_value=hex(4663)), patch.object(runner, 'verify') as verify, patch.object(runner.subprocess, 'run', side_effect=AssertionError('must not execute subprocess')), patch.object(runner.sys, 'argv', ['lending_acceptance.py', '--stage', 'open', '--verify']):
            runner.main()
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0], 'open')

    def test_native_clock_skips_only_secondary_replay(self):
        def chain(method, args):
            if method == 'eth_chainId':
                return hex(4663)
            if method == 'eth_getBlockByNumber':
                return {'number': hex(76000000), 'hash': '0xabc'}
            if method == 'eth_call':
                return hex(26000000)
            raise AssertionError(method)
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, 'rpc', side_effect=chain), patch.object(runner.subprocess, 'run') as process, patch.object(runner.sys, 'argv', ['lending_acceptance.py', '--stage', 'close']):
            process.return_value.returncode = 0
            with self.assertRaises(SystemExit) as result:
                runner.main()
        self.assertEqual(result.exception.code, 0)
        command = process.call_args.args[0]
        self.assertEqual(command[:2], ['forge', 'script'])
        self.assertIn('--skip-simulation', command)
        self.assertIn('repayAndRedeem()', command)
        self.assertNotIn('--broadcast', command)
        self.assertNotIn('--account', command)
        self.assertEqual(process.call_args.kwargs['env']['ACCEPTANCE_NATIVE_BLOCK'], '26000000')

    def test_valid_open(self):
        self.verify()
        self.assertTrue(json.loads((self.root/'remediation/evidence/lending-acceptance-open-verified.json').read_text())['verified'])

    def test_soft_failure_is_not_success(self):
        receipt = list(self.receipts.values())[1]
        receipt['logs'].append({'address': runner.MARKETS['pNVDA'], 'topics': [runner.cast('keccak', 'Failure(uint256,uint256,uint256)')]})
        with self.assertRaisesRegex(RuntimeError, 'Failure event'):
            self.verify()

    def test_wrong_amount_calldata(self):
        list(self.transactions.values())[3]['input'] = runner.cast('calldata', 'borrow(uint256)', '500000')
        with self.assertRaisesRegex(RuntimeError, 'mismatch'):
            self.verify()

    def test_partial_stage(self):
        d = json.loads(self.journal_path.read_text()); d['transactions'].pop()
        self.journal_path.write_text(json.dumps(d))
        with self.assertRaisesRegex(RuntimeError, 'transaction count'):
            self.verify()

    def test_missing_success_event(self):
        list(self.receipts.values())[3]['logs'] = []
        with self.assertRaisesRegex(RuntimeError, 'Missing success event'):
            self.verify()

    def test_wrong_balance_delta(self):
        self.after['dollarWallet'] = self.before['dollarWallet']
        with self.assertRaisesRegex(RuntimeError, 'final balances'):
            self.verify()

    def test_noncanonical_receipt(self):
        list(self.receipts.values())[0]['blockHash'] = '0xdef'
        with self.assertRaisesRegex(RuntimeError, 'Noncanonical'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
