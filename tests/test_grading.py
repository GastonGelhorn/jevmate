import unittest

from _fake import FakeTransport, fresh_home
from jev.client import Client
from jev.decide import Decision, decide, decide_many
from jev.errors import UsageError
from jev.grading import grade, in_band, pack, parse_band


class Pack(unittest.TestCase):
    def test_respects_chars_items_and_overhead(self):
        items = ["a" * 10] * 5
        self.assertEqual([len(c) for c in pack(items, 25, 10)], [2, 2, 1])
        self.assertEqual([len(c) for c in pack(items, 1000, 2)], [2, 2, 1])
        self.assertEqual([len(c) for c in pack(items, 25, 10, per_item_overhead=10)], [1] * 5)
        self.assertEqual(pack([], 10, 10), [])

    def test_keeps_input_indexes(self):
        chunks = pack(["x", "y", "z"], 100, 2)
        self.assertEqual([[i for i, _ in c] for c in chunks], [[0, 1], [2]])


class Grade(unittest.TestCase):
    def setUp(self):
        fresh_home()
        self.t = FakeTransport()
        self.c = Client(transport=self.t)

    def test_noul_results_in_input_order(self):
        g = grade(self.c, ["yes one", "no two", "yes three"], "Is `candidate` a yes?", "q", chunk_size=2)
        self.assertEqual([r["i"] for r in g.results], [0, 1, 2])
        self.assertEqual([round(r["p"], 1) for r in g.results], [0.9, 0.1, 0.9])
        self.assertEqual(g.requests, 2)
        self.assertEqual(len(self.t.calls), 2)
        self.assertGreater(g.input_tokens, 0)

    def test_levels_normalise_to_unit(self):
        g = grade(self.c, ["yes"], "rate `candidate`", "q", levels=["a", "b", "c"])
        self.assertAlmostEqual(g.results[0]["p"], 0.9)
        self.assertAlmostEqual(g.results[0]["score"], 1.8)

    def test_empty(self):
        self.assertEqual(grade(self.c, [], "q", "q").requests, 0)

    def test_band(self):
        self.assertIsNone(parse_band(None))
        self.assertEqual(parse_band(["0.3", "0.6"]), (0.3, 0.6))
        with self.assertRaises(UsageError):
            parse_band([0.6, 0.3])
        self.assertTrue(in_band(0.5, (0.4, 0.6)))
        self.assertFalse(in_band(0.5, None))


class Decide(unittest.TestCase):
    def setUp(self):
        fresh_home()
        self.c = Client(transport=FakeTransport())

    def test_three_outcomes(self):
        d = Decision(0.5, 0.5, (0.4, 0.6))
        self.assertTrue(d.unsure)
        self.assertFalse(d.yes)
        self.assertFalse(d.no)
        self.assertEqual(d.answer, "unsure")
        with self.assertRaises(TypeError):
            bool(d)
        self.assertTrue(Decision(0.7, 0.5).yes)
        self.assertTrue(Decision(0.2, 0.5).no)

    def test_decide_and_decide_many(self):
        self.assertTrue(decide({"t": "yes"}, "Is `t` a yes?", client=self.c).yes)
        ds = decide_many(["yes a", "maybe b", "no c"], "Is `candidate` a yes?", threshold=0.5, band=(0.4, 0.6), client=self.c)
        self.assertEqual([d.answer for d in ds], ["yes", "unsure", "no"])
        self.assertEqual(ds[1].item, "maybe b")


if __name__ == "__main__":
    unittest.main()
