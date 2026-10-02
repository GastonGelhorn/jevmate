import unittest
from pathlib import Path

from _fake import fresh_home
from jev.textio import compact_diff, first_line, split_functions, split_hunks, split_items, test_names, walk

DIFF = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 import os
+import sys
 x = 1
-y = 2
+y = 3
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -10 +10 @@
 unchanged context only
"""


class Splitting(unittest.TestCase):
    def test_line_and_jsonl(self):
        self.assertEqual(split_items('a\n{"text": "b"}\n\n{"other": 1}\n', None), ["a", "b", '{"other": 1}'])

    def test_blank(self):
        self.assertEqual(split_items("a\nb\n\n\nc\n", "blank"), ["a\nb", "c"])

    def test_pytest_preset_and_regex(self):
        raw = "___ test_a ___\nboom\n___ test_b ___\nbang\n"
        self.assertEqual(len(split_items(raw, "pytest-long")), 2)
        self.assertEqual(split_items("1) A\nx\n2) B\ny", "phpunit"), ["1) A\nx", "2) B\ny"])
        self.assertEqual(split_items("X a\nX b", r"^X "), ["X a", "X b"])
        self.assertEqual(split_items("no match", "go"), ["no match"])

    def test_first_line_strips_decoration(self):
        self.assertEqual(first_line("____ test_x ____\nmore", 80), "test_x")
        self.assertEqual(first_line("a" * 30, 10), "a" * 9 + "…")


class Diffs(unittest.TestCase):
    def test_split_hunks_keeps_only_changed(self):
        hunks = split_hunks(DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual((hunks[0]["file"], hunks[0]["line"], hunks[0]["added"], hunks[0]["removed"]), ("src/a.py", 1, 2, 1))
        self.assertEqual(hunks[0]["summary"], "import sys")

    def test_compact_diff_prefers_changed_lines(self):
        full = compact_diff(DIFF, 10_000)
        self.assertIn(" import os", full)
        short = compact_diff(DIFF, 200)
        self.assertNotIn(" import os", short)
        self.assertIn("+import sys", short)
        self.assertTrue(len(compact_diff(DIFF, 60)) <= 120)


class Code(unittest.TestCase):
    def test_split_functions_and_test_names(self):
        src = "import x\n\ndef test_one():\n    pass\n\nclass Foo:\n    def test_two(self):\n        pass\n"
        chunks = split_functions(src)
        self.assertEqual([c[1] for c in chunks], ["test_one", "Foo", "test_two"])
        self.assertEqual(chunks[0][0], 3)
        self.assertEqual(test_names(src), ["test_one", "test_two"])
        self.assertEqual(test_names("it('does a thing', () => {})\nfunc TestGo(t *testing.T) {}"), ["does a thing", "TestGo"])

    def test_walk_skips_junk(self):
        home = fresh_home()
        (home / "src").mkdir()
        (home / "src" / "a.py").write_text("x")
        (home / "src" / "pic.png").write_bytes(b"\x89PNG")
        (home / "src" / ".hidden").write_text("x")
        (home / "node_modules").mkdir()
        (home / "node_modules" / "b.js").write_text("x")
        got = [str(p.relative_to(home)) for p in walk([str(home)])]
        self.assertEqual(got, ["src/a.py"])
        self.assertEqual(walk([str(home / "src" / "pic.png")]), [Path(home / "src" / "pic.png")])


if __name__ == "__main__":
    unittest.main()
