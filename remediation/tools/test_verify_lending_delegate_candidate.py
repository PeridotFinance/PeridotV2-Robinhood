import unittest

import verify_lending_delegate_candidate as v


def artifact(code_hex, refs=None, sources=()):
    return {'deployedBytecode': {'object': '0x' + code_hex, 'immutableReferences': refs or {}},
            'metadata': {'sources': {s: {} for s in sources}}}


class LendingDelegateVerifierTests(unittest.TestCase):
    def test_strip_cbor_removes_trailer_and_its_length_bytes(self):
        body = bytes(range(10))
        trailer = bytes([0xA1, 0x64, 0x69, 0x70, 0x66, 0x73])
        code = body + trailer + len(trailer).to_bytes(2, 'big')
        self.assertEqual(v.strip_cbor(code), body)

    def test_fill_writes_every_reference_with_the_padded_address(self):
        art = artifact('00' * 100, {'7': [{'start': 4, 'length': 32}, {'start': 50, 'length': 32}]})
        address = '0x' + 'ab' * 20
        filled = v.fill(art, address)
        word = bytes.fromhex('00' * 12 + 'ab' * 20)
        self.assertEqual(filled[4:36], word)
        self.assertEqual(filled[50:82], word)
        self.assertEqual(filled[:4], bytes(4))
        self.assertEqual(len(filled), 100)

    def test_immutable_shape_ignores_ids_but_not_structure(self):
        a = artifact('00' * 8, {'111': [{'start': 0, 'length': 32}] * 4})
        b = artifact('00' * 8, {'999': [{'start': 5, 'length': 32}] * 4})
        c = artifact('00' * 8, {'999': [{'start': 5, 'length': 32}] * 3})
        d = artifact('00' * 8, {})
        self.assertEqual(v.immutable_shape(a), v.immutable_shape(b))
        self.assertNotEqual(v.immutable_shape(a), v.immutable_shape(c))
        self.assertNotEqual(v.immutable_shape(a), v.immutable_shape(d))
        self.assertEqual(len(v.immutable_shape(a)), 1)

    def test_normalized_abi_drops_internal_types_only(self):
        item = {'type': 'function', 'name': 'x', 'inputs': [{'name': 'a', 'type': 'uint256', 'internalType': 'uint256'}]}
        self.assertEqual(v.normalized_abi(item)['inputs'][0], {'name': 'a', 'type': 'uint256'})

    def test_expected_immutables_are_the_two_known_declarations(self):
        self.assertEqual(v.EXPECTED_IMMUTABLES, {'BORROW_ACCOUNTING_MODULE': 'PToken.sol', 'SELF': 'BorrowAccountingModule.sol'})

    def test_only_the_zero_share_error_may_be_added_to_the_abi(self):
        self.assertEqual(v.ALLOWED_ABI_ADDITIONS, [{'type': 'error', 'name': 'ZeroSharesMinted', 'inputs': []}])


if __name__ == '__main__':
    unittest.main()
