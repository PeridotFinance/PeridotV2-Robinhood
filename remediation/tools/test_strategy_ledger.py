import unittest

from evidence_lib import EvidenceError, addr_topic
from strategy_data import NVDA, USDG
from strategy_ledger import (PositionBook, build_position_events, build_principal_timeline, our_token_ids,
                             principal_at)

PAIR = "0x" + "11" * 32


def w(*vals):
    return "0x" + "".join((v % (1 << 256)).to_bytes(32, "big").hex() for v in vals)


def log(block, idx, topics, data):
    return {"blockNumber": block, "logIndex": idx, "topics": topics, "data": data, "tx": f"0x{block:064x}"}


def dep(block, idx, token, amount):
    return log(block, idx, ["0x0", PAIR, addr_topic(token), addr_topic("0x" + "aa" * 20)], w(amount))


def wd(block, idx, token, requested, returned, loss):
    return log(block, idx, ["0x0", PAIR, addr_topic(token), addr_topic("0x" + "bb" * 20)], w(requested, returned, loss))


def fees(block, idx, sf, uf, sr, ur):
    return log(block, idx, ["0x0", PAIR], w(sf, uf, sr, ur))


def ckpt(block, idx, stock_acc, usdg_acc, pnl=0):
    return log(block, idx, ["0x0", PAIR], w(0, 0, 0, pnl, stock_acc, usdg_acc))


def events(**kw):
    base = {"deposited": [], "withdrawal": [], "fees": [], "checkpoint": []}
    base.update(kw)
    return base


class PrincipalTimelineTests(unittest.TestCase):
    def test_sequence_matches_hand_computation(self):
        ev = events(
            deposited=[dep(10, 0, NVDA, 100), dep(10, 1, USDG, 1_000)],
            fees=[fees(20, 0, 10, 50, 2, 10)],          # stock +8, usdg +40
            checkpoint=[ckpt(20, 1, 105, 1_030, -13)],   # absolute after-values
            withdrawal=[wd(30, 0, NVDA, 40, 40, 0)],     # stock -40
        )
        tl = build_principal_timeline(ev)
        self.assertEqual([(p.kind, p.stock_principal, p.usdg_principal) for p in tl], [
            ("deposited", 100, 0), ("deposited", 100, 1_000), ("fees", 108, 1_040),
            ("checkpoint", 105, 1_030), ("withdrawal", 65, 1_030)])
        self.assertEqual(tl[3].detail["stockBefore"], 108)  # loss = before - after, available to analyses

    def test_same_block_ordering_uses_log_index(self):
        ev = events(deposited=[dep(5, 3, NVDA, 7)], checkpoint=[ckpt(5, 1, 50, 60)])
        tl = build_principal_timeline(ev)
        self.assertEqual([p.kind for p in tl], ["checkpoint", "deposited"])
        self.assertEqual((tl[-1].stock_principal, tl[-1].usdg_principal), (57, 60))

    def test_principal_at_is_after_the_event(self):
        ev = events(deposited=[dep(10, 0, NVDA, 100)], withdrawal=[wd(30, 0, NVDA, 40, 40, 0)])
        tl = build_principal_timeline(ev)
        self.assertEqual(principal_at(tl, 9), (0, 0))
        self.assertEqual(principal_at(tl, 10, 0), (100, 0))
        self.assertEqual(principal_at(tl, 29), (100, 0))
        self.assertEqual(principal_at(tl, 30, 0), (60, 0))

    def test_unexpected_token_fails(self):
        ev = events(deposited=[dep(1, 0, "0x" + "cc" * 20, 1)])
        with self.assertRaises(EvidenceError):
            build_principal_timeline(ev)


class PositionTests(unittest.TestCase):
    def mod(self, block, salt, delta, lower=-100, upper=100):
        return {"block": block, "idx": 0, "sender": "0xpm", "lower": lower, "upper": upper, "delta": delta, "salt": salt, "tx": "0x"}

    def test_only_our_token_ids_are_selected(self):
        added = [log(1, 0, ["0x0", PAIR, "0x" + hex(7)[2:].rjust(64, "0")], w(0, 0, 0))]
        ids = our_token_ids({"added": added})
        self.assertEqual(ids, {7})
        sel = build_position_events([self.mod(1, 7, 10), self.mod(2, 8, 10), self.mod(3, 7, -10)], ids)
        self.assertEqual([m["delta"] for m in sel], [10, -10])

    def test_book_tracks_and_closes_positions(self):
        b = PositionBook()
        b.apply(self.mod(1, 7, 10))
        b.apply(self.mod(2, 7, 5))
        self.assertEqual(b.live()[0].liq, 15)
        b.apply(self.mod(3, 7, -15))
        self.assertEqual(b.live(), [])

    def test_negative_liquidity_and_range_change_fail(self):
        b = PositionBook()
        with self.assertRaises(EvidenceError):
            b.apply(self.mod(1, 7, -1))
        b = PositionBook()
        b.apply(self.mod(1, 7, 10))
        with self.assertRaises(EvidenceError):
            b.apply(self.mod(2, 7, 1, lower=-50))


if __name__ == "__main__":
    unittest.main()
