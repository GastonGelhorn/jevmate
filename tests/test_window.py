import json
import os
import unittest
from pathlib import Path

from _fake import fresh_home
from jev import settings, window


class Window(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        self.path = Path(os.environ["JEV_CLAUDE_SETTINGS"])

    def read(self):
        return json.loads(self.path.read_text())

    def test_set_when_absent_and_other_settings_kept(self):
        self.path.write_text(json.dumps({"model": "opus", "env": {"A": "1"}}))
        self.assertEqual(window.apply(), "set")
        self.assertEqual(self.read(), {"model": "opus", "env": {"A": "1"}, "autoCompactWindow": 400_000})
        self.assertEqual(window.current(), (400_000, "jev"))
        self.assertEqual(window.apply(), "unchanged")

    def test_the_person_s_own_value_is_left_alone(self):
        self.path.write_text(json.dumps({"autoCompactWindow": 700_000}))
        self.assertEqual(window.apply(), "kept")
        self.assertEqual(self.read()["autoCompactWindow"], 700_000)
        self.assertEqual(window.current(), (700_000, "yours"))

    def test_jev_s_value_follows_the_option_and_off_removes_it(self):
        self.assertEqual(window.apply(), "set")
        settings.save_config({**settings.config(), "compact_window": "300K"})
        self.assertEqual(window.apply(), "updated")
        self.assertEqual(self.read()["autoCompactWindow"], 300_000)
        settings.save_config({**settings.config(), "compact_window": "off"})
        self.assertEqual(window.apply(), "removed")
        self.assertNotIn("autoCompactWindow", self.read())
        self.assertEqual(window.apply(), "unchanged")

    def test_unreadable_settings_are_not_touched(self):
        self.path.write_text("{ not json")
        self.assertEqual(window.apply(), "skipped")
        self.assertEqual(self.path.read_text(), "{ not json")

    def test_parse(self):
        self.assertEqual([window.parse(v) for v in ("400K", "0.6M", 250000, "off", "50k", "x")], [400_000, 600_000, 250_000, None, 100_000, 400_000])


if __name__ == "__main__":
    unittest.main()
