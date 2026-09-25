import unittest

from _fake import FakeTransport, fresh_home
from jev import analysis, settings
from jev.client import Client
from jev.errors import UsageError
from jev.textio import split_hunks


class Sift(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        (self.home / "src").mkdir()
        (self.home / "src" / "a.py").write_text("def one():\n    return 'yes'\n\ndef two():\n    return 'no'\n")
        (self.home / "src" / "b.txt").write_text("nothing here")
        self.c = Client(transport=FakeTransport())

    def test_files_functions_and_grep(self):
        cands, meta = analysis.sift_candidates([str(self.home / "src")])
        self.assertEqual([c["path"].rsplit("/", 1)[1] for c in cands], ["a.py", "b.txt"])
        cands, meta = analysis.sift_candidates([str(self.home / "src" / "a.py")], functions=True)
        self.assertEqual([c["name"] for c in cands], ["one", "two"])
        self.assertEqual(meta[1][0].endswith(":4  two"), True)
        cands, meta = analysis.sift_candidates([], [f"{self.home}/src/a.py:2:    return 'yes'", f"{self.home}/src/a.py:5:    return 'no'", "other.py"], grep=True)
        self.assertEqual([c["hits"] for c in cands], [2, 0])
        rows, cut, u = analysis.sift(self.c, "yes", *analysis.sift_candidates([str(self.home / "src")]), top=5, budget_tokens=20)
        self.assertTrue(rows[0]["path"].endswith("a.py"))
        self.assertEqual(cut, 1)
        with self.assertRaises(UsageError):
            analysis.sift(self.c, "q", [], [])


class Tests(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        (self.home / "tests").mkdir()
        (self.home / "tests" / "test_http.py").write_text("def test_yes():\n    pass\n")
        (self.home / "tests" / "test_cache.py").write_text("def test_no():\n    pass\n")
        (self.home / "src").mkdir()
        (self.home / "src" / "http.py").write_text("x = 1\n")
        self.c = Client(transport=FakeTransport())

    def test_candidates_and_ranking(self):
        cands, meta = analysis.test_candidates(str(self.home), ["src/http.py"])
        self.assertEqual(sorted(m["path"].rsplit("/", 1)[1] for m in meta), ["test_cache.py", "test_http.py"])
        http = next(m for m in meta if m["path"].endswith("test_http.py"))
        self.assertTrue(http["name_match"])
        self.assertEqual(http["n_tests"], 1)
        results, u = analysis.rank_tests(self.c, "+yes", cands, meta, top=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(u.requests, 1)
        with self.assertRaises(UsageError):
            analysis.rank_tests(self.c, "d", [], [])


class Diff(unittest.TestCase):
    def test_rate_hunks_and_scope(self):
        fresh_home()
        raw = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a = 1\n+a = yes\n@@ -9,2 +9,2 @@\n-b = 1\n+b = 2\n"
        rows, u = analysis.rate_hunks(Client(transport=FakeTransport()), split_hunks(raw), task="the task")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["level"], "critical")  # the "yes" hunk scores top
        self.assertIn("in_scope", rows[0])
        self.assertEqual(u.requests, 2)


class Cluster(unittest.TestCase):
    def test_groups_and_near_misses(self):
        fresh_home()
        clusters, notes, u = analysis.cluster(Client(transport=FakeTransport()), ["yes a", "yes b", "no c", "maybe d"], band=(0.4, 0.6))
        self.assertEqual([len(c["members"]) for c in clusters][0], 2)
        self.assertEqual([j for j, r, p in notes], [3])
        with self.assertRaises(UsageError):
            analysis.cluster(Client(transport=FakeTransport()), ["one"])


class Failures(unittest.TestCase):
    def test_sort(self):
        fresh_home()
        rows, u = analysis.sort_failures(Client(transport=FakeTransport()), "+yes", ["yes a", "no b"])
        self.assertEqual(rows[0]["i"], 0)
        self.assertEqual(rows[0]["flaky_level"], "flaky")
        self.assertEqual(u.requests, 2)


if __name__ == "__main__":
    unittest.main()
