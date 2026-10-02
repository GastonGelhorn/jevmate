import json
import os
import unittest

from _fake import fresh_home
from jev import library, settings
from jev.errors import UsageError


class Library(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        self.project = self.home / "proj"
        self.project.mkdir()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.project)

    def tearDown(self):
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def test_save_load_list_remove(self):
        p = library.save("refund", {"question": "Is `candidate` a refund request?", "threshold": 0.42, "band": (0.32, 0.52), "model": "m", "ignored": 1})
        self.assertEqual(p, self.home / "questions" / "refund.json")
        spec = library.load("refund")
        self.assertEqual(spec["band"], [0.32, 0.52])
        self.assertNotIn("ignored", spec)
        self.assertEqual(spec["path"], str(p))
        pp = library.save("refund", {"question": "project one"}, project=True)
        self.assertTrue(str(pp).startswith(str(self.project)))
        self.assertEqual(library.load("refund")["question"], "project one", "the project copy wins")
        names = [(r["name"], r["scope"]) for r in library.list_all()]
        self.assertEqual(names, [("refund", "project")])
        library.remove("refund")
        self.assertEqual(library.load("refund")["question"], "Is `candidate` a refund request?")
        with self.assertRaises(UsageError):
            library.load("nope")
        with self.assertRaises(UsageError):
            library.save("bad name!", {"question": "q"})
        with self.assertRaises(UsageError):
            library.save("empty", {})

    def test_apply_fills_only_blanks(self):
        class A:
            instructions = None
            threshold = 0.5
            band = None
            model = None
            true = None
            false = None

        spec = {"question": "q?", "threshold": 0.7, "band": [0.6, 0.8], "model": "pinned", "true": "yes means"}
        a = A()
        library.apply(spec, a)
        self.assertEqual((a.instructions, a.threshold, a.band, a.model, a.true), ("q?", 0.7, [0.6, 0.8], "pinned", "yes means"))
        b = A()
        b.instructions, b.threshold, b.model = "mine", 0.3, "other"
        library.apply(spec, b)
        self.assertEqual((b.instructions, b.threshold, b.model), ("mine", 0.3, "other"))

    def test_decide_many_accepts_a_saved_question(self):
        from _fake import FakeTransport
        from jev.client import Client
        from jev.decide import decide_many
        library.save("q1", {"question": "Is `candidate` yes?", "threshold": 0.8, "band": [0.45, 0.55]})
        ds = decide_many(["yes a", "maybe b", "no c"], library.load("q1"), client=Client(transport=FakeTransport()))
        self.assertEqual([d.answer for d in ds], ["yes", "unsure", "no"])
        self.assertEqual(ds[0].threshold, 0.8)


if __name__ == "__main__":
    unittest.main()
