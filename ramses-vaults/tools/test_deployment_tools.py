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
        reader=FakeReader(code); Verifier(reader,{}).runtime(address,'RamsesInterestRateModel')
        # Same-size byte change in the executable body.
        reader.runtime=code[:40]+('ff' if code[40:42]!='ff' else '00')+code[42:]
        with self.assertRaises(AssertionError): Verifier(reader,{}).runtime(address,'RamsesInterestRateModel')
    def test_truncated_runtime_rejected(self):
        with self.assertRaises(AssertionError): Verifier(FakeReader('0x00'),{}).runtime('0x'+'12'*20,'RamsesInterestRateModel')

if __name__=='__main__': unittest.main()
