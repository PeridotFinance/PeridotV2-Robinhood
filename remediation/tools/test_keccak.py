import unittest

from keccak import keccak256, keccak_hex


class KeccakTests(unittest.TestCase):
    def test_published_vectors(self):
        self.assertEqual(keccak256(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(keccak256(b"abc").hex(), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")

    def test_known_event_topics(self):
        self.assertEqual(keccak_hex("Transfer(address,address,uint256)"),
                         "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
        self.assertEqual(keccak_hex("Swap(address,address,int256,int256,uint160,uint128,int24)"),
                         "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67")

    def test_rate_boundary_lengths(self):
        # 135, 136 and 137 bytes straddle the padding edge cases of the 136-byte rate
        expected = {
            135: "34367dc248bbd832f4e3e69dfaac2f92638bd0bbd18f2912ba4ef454919cf446",
            136: "a6c4d403279fe3e0af03729caada8374b5ca54d8065329a3ebcaeb4b60aa386e",
            137: "d869f639c7046b4929fc92a4d988a8b22c55fbadb802c0c66ebcd484f1915f39",
        }
        for n, want in expected.items():
            self.assertEqual(keccak256(b"a" * n).hex(), want, n)


if __name__ == "__main__":
    unittest.main()
