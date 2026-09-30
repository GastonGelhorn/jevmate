import json
import os
import unittest
from unittest import mock

from _fake import FakeTransport, fresh_home
from jev import ledger, settings
from test_cli import run

PYTEST_OUT = "\n".join([
    "___________ tests/test_a.py::test_1 ___________", "    row = payload['user_id']", "E   KeyError: 'user_id'", "",
    "___________ tests/test_b.py::test_2 ___________", "    row = payload['user_id']", "E   KeyError: 'user_id'  yes", "",
    "___________ tests/test_c.py::test_3 ___________", "    sock.connect(('127.0.0.1', 6379))", "E   ConnectionRefusedError: [Errno 61] Connection refused", "",
    "=========== 3 failed in 1.2s ===========", ""])


def bash_payload(cmd, stdout="", stderr="", exit_code=0, event="PostToolUse", **extra):
    return json.dumps({"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "abcdef12-0000", "cwd": str(settings.HOME),
                       "tool_response": {"stdout": stdout, "stderr": stderr, "exit_code": exit_code, "interrupted": False}, **extra})


class AfterBash(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_records_ran_and_triages_a_red_suite(self):
        code, out, err, t = run(["hook", "after-bash"], stdin=bash_payload("pytest -q", stdout=PYTEST_OUT, exit_code=1, event="PostToolUseFailure",
                                                                              scratchpad_dir=str(self.home)))
        self.assertEqual(code, 0, err)
        rows = ledger.hook_rows()
        self.assertEqual(rows[0]["hook"], "ran")
        self.assertFalse(rows[0]["ok"])
        ctx = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUseFailure")
        self.assertIn("jev triage: 3 failures", ctx["additionalContext"])
        self.assertIn("cause(s)", ctx["additionalContext"])
        self.assertTrue((self.home / "jev-failures.txt").exists())
        self.assertIn("jev cluster -i", ctx["additionalContext"])

    def test_silent_on_a_passing_command_and_short_output(self):
        code, out, _, t = run(["hook", "after-bash"], stdin=bash_payload("pytest -q", stdout="3 passed", exit_code=0))
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(t.calls, [])
        self.assertTrue(ledger.hook_rows()[-1]["ok"])

    def test_screens_curled_content(self):
        code, out, _, _ = run(["hook", "after-bash"], stdin=bash_payload("curl -s https://example.com/page", stdout="yes " * 40))
        self.assertIn("jev screen", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        code, out, _, t = run(["hook", "after-bash"], stdin=bash_payload("ls -la", stdout="yes " * 40))
        self.assertEqual(out, "", "ls is not remote content")

    def test_triage_off(self):
        with mock.patch.dict("os.environ", {"CLAUDE_PLUGIN_OPTION_TRIAGE_MODE": "off"}):
            code, out, _, t = run(["hook", "after-bash"], stdin=bash_payload("pytest", stdout=PYTEST_OUT, exit_code=1))
        self.assertEqual(out, "")
        self.assertEqual(t.calls, [])


class GuardMemoryAndRules(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def guard(self, cmd, **kw):
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "permission_mode": "default", "session_id": "abcdef12-0000", "cwd": str(self.home), **kw}
        return run(["hook", "guard"], stdin=json.dumps(payload))

    def test_does_not_ask_twice_for_a_command_that_ran(self):
        code, out, _, t = self.guard("rm -rf yes-build")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        run(["hook", "after-bash"], stdin=bash_payload("rm -rf yes-build", stdout="", exit_code=0))
        code, out, _, t = self.guard("rm -rf yes-build")
        self.assertEqual((out, t.calls), ("", []))
        self.assertEqual(ledger.hook_rows()[-1]["why"], "ran-before")

    def test_project_rules(self):
        (self.home / ".jev").mkdir()
        (self.home / ".jev" / "guard.json").write_text(json.dumps({"safe": [r"^tools/checksums\.sh"], "ask": [r"^deploy\b"]}))
        code, out, _, t = self.guard("tools/checksums.sh && rm -rf yes")
        self.assertEqual((out, t.calls), ("", []))
        code, out, _, t = self.guard("deploy production")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(t.calls, [])
        self.assertIn("guard.json", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])


class Route(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_off_by_default_and_hints_on_routine_prompts_only(self):
        routine = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "rename the helper in utils.py and fix the two call sites", "session_id": "s"})
        code, out, _, t = run(["hook", "route"], stdin=routine)
        self.assertEqual((out, t.calls), ("", []), "opt-in: nothing without route_mode on")
        with mock.patch.dict("os.environ", {"JEV_ROUTE_MODE": "on"}):
            # the fake scores 0.9 * top on "yes" -> level 3 (hard): no hint; 0.1 * top -> level 0 (lookup): hint at confidence 0.8
            code, out, _, _ = run(["hook", "route"], stdin=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "yes " * 20, "session_id": "s"}))
            self.assertEqual(out, "")
            code, out, _, _ = run(["hook", "route"], stdin=routine)
            ctx = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(ctx["hookEventName"], "UserPromptSubmit")
            self.assertIn("jev route", ctx["additionalContext"])
            for skipped in ("/jev:stats", "[Image: source: /tmp/x.png] " + "a" * 40, '@"/Users/x/file.sql" ' + "b" * 40):
                code, out, _, t = run(["hook", "route"], stdin=json.dumps({"prompt": skipped, "session_id": "s"}))
                self.assertEqual((out, t.calls), ("", []), skipped[:12])
            with mock.patch.dict("os.environ", {"JEV_ROUTE_CONF": "0.95"}):
                code, out, _, _ = run(["hook", "route"], stdin=routine)
                self.assertEqual(out, "", "below the confidence bar: no hint")


class Stop(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def transcript(self, commands):
        p = self.home / "t.jsonl"
        lines = [json.dumps({"type": "user", "message": {"content": "please run the tests"}})]
        for c in commands:
            lines.append(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": c}}]}}))
            lines.append(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}))
        p.write_text("\n".join(lines) + "\n")
        return str(p)

    def test_blocks_only_when_enabled_and_the_claim_is_unbacked(self):
        payload = {"hook_event_name": "Stop", "stop_hook_active": False, "session_id": "s", "transcript_path": self.transcript([]),
                   "last_assistant_message": "Done. I ran the suite and all tests pass, yes, everything is green now."}
        code, out, _, t = run(["hook", "stop"], stdin=json.dumps(payload))
        self.assertEqual((out, t.calls), ("", []), "off by default")
        with mock.patch.dict("os.environ", {"JEV_HONESTY_MODE": "on"}):
            code, out, _, _ = run(["hook", "stop"], stdin=json.dumps(payload))
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["decision"], "block")
            code, out, _, _ = run(["hook", "stop"], stdin=json.dumps({**payload, "stop_hook_active": True}))
            self.assertEqual(out, "", "never twice in a row")
            backed = {**payload, "transcript_path": self.transcript(["pytest -q yes"])}
            code, out, _, _ = run(["hook", "stop"], stdin=json.dumps(backed))
            self.assertEqual(out, "", "the commands ran the tests: no block")


class HooksTune(unittest.TestCase):
    def test_reports_pairs(self):
        fresh_home()
        for i in range(25):
            ledger.log_hook("guard", {"cmd": f"cmd {i}", "p": 0.3 + i * 0.02, "decision": "ask" if i > 12 else "-", "mode": "default"})
            if i % 3:  # most commands ran afterwards
                ledger.log_hook("ran", {"cmd": f"cmd {i}", "ok": True})
        ledger.log_hook("ran", {"cmd": "much later", "ok": True})
        code, out, _, _ = run(["hooks", "tune"])
        self.assertEqual(code, 0)
        self.assertIn("guard decisions with a p since", out)
        self.assertIn(": 25 ·", out)


class BatchResume(unittest.TestCase):
    def test_resume_skips_done_rows(self):
        home = fresh_home()
        (home / "rows.jsonl").write_text('{"id": 1, "text": "yes"}\n{"id": 2, "text": "no"}\n{"id": 3, "text": "yes"}\n')
        (home / "out.jsonl").write_text('{"line": 0, "answers": {}, "usage": {}, "id": 1}\n')
        code, out, err, t = run(["batch", "--input", str(home / "rows.jsonl"), "--state-key", "text", "--noul", "q", "Is `text` yes?", "--out", str(home / "out.jsonl"), "--resume"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(t.calls), 2)
        rows = [json.loads(l) for l in (home / "out.jsonl").read_text().splitlines()]
        self.assertEqual([r["id"] for r in rows], [1, 2, 3])
        self.assertIn("1 skipped", err)


class Packs(unittest.TestCase):
    def test_list_and_install(self):
        fresh_home()
        code, out, _, _ = run(["q", "packs"])
        self.assertIn("core", out)
        self.assertIn("bug_fix_commit", out)
        code, out, _, _ = run(["q", "install", "core"])
        self.assertEqual(code, 0)
        self.assertIn("installed 10", out)
        code, out, _, _ = run(["q", "install", "core"])
        self.assertIn("kept 10", out)
        code, out, _, _ = run(["yes", "--q", "destructive_command", "-s", "rm -rf yes"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
