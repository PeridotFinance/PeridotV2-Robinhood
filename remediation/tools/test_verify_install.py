import unittest
from verify_install import masked_runtime, verify_immutables


class RuntimeVerificationTests(unittest.TestCase):
    def test_only_reviewed_immutable_bytes_are_masked(self):
        refs = {'1': [{'start': 1, 'length': 32}]}
        self.assertEqual(masked_runtime('ab' + 'ff' * 32 + 'cd', refs), bytes.fromhex('ab' + '00' * 32 + 'cd'))
        self.assertNotEqual(masked_runtime('ab' + 'ff' * 32 + 'cc', refs), bytes.fromhex('ab' + '00' * 32 + 'cd'))

    def test_invalid_reference_cannot_hide_runtime(self):
        for start, length in ((0, 33), (2, 32), (-1, 32)):
            with self.assertRaises(RuntimeError):
                masked_runtime('00' * 32, {'1': [{'start': start, 'length': length}]})

    def test_all_copies_of_each_immutable_must_match(self):
        artifact = {'ast': {'nodeType': 'VariableDeclaration', 'mutability': 'immutable', 'id': 1, 'name': 'source'},
                    'deployedBytecode': {'immutableReferences': {'1': [{'start': 0, 'length': 32}, {'start': 32, 'length': 32}]}}}
        value = (123).to_bytes(32, 'big').hex()
        verify_immutables(value * 2, artifact, {'source': hex(123)})
        with self.assertRaises(RuntimeError):
            verify_immutables(value + (124).to_bytes(32, 'big').hex(), artifact, {'source': hex(123)})
        with self.assertRaises(RuntimeError):
            verify_immutables(value * 2, artifact, {'wrong': hex(123)})


if __name__ == '__main__':
    unittest.main()
