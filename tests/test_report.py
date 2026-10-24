import json
import unittest
from datetime import datetime, timezone

from _fake import fresh_home
from jev import ledger, report, settings


def assistant(mid, ts, model="claude-opus-5-5", inp=10, out=20, cr=400_000, cw=0, sidechain=False, block=0):
    return json.dumps({"type": "assistant", "timestamp": ts, "isSidechain": sidechain, "uuid": f"{mid}-{block}",
                       "message": {"id": mid, "model": model, "usage": {"input_tokens": inp, "output_tokens": out,
                                                                         "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}})


class ReadSince(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_a_log_larger_than_any_tail_keeps_every_row_since(self):
        path = self.home / "big.log"
        with open(path, "w") as f:
            for day in range(1, 21):
                for i in range(600):
                    f.write(json.dumps({"ts": f"2026-10-{day:02d}T10:{i // 60:02d}:{i % 60:02d}", "cmd": "x" * 300}) + "\n")
        self.assertGreater(path.stat().st_size, 3 * ledger.SINCE_CHUNK)
        rows = ledger.read_since(path, "2026-10-15T00:00:00")
        self.assertEqual(len(rows), 6 * 600)
        self.assertEqual(rows[0]["ts"], "2026-10-15T10:00:00")
        self.assertEqual(len(ledger.read_since(path, "2026-09-01T00:00:00")), 20 * 600)
        self.assertEqual(ledger.read_since(self.home / "missing.log", "2026-10-01T00:00:00"), [])


class Report(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        self.projects = self.home / "projects"
        main = self.projects / "-work-shop" / "s1.jsonl"
        main.parent.mkdir(parents=True)
        main.write_text("\n".join([
            assistant("m1", "2026-10-20T10:00:00.000Z"),
            assistant("m1", "2026-10-20T10:00:00.000Z", block=1),  # the same request, a second content block
            assistant("m2", "2026-10-20T10:05:00.000Z", cr=800_000),
            assistant("m3", "2026-10-20T10:06:00.000Z", cr=1000, sidechain=True),
            json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-20T10:07:00.000Z",
                        "compactMetadata": {"trigger": "auto", "preTokens": 967_000, "durationMs": 150_000}}),
            assistant("m0", "2026-10-01T10:00:00.000Z"),  # before the span
        ]) + "\n")
        sub = self.projects / "-work-shop" / "s1" / "subagents" / "a1.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text(assistant("x1", "2026-10-20T11:00:00.000Z", model="claude-sonnet-5-5", cr=50_000) + "\n")

    def test_the_model_side_counts_each_request_once_and_reads_the_main_context(self):
        r = report.build(days=7, now=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc), root=self.projects)
        m = r["model"]
        self.assertEqual(m["requests"], 4)
        self.assertEqual(m["sessions"], 1)
        self.assertEqual(m["context"]["requests"], 2, "subagents and sidechains do not count as the session's context")
        self.assertEqual(m["context"]["over_700k"], 0.5)
        self.assertEqual(m["compactions"][0]["trigger"], "auto")
        opus = m["per_model"]["claude-opus-5-5"]
        self.assertEqual(opus["cr"], 400_000 + 800_000 + 1000)
        self.assertAlmostEqual(opus["usd"], round((30 * 4.0 + 60 * 20.0 + 1_201_000 * 0.20) / 1e6, 4))
        self.assertIn("1 by Claude Code (1 auto), median 2.5 min", report.render(r))

    def test_the_jev_side_by_caller_and_the_guard(self):
        usage = [{"ts": "2026-10-20T10:00:01", "cmd": "hook:guard", "agent": "session:abcd1234", "ms": 900, "in": 300},
                 {"ts": "2026-10-20T10:00:02", "cmd": "hook:guard", "agent": "session:abcd1234", "ms": 1800, "in": 300},
                 {"ts": "2026-10-20T10:05:00", "cmd": "hook:guard", "agent": "session:abcd1234", "ms": 700, "in": 300},
                 {"ts": "2026-10-20T10:06:00", "cmd": "rank", "agent": "", "ms": 1200, "in": 5000},
                 {"ts": "2026-10-20T10:06:30", "cmd": "ask", "agent": "shelflife", "ms": 900, "in": 900}]
        hooks = [{"ts": "2026-10-20T10:00:02", "hook": "guard", "cmd": "rm -rf build", "p": 0.95, "decision": "-", "held": "mod"},
                 {"ts": "2026-10-20T10:00:03", "hook": "guard", "cmd": "rm -rf build", "p": 0.95, "decision": "ask", "via": "mod"},
                 {"ts": "2026-10-20T10:00:09", "hook": "guard-answer", "answer": "refuse"},
                 {"ts": "2026-10-20T10:05:00", "hook": "guard", "cmd": "git status", "err": "NetworkError"},
                 {"ts": "2026-10-20T10:08:00", "hook": "compact-auto", "reason": "idle"}]
        j = report.jev_side(usage, hooks)
        self.assertEqual(list(j["callers"]), ["sessions", "untagged", "shelflife"])
        g = j["guard"]
        self.assertEqual((g["commands"], g["judged"], g["questions"], g["unchecked"]), (2, 1, 1, 1))
        self.assertEqual(g["answers"], {"run": 0, "refuse": 1, "dismissed": 0})
        self.assertEqual(report.guard_waits(usage, hooks), [1800, 700])
        self.assertEqual(j["compactions_by_jev"], 1)

    def test_a_logged_wait_is_used_as_it_is(self):
        hooks = [{"ts": "2026-10-20T10:00:02", "hook": "guard", "cmd": "ls", "p": 0.01, "ms": 640}]
        self.assertEqual(report.guard_waits([{"ts": "2026-10-20T10:00:01", "cmd": "hook:guard", "ms": 5000}], hooks), [640])


if __name__ == "__main__":
    unittest.main()
