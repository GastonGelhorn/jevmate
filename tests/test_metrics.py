import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from _fake import fresh_home
from jev import ledger, metrics, settings


def turn(mid, model, ts, inp=100, out=50, cr=1000, cw=0, block=0):
    return json.dumps({"type": "assistant", "timestamp": ts, "uuid": f"{mid}-{block}",
                       "message": {"id": mid, "model": model, "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}})


class TranscriptParsing(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_dedupes_by_message_id_and_reads_incrementally(self):
        p = self.home / "s1.jsonl"
        t0 = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
        lines = [json.dumps({"type": "user", "timestamp": "x"}),
                 turn("m1", "claude-fable-5-1", (t0).strftime("%Y-%m-%dT%H:%M:%S.000Z")),
                 turn("m1", "claude-fable-5-1", (t0).strftime("%Y-%m-%dT%H:%M:%S.000Z"), block=1),  # same message, second content block
                 turn("m2", "claude-opus-5", (t0 + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), cw=500)]
        p.write_text("\n".join(lines) + "\n")
        tr = metrics.Transcript(p)
        tr.refresh()
        self.assertEqual({k: v["turns"] for k, v in tr.per.items()}, {"claude-fable-5-1": 1, "claude-opus-5": 1})
        self.assertEqual(len(tr.turn_ts), 2)
        usd = tr.usd()
        self.assertAlmostEqual(usd, (100 * 10 + 50 * 50 + 1000 * 0.25) / 1e6 + (100 * 5 + 50 * 25 + 1000 * 0.5 + 500 * 6.25) / 1e6, places=9)
        with p.open("a") as f:
            f.write(turn("m3", "claude-opus-5", (t0 + timedelta(minutes=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")) + "\n")
        tr.refresh()
        self.assertEqual(tr.per["claude-opus-5"]["turns"], 2)
        self.assertEqual(tr.last_model, "claude-opus-5")
        self.assertEqual(metrics.context_size("claude-fable-5-1"), 1_000_000)
        self.assertEqual(metrics.price_for("claude-unknown-9"), metrics.DEFAULT_PRICE)


class SessionSummary(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_summary_counts_tagged_rows_and_prices_the_reread(self):
        os.environ["JEV_SESSION"] = "session:abcdef12"
        try:
            now = datetime.now()
            ledger.record({"ts": (now - timedelta(minutes=2)).isoformat(timespec="seconds"), "cmd": "sift", "agent": "session:abcdef12", "q": 40, "ms": 300, "cwd": str(self.home), "in": 10_000, "out": 0})
            ledger.record({"ts": (now - timedelta(minutes=1)).isoformat(timespec="seconds"), "cmd": "hook:guard", "agent": "session:abcdef12", "q": 2, "ms": 200, "cwd": "/elsewhere", "in": 0, "out": 0, "cached": True, "cached_in": 500})
            ledger.record({"ts": now.isoformat(timespec="seconds"), "cmd": "rank", "agent": "session:other000", "q": 9, "ms": 1, "cwd": "/elsewhere", "in": 999, "out": 0})
        finally:
            os.environ.pop("JEV_SESSION", None)
        p = self.home / "abcdef12-0000.jsonl"
        base = datetime.now(timezone.utc) - timedelta(minutes=3)
        p.write_text("\n".join(turn(f"m{i}", "claude-fable-5-1", (base + timedelta(seconds=30 * i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")) for i in range(7)) + "\n")
        s = metrics.session_summary(str(self.home), "abcdef12", p)
        j = s["jev"]
        self.assertEqual(j["decisions"], 42)
        self.assertEqual(j["tokens"], 10_500)
        self.assertEqual(j["requests"], 2)
        self.assertAlmostEqual(j["paid"], 10_000 / 1e6 * 0.042)
        self.assertAlmostEqual(j["once"], 10_500 / 1e6 * 10.0)
        self.assertGreater(j["reread"], 0, "turns after the rows are priced as cache reads")
        self.assertEqual(list(j["labels"])[0], "sift")
        self.assertEqual(s["model"]["turns"], 7)
        text = metrics.render_session(s, color=False)
        self.assertIn("went through jev", text)
        self.assertIn("42 decisions", text)
        self.assertIn("by command", text)
        self.assertIn("42 decisions", metrics.one_line(s))

    def test_no_transcript_no_rows(self):
        s = metrics.session_summary(str(self.home), None, None)
        self.assertIsNone(s["model"])
        self.assertEqual(s["jev"]["requests"], 0)
        self.assertIn("nothing decided", metrics.render_session(s, color=False))
        self.assertIsNone(metrics.find_transcript(str(self.home), None, None))


if __name__ == "__main__":
    unittest.main()
