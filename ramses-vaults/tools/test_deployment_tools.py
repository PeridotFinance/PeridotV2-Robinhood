"""Verifier regression tests: malformed ABI and unreviewed runtime changes fail closed."""
import json, os, subprocess, sys, unittest
from verify_deployment import ROOT, OUT, Reader, Verifier, words

class FakeReader:
    def __init__(self, code): self.runtime=code
    def code(self, target): return self.runtime

class DeploymentToolsTest(unittest.TestCase):
    def test_optimized_python_refuses_to_verify(self):
        result = subprocess.run([sys.executable, '-O', '-c', 'import verify_deployment'], cwd=ROOT/'ramses-vaults/tools', env={**os.environ,'PYTHONPYCACHEPREFIX':'/tmp/ramses-pycache'}, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Optimized Python mode', result.stderr)
    def test_read_only_rpc_rejects_mutating_methods(self):
        r=Reader('http://127.0.0.1:1')
        for method in ['eth_sendTransaction','eth_sendRawTransaction','personal_unlockAccount','anvil_setStorageAt']:
            with self.assertRaises(AssertionError): r.rpc(method,[])
    def test_malformed_abi_rejected(self):
        with self.assertRaises(AssertionError): words('0x01')
    def test_runtime_exact_bytes_not_just_size(self):
        artifact=json.loads((OUT/'RamsesInterestRateModel.sol/RamsesInterestRateModel.json').read_text())
        code=artifact['deployedBytecode']['object']
        address='0x'+'12'*20
        reader=FakeReader(code); Verifier(reader,{}).runtime(address,'RamsesInterestRateModel',['0x0'])
        # Same-size byte change in the executable body.
        reader.runtime=code[:40]+('ff' if code[40:42]!='ff' else '00')+code[42:]
        with self.assertRaises(AssertionError): Verifier(reader,{}).runtime(address,'RamsesInterestRateModel',['0x0'])
    def test_truncated_runtime_rejected(self):
        with self.assertRaises(AssertionError): Verifier(FakeReader('0x00'),{}).runtime('0x'+'12'*20,'RamsesInterestRateModel')


class Phase5aRateTest(unittest.TestCase):
    def test_phase5a_maximum_rate_guard(self):
        class RateReader:
            def uint(self, target, signature, *args):
                self.observed = (signature, args)
                return self.rate
        reader = RateReader()
        verifier = Verifier(reader, {'rate': 'rate-model'})
        reader.rate = 121765601217
        verifier.maximum_rate()
        self.assertEqual(reader.observed, ('getBorrowRate(uint256,uint256,uint256)', (0, 10**18, 0)))
        reader.rate = 5 * 10**12 + 1
        with self.assertRaisesRegex(AssertionError, 'maximum borrow rate'):
            verifier.maximum_rate()

class Phase5aWallTimeoutTest(unittest.TestCase):
    def test_wall_clock_jump_stops_process_group(self):
        import runpy
        from unittest.mock import Mock, patch
        child = Mock(pid=43210)
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child), patch('time.time', side_effect=[100, 701]), patch('os.killpg') as kill:
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT/'ramses-vaults/tools/check_phase2.py'), run_name='__main__')
            self.assertEqual(stopped.exception.code, 124)
            kill.assert_called_once()
            self.assertEqual(kill.call_args.args[0], 43210)
            child.wait.assert_called_once()

if __name__=='__main__': unittest.main()
