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
        self.assertEqual(j["kept_out"], 10_000, "the guard's tokens went through jev but are not text the agent avoided reading")
        self.assertEqual(j["overhead"], 500)
        self.assertAlmostEqual(j["once"], 10_000 / 1e6 * 10.0, msg="read once on the next turn, at Fable 5.1's input price")
        self.assertGreater(j["reread"], 0, "turns after the rows are priced as cache reads")
        self.assertEqual(j["pricing"], "per-model")
        self.assertEqual(list(j["labels"]), ["sift"])
        self.assertEqual(list(j["hook_labels"]), ["guard"])
        self.assertEqual(s["model"]["turns"], 7)
        text = metrics.render_session(s, color=False)
        self.assertIn("went through jev", text)
        self.assertIn("42 decisions", text)
        self.assertIn("by command", text)
        self.assertIn("safety checks", text)
        self.assertIn("42 decisions", metrics.one_line(s))

    def test_no_transcript_no_rows(self):
        s = metrics.session_summary(str(self.home), None, None)
        self.assertIsNone(s["model"])
        self.assertEqual(s["jev"]["requests"], 0)
        self.assertIn("nothing decided", metrics.render_session(s, color=False))
        self.assertIsNone(metrics.find_transcript(str(self.home), None, None))


def ts(minutes):
    return (datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


class Prices(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_current_models(self):
        self.assertEqual(metrics.price_for("claude-opus-5-5"), (4.0, 20.0, 0.20, 5.0))
        self.assertEqual(metrics.price_for("claude-opus-5"), (5.0, 25.0, 0.5, 6.25))
        self.assertEqual(metrics.price_for("claude-sonnet-5-5"), (2.0, 10.0, 0.20, 2.5))
        self.assertEqual(metrics.price_for("claude-fable-5-1"), (10.0, 50.0, 0.25, 12.5))
        self.assertEqual(metrics.price_for("claude-haiku-4-5"), (1.0, 5.0, 0.1, 1.25))
        self.assertEqual(metrics.context_size("claude-haiku-4-5"), 200_000)
        self.assertTrue(metrics.reads_for_agent("sift") and metrics.reads_for_agent("mcp:rank") and metrics.reads_for_agent("lib"))
        self.assertFalse(metrics.reads_for_agent("hook:screen") or metrics.reads_for_agent("inspect") or metrics.reads_for_agent("tune"))


class WouldHaveCost(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_each_turn_at_its_own_model_until_the_compaction(self):
        turns = [(ts(1), "claude-fable-5-1", 100_000), (ts(2), "claude-fable-5-1", 100_000),
                 (ts(3), "claude-opus-5-5", 100_000), (ts(5), "claude-opus-5-5", 100_000)]
        once, reread = metrics.would_have_cost([(ts(0), 100_000)], turns, [ts(4)])
        self.assertAlmostEqual(once, 0.1 * 10.0, msg="once, as input, on the first turn after it, at Fable 5.1's price")
        self.assertAlmostEqual(reread, 0.1 * 0.25 + 0.1 * 0.20, msg="re-read at each later turn's own cache price; nothing after the compaction")

    def test_text_that_would_not_have_fit_stops_being_re_read(self):
        turns = [(ts(1), "claude-fable-5-1", 100_000), (ts(2), "claude-fable-5-1", 850_000), (ts(3), "claude-fable-5-1", 100_000)]
        once, reread = metrics.would_have_cost([(ts(0), 100_000)], turns, [])
        self.assertAlmostEqual(once, 1.0)
        self.assertEqual(reread, 0.0, "850k + 100k is past the compaction bar of a 1M window: the text would have been dropped")

    def test_flat_price_and_text_after_the_last_turn(self):
        turns = [(ts(1), "claude-opus-5-5", 1_000)]
        once, reread = metrics.would_have_cost([(ts(0), 1_000_000), (ts(9), 1_000_000)], turns, [], flat_price=3.0)
        self.assertAlmostEqual(once, 6.0, msg="the person's own price, for both; the second is read by a turn still to come")
        self.assertEqual(reread, 0.0)

    def test_the_transcript_records_compactions_and_main_thread_turns(self):
        home = fresh_home()
        p = home / "s.jsonl"
        lines = [turn("m1", "claude-fable-5-1", ts(1) + ".000Z"),
                 json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": ts(2) + ".000Z"}),
                 turn("m2", "claude-opus-5-5", ts(3) + ".000Z"),
                 json.dumps({"type": "assistant", "isSidechain": True, "timestamp": ts(4) + ".000Z",
                             "message": {"id": "s1", "model": "claude-haiku-4-5", "usage": {"input_tokens": 5, "output_tokens": 5}}})]
        p.write_text("\n".join(lines) + "\n")
        tr = metrics.Transcript(p)
        tr.refresh()
        self.assertEqual(tr.compactions, [ts(2)])
        self.assertEqual([m for _, m, _ in tr.turns], ["claude-fable-5-1", "claude-opus-5-5"], "a subagent's turn is not the main context")
        self.assertIn("claude-haiku-4-5", tr.per, "but it is still paid for")


class SafetyAndRouting(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_counts_what_jev_caught_and_adds_subagent_savings(self):
        os.environ["JEV_AGENT"] = "session:abcdef12"
        try:
            ledger.log_hook("guard", {"cmd": "rm -rf x", "p": 0.9, "decision": "ask"})
            ledger.log_hook("guard", {"cmd": "ls", "p": 0.1, "decision": "-"})
            ledger.log_hook("screen", {"src": "https://x", "p": 0.8, "warned": True})
            ledger.log_hook("screen", {"src": "https://y", "p": 0.1, "warned": False})
            ledger.log_hook("inspect", {"files": 9, "sent": 2, "flagged": 1, "new_flags": 1})
            ledger.log_hook("triage", {"cmd": "pytest", "noted": True})
            ledger.log_hook("evidence", {})
            ledger.log_hook("subagent", {"parent": "claude-fable-5-1", "model": "claude-sonnet-5-5", "spent": 1.0, "saved_usd": 4.0})
            ledger.log_hook("effort", {"conf": 0.9})
            ledger.log_hook("effort-cache", {"verdict": "keeps", "version": "2.1.287"})
        finally:
            os.environ.pop("JEV_AGENT", None)
        j = metrics.session_summary(str(self.home), "abcdef12", None)["jev"]
        self.assertEqual(j["safety"], {"asked": 1, "pages_flagged": 1, "files_flagged": 1, "triaged": 1, "claims": 1, "checks": 5})
        self.assertEqual(j["routing"]["subagents"], 1)
        self.assertAlmostEqual(j["saved_total"], j["saved"] + 4.0)
        self.assertEqual((j["routing"]["effort_turns"], j["routing"]["effort_cache"]), (1, "keeps"))
        text = metrics.render_session(metrics.session_summary(str(self.home), "abcdef12", None), color=False)
        self.assertIn("1 fetched page(s) flagged", text)
        self.assertIn("1 ran on a cheaper model", text)
        self.assertIn("the prompt cache survives it", text)
        self.assertIn("nothing from the Claude plan", text)


class Plan(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_learns_points_per_dollar_and_prices_the_kept_out_text(self):
        r = lambda five, week: {"five_hour": {"pct": five, "resets": "A"}, "seven_day": {"pct": week, "resets": "W"}}  # noqa: E731
        self.assertIsNone(metrics.plan_view("s1", 10.0, None, now=100), "off a subscription there is no plan")
        metrics.plan_record("s1", 1.0, r(10.0, 2.0), now=100)
        for i in range(1, 4):
            metrics.plan_record("s1", 1.0 + 2 * i, r(10.0 + i, 2.0 + 0.2 * i), now=100 + i)
        view = metrics.plan_view("s1", 10.0, now=104)
        five, week = view["windows"]["five_hour"], view["windows"]["seven_day"]
        self.assertEqual(view["billing"], "subscription")
        self.assertAlmostEqual(five["rate"], 0.5, places=2, msg="3 points over $6")
        self.assertAlmostEqual(five["kept_free"], 5.0, places=1, msg="$10 of text at 0.5 points a dollar")
        self.assertAlmostEqual(week["kept_free"], 1.0, places=1)
        self.assertEqual(five["used"], 13.0)
        self.assertIn("≈ 5.0% of the 5-hour window", metrics.plan_line(view))
        self.assertIn("of the week", metrics.plan_line(view))

    def test_a_reset_a_rollback_or_another_session_teaches_nothing(self):
        metrics.plan_record("s1", 1.0, {"five_hour": {"pct": 50.0, "resets": "A"}}, now=100)
        metrics.plan_record("s1", 3.0, {"five_hour": {"pct": 1.0, "resets": "B"}}, now=101)   # the window reset
        metrics.plan_record("s2", 1.0, {"five_hour": {"pct": 2.0, "resets": "B"}}, now=102)   # another session, active
        metrics.plan_record("s1", 9.0, {"five_hour": {"pct": 9.0, "resets": "B"}}, now=103)   # its interval overlaps s2
        metrics.plan_record("s1", 8.0, {"five_hour": {"pct": 9.5, "resets": "B"}}, now=104)   # the session's figure went back
        rates = json.loads((settings.HOME / "plan.json").read_text())["rates"]
        self.assertEqual(rates, {})
        self.assertIsNone(metrics.plan_view("s1", 10.0, now=105)["windows"]["five_hour"]["kept_free"], "still measuring")
        self.assertIn("measuring", metrics.plan_line(metrics.plan_view("s1", 10.0, now=105)))

    def test_a_window_left_out_keeps_its_last_reading_until_it_resets(self):
        import time as _time
        base = _time.time()
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        later, soon = iso(base + 36_000), iso(base + 150)
        metrics.plan_record("s1", 1.0, {"five_hour": {"pct": 4.0, "resets": soon}, "seven_day": {"pct": 22.0, "resets": later}}, now=base)
        metrics.plan_record("s1", 2.0, {"seven_day": {"pct": 22.5, "resets": later}}, now=base + 60)
        view = metrics.plan_view("s1", 1.0, now=base + 100)
        self.assertEqual(view["windows"]["five_hour"]["used"], 4.0, "kept from the reading before")
        self.assertEqual(view["windows"]["seven_day"]["used"], 22.5)
        self.assertEqual(view["missing"], [])
        view = metrics.plan_view("s1", 1.0, now=base + 200)
        self.assertNotIn("five_hour", view["windows"], "a window that reset is not shown")
        self.assertEqual(view["missing"], ["five_hour"])

    def test_per_model_weekly_windows_are_kept_and_named(self):
        future = "2099-01-01T00:00:00Z"
        metrics.plan_record("s1", 1.0, {"seven_day": {"pct": 25.0, "resets": future}, "seven_day_fable": {"pct": 24.0, "resets": future},
                                        "spend_limit": {"pct": 5.0, "resets": future}}, now=1000)
        view = metrics.plan_view("s1", 1.0, now=1001)
        self.assertEqual([w["label"] for w in view["windows"].values()], ["week", "week, Fable"])

    def test_parse(self):
        self.assertEqual(metrics.parse_plan(["five_hour=23.5@2026-10-02T14:00:00Z", "seven_day=4", "bogus"]),
                         {"five_hour": {"pct": 23.5, "resets": "2026-10-02T14:00:00Z"}, "seven_day": {"pct": 4.0, "resets": None}})


if __name__ == "__main__":
    unittest.main()
