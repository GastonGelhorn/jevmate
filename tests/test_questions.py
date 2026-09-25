import json
import unittest

from _fake import fresh_home  # noqa: F401  (puts the repo on sys.path)
from jev.errors import UsageError
from jev.questions import choice, level_name, noul, parse_kv, parse_value, score, validate


class Builders(unittest.TestCase):
    def test_noul_criteria_only_when_given(self):
        self.assertNotIn("criteria", noul("q"))
        self.assertEqual(noul("q", true="a")["criteria"], {"true": "a"})

    def test_choice_from_list_and_dict(self):
        self.assertEqual(choice("q", ["a", "b"])["criteria"], {"a": None, "b": None})
        self.assertEqual(choice("q", {"a": "x"})["criteria"], {"a": "x"})

    def test_score_keeps_order(self):
        self.assertEqual(score("q", ("lo", "hi"))["criteria"], ["lo", "hi"])


class Validate(unittest.TestCase):
    def test_accepts_all_three(self):
        validate({"a": noul("q"), "b": choice("q", ["x", "y"]), "c": score("q", ["l", "h"])})

    def test_rejects(self):
        for bad in ({}, {"a": "str"}, {"a": {"type": "nope", "instructions": "q"}}, {"a": {"type": "noul"}},
                    {"a": choice("q", ["one"])}, {"a": score("q", ["one"])}, {"a": {"type": "noul", "instructions": "q", "criteria": {"maybe": 1}}},
                    {"a": {"type": "choice", "instructions": "q", "criteria": {str(i): None for i in range(300)}}}):
            with self.assertRaises(UsageError, msg=json.dumps(bad)[:60]):
                validate(bad)


class Parsing(unittest.TestCase):
    def test_parse_value(self):
        self.assertEqual(parse_value('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_value("[1, 2]"), [1, 2])
        self.assertIs(parse_value("true"), True)
        self.assertEqual(parse_value("plain text"), "plain text")
        self.assertEqual(parse_value("{not json"), "{not json")

    def test_parse_value_file(self):
        home = fresh_home()
        (home / "x.json").write_text('{"k": [1]}')
        (home / "y.jsonl").write_text('{"a": 1}\n\n{"a": 2}\n')
        (home / "z.txt").write_text("hello")
        self.assertEqual(parse_value(f"@{home}/x.json"), {"k": [1]})
        self.assertEqual(parse_value(f"@{home}/y.jsonl"), [{"a": 1}, {"a": 2}])
        self.assertEqual(parse_value(f"@{home}/z.txt"), "hello")
        with self.assertRaises(UsageError):
            parse_value(f"@{home}/missing.txt")

    def test_parse_kv(self):
        self.assertEqual(parse_kv(["a=desc", "b", "c="]), {"a": "desc", "b": None, "c": None})

    def test_level_name(self):
        self.assertEqual(level_name({"what": "w", "level": "l"}), "w")
        self.assertEqual(level_name("plain"), "plain")


if __name__ == "__main__":
    unittest.main()
