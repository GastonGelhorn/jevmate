import json
import unittest

from _fake import FakeTransport, fresh_home
from jev import cache, ledger, settings
from jev.client import Client, serialize
from jev.errors import AuthError, DryRun, JevError, UsageError
from jev.questions import noul


class Ask(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        self.t = FakeTransport()
        self.c = Client(transport=self.t, label="t", retries=1)

    def test_roundtrip_records_and_caches(self):
        r = self.c.ask({"x": "yes please"}, {"q": noul("Is `x` a yes?")})
        self.assertAlmostEqual(r["answers"]["q"]["noul"], 0.9)
        self.assertEqual(len(self.t.calls), 1)
        rows = ledger.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cmd"], "t")
        self.assertEqual(rows[0]["q"], 1)
        self.assertEqual(rows[0]["rid"], "rid-1")
        r2 = self.c.ask({"x": "yes please"}, {"q": noul("Is `x` a yes?")})
        self.assertTrue(r2["cached"])
        self.assertEqual(r2["usage"]["input_tokens"], 0)
        self.assertGreater(r2["cached_usage"]["input_tokens"], 0)
        self.assertEqual(len(self.t.calls), 1, "a cache hit must not go to the network")
        self.assertTrue(ledger.rows()[-1]["cached"])

    def test_no_cache_flag(self):
        settings.RUNTIME.cache = False
        self.c.ask("yes", {"q": noul("q")})
        self.c.ask("yes", {"q": noul("q")})
        self.assertEqual(len(self.t.calls), 2)

    def test_dry_run_raises_with_body_and_needs_no_key(self):
        settings.RUNTIME.dry_run = True
        with self.assertRaises(DryRun) as cm:
            Client(api_key="", transport=self.t).ask("s", {"q": noul("q")})
        self.assertEqual(cm.exception.body["state"], "s")
        self.assertEqual(self.t.calls, [])

    def test_empty_state_and_bad_questions(self):
        with self.assertRaises(UsageError):
            self.c.ask("", {"q": noul("q")})
        with self.assertRaises(UsageError):
            self.c.ask("s", {})

    def test_auth_error_not_retried(self):
        t = FakeTransport([401])
        with self.assertRaises(AuthError):
            Client(transport=t, retries=3).ask("s", {"q": noul("q")})
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(ledger.rows()[-1]["err"], "401")

    def test_retry_then_success(self):
        t = FakeTransport([429, 503, 200])
        c = Client(transport=t, retries=3)
        r = c.ask("s", {"q": noul("q")})
        self.assertIn("answers", r)
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(c.last_attempts, 3)
        self.assertEqual(ledger.rows()[-1]["attempts"], 3)

    def test_gives_up_after_retries(self):
        t = FakeTransport([500, 500, 500])
        with self.assertRaises(JevError):
            Client(transport=t, retries=1).ask("s", {"q": noul("q")})
        self.assertEqual(len(t.calls), 2)

    def test_serialize_is_canonical_utf8(self):
        a = serialize({"b": 1, "a": "ñ"})
        b = serialize({"a": "ñ", "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(a, '{"a":"ñ","b":1}'.encode("utf-8"))

    def test_models(self):
        self.assertEqual(self.c.models()["models"][0]["name"], "jev-latest")


class CacheModule(unittest.TestCase):
    def test_stats_and_clear(self):
        fresh_home()
        cache.put("k" * 64, {"answers": {}})
        st = cache.stats()
        self.assertEqual(st["entries"], 1)
        self.assertEqual(cache.clear(), 1)
        self.assertEqual(cache.stats()["entries"], 0)

    def test_get_respects_ttl(self):
        home = fresh_home()
        settings.save_config({"cache_ttl_days": 0})
        cache.put("a" * 64, {"answers": {}})
        self.assertIsNone(cache.get("a" * 64))


if __name__ == "__main__":
    unittest.main()
