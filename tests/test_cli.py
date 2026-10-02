import contextlib
import io
import json
import os
import unittest
from unittest import mock

from _fake import FakeTransport, fresh_home, FAKE_OR_KEY, FAKE_OR_KEY_2
from jev import settings
from jev.cli import COMMANDS, build_parser, main


def run(argv, transport=None, stdin=""):
    t = transport or FakeTransport()
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("jev.transport.Transport.request", lambda self, *a, **k: t.request(*a, **k)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch.dict("os.environ", {}):  # hooks may set JEV_AGENT; keep it per run
        code = main(argv)
    return code, out.getvalue(), err.getvalue(), t


class Parser(unittest.TestCase):
    def test_every_command_registers(self):
        p = build_parser()
        names = set(p._subparsers._group_actions[0].choices)
        self.assertTrue(set(COMMANDS) <= names, set(COMMANDS) - names)

    def test_partial_build_only_imports_one_module(self):
        p = build_parser("yes")
        names = set(p._subparsers._group_actions[0].choices)
        self.assertIn("yes", names)
        self.assertNotIn("watch", names)


class Commands(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_help_and_version(self):
        code, out, _, _ = run([])
        self.assertEqual(code, 0)
        self.assertIn("jev yes", out)
        code, out, _, _ = run(["version"])
        self.assertIn("jev 1.", out)

    def test_yes_exit_codes_and_json(self):
        code, out, _, _ = run(["yes", "Is `text` a yes?", "-s", "yes indeed"])
        self.assertEqual((code, out.strip()), (0, "0.900 yes"))
        code, out, _, _ = run(["yes", "q", "-s", "no way"])
        self.assertEqual(code, 1)
        code, out, _, _ = run(["yes", "q", "-s", "maybe", "--band", "0.4", "0.6", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["verdict"], "uncertain")

    def test_dry_run_prints_request_without_sending(self):
        code, out, _, t = run(["ask", "-s", "hola ñ", "--noul", "a", "Is `text` yes?", "--dry-run", "--api-key", ""])
        self.assertEqual(code, 0)
        body = json.loads(out)["body"]
        self.assertEqual(body["state"], "hola ñ")
        self.assertEqual(body["questions"]["a"]["type"], "noul")
        self.assertEqual(t.calls, [])

    def test_ask_fields_and_pick_rate(self):
        code, out, _, _ = run(["ask", "--field", "a=yes", "--field", "n=3", "--noul", "q1", "Is `a` a yes?", "--choice", "c", "which", "x=1", "y", "--json"])
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertAlmostEqual(r["answers"]["q1"]["noul"], 0.9)
        self.assertEqual(r["answers"]["c"]["choice"], "x")
        code, out, _, _ = run(["pick", "which", "x=1", "y", "-s", "s", "--min-confidence", "0.8"])
        self.assertEqual(code, 1)
        self.assertIn("uncertain", out)
        code, out, _, _ = run(["rate", "how", "lo", "mid", "hi", "-s", "yes"])
        self.assertEqual(code, 0)
        self.assertIn("/2", out)

    def test_rank_orders_filters_and_abstains(self):
        home = settings.HOME
        (home / "c.txt").write_text("no a\nyes b\nmaybe c\nyes d\n")
        code, out, err, t = run(["rank", "--query", "q", "--candidates-file", str(home / "c.txt"), "--abstain", "0.4", "0.6",
                                 "--uncertain-out", str(home / "u.txt"), "--json"])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual([x["candidate"] for x in r["results"]], ["yes b", "yes d", "no a"])
        self.assertEqual([x["candidate"] for x in r["uncertain"]], ["maybe c"])
        self.assertEqual((home / "u.txt").read_text(), "maybe c\n")
        self.assertEqual(r["requests"], 1)
        code, out, _, _ = run(["rank", "--query", "q", "--candidates-file", str(home / "c.txt"), "--min", "0.5", "--top", "1"])
        self.assertEqual(out.count("\n"), 1)

    def test_batch_writes_jsonl(self):
        home = settings.HOME
        (home / "rows.jsonl").write_text('{"id": 1, "text": "yes"}\n{"id": 2, "text": "maybe"}\n')
        code, out, err, _ = run(["batch", "--input", str(home / "rows.jsonl"), "--state-key", "text", "--noul", "q", "Is `text` yes?",
                                 "--abstain", "0.4", "0.6", "--out", str(home / "out.jsonl")])
        self.assertEqual(code, 0, err)
        rows = [json.loads(l) for l in (home / "out.jsonl").read_text().splitlines()]
        self.assertEqual([r["id"] for r in rows], [1, 2])
        self.assertEqual([r["uncertain"] for r in rows], [False, True])
        self.assertIn("1 uncertain", err)

    def test_tune_reports_a_threshold(self):
        home = settings.HOME
        rows = [{"text": f"yes {i}", "label": "y"} for i in range(6)] + [{"text": f"no {i}", "label": "n"} for i in range(6)]
        (home / "l.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        code, out, err, _ = run(["tune", "--labels", str(home / "l.jsonl"), "--positive", "y", "-Q", "Is `candidate` a yes?", "--json"])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual(r["questions"][0]["best"]["accuracy"], 1.0)
        code, out, err, _ = run(["tune", "--labels", str(home / "l.jsonl"), "--positive", "y", "-Q", "Is `candidate` a yes?"])
        self.assertIn("winner:", out)
        self.assertIn("jev rank", out)

    def test_cluster_groups(self):
        code, out, err, _ = run(["cluster", "--json"], stdin="yes a\nyes b\nno c\n")
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual(r["clusters"][0]["size"], 2)

    def test_sift_ranks_files(self):
        home = settings.HOME
        (home / "src").mkdir()
        (home / "src" / "a.py").write_text("yes yes yes")
        (home / "src" / "b.py").write_text("nothing here")
        code, out, err, _ = run(["sift", "--query", "yes", str(home / "src"), "--json"])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertTrue(r["results"][0]["path"].endswith("a.py"))
        self.assertIn("tokens", r["results"][0])

    def test_usage_and_cache_and_config(self):
        run(["yes", "q", "-s", "yes"])
        code, out, _, _ = run(["usage"])
        self.assertEqual(code, 0)
        self.assertIn("1 requests", out)
        code, out, _, _ = run(["usage", "--by", "cmd", "--json"])
        self.assertEqual(json.loads(out)["yes"]["requests"], 1)
        code, out, _, _ = run(["cache"])
        self.assertIn("1 entries", out)
        code, out, _, _ = run(["config", "set", "agent_price", "7"])
        self.assertEqual(code, 0)
        self.assertEqual(settings.agent_price(), 7.0)
        code, out, _, _ = run(["config", "set", "nope", "1"])
        self.assertEqual(code, 2)

    def test_scaffold_and_schema_and_guide(self):
        code, out, _, _ = run(["scaffold", "tagger"])
        self.assertIn('label="tagger"', out)
        code, out, _, _ = run(["schema"])
        self.assertIn('"questions"', out)
        code, out, _, _ = run(["guide", "--list"])
        self.assertIn("when", out)
        code, out, _, _ = run(["guide", "tune"])
        self.assertIn("76.2%", out)
        code, out, _, _ = run(["examples", "rank"])
        self.assertIn("jev rank", out)
        code, _, err, _ = run(["guide", "nope"])
        self.assertEqual(code, 2)

    def test_hooks_install_and_uninstall_edit_settings(self):
        home = settings.HOME
        st = home / "settings.json"
        st.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}, "other": 1}))
        code, out, _, _ = run(["hooks", "install", "--settings", str(st)])
        self.assertEqual(code, 0)
        cfg = json.loads(st.read_text())
        self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 2)
        self.assertTrue(any("hook guard" in h["hooks"][0]["command"] for h in cfg["hooks"]["PreToolUse"]))
        self.assertTrue(any("hook screen" in h["hooks"][0]["command"] for h in cfg["hooks"]["PostToolUse"]))
        self.assertEqual(cfg["other"], 1)
        code, out, _, _ = run(["hooks", "uninstall", "--settings", str(st)])
        cfg = json.loads(st.read_text())
        self.assertEqual(cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "echo mine")
        self.assertNotIn("PostToolUse", cfg["hooks"])

    def test_hook_guard_asks_and_skips_safe(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf yes"}, "permission_mode": "default", "session_id": "abcdef12"}
        code, out, _, _ = run(["hook", "guard"], stdin=json.dumps(payload))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        code, out, _, t = run(["hook", "guard"], stdin=json.dumps({**payload, "tool_input": {"command": "git status"}}))
        self.assertEqual((code, out, t.calls), (0, "", []))
        code, out, _, _ = run(["hook", "guard"], stdin=json.dumps({**payload, "permission_mode": "bypassPermissions"}))
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        code, out, _, _ = run(["hook", "guard"], stdin="not json")
        self.assertEqual((code, out), (0, ""))

    def test_hook_screen_warns(self):
        payload = {"tool_name": "WebFetch", "tool_input": {"url": "http://x"}, "tool_response": {"content": "yes " * 40}}
        code, out, _, _ = run(["hook", "screen"], stdin=json.dumps(payload))
        self.assertIn("additionalContext", out)

    def test_statusline_render_and_install(self):
        home = settings.HOME
        st = home / "settings.json"
        st.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo old"}}))
        code, out, _, _ = run(["statusline", "install", "--settings", str(st)])
        self.assertEqual(code, 0)
        cfg = json.loads(st.read_text())
        self.assertTrue(cfg["statusLine"]["command"].endswith("statusline render"))
        self.assertEqual(settings.config()["statusline_prev"], "echo old")
        sample = {"session_id": "abc", "model": {"display_name": "M"}, "cost": {"total_cost_usd": 1.5}, "context_window": {"used_percentage": 10}}
        code, out, _, _ = run(["statusline", "render"], stdin=json.dumps(sample))
        self.assertIn("$1.50", out)
        self.assertIn("jev", out)
        code, out, _, _ = run(["statusline", "uninstall", "--settings", str(st)])
        self.assertEqual(json.loads(st.read_text())["statusLine"]["command"], "echo old")

    def test_session_q_and_saved_questions(self):
        home = settings.HOME
        code, out, err, _ = run(["session", "--plain", "--cwd", str(home)])
        self.assertEqual(code, 0, err)
        self.assertIn("nothing decided", out)
        rows = [{"text": f"yes {i}", "label": "y"} for i in range(6)] + [{"text": f"no {i}", "label": "n"} for i in range(6)]
        (home / "l.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        code, out, err, _ = run(["tune", "--labels", str(home / "l.jsonl"), "--positive", "y", "-Q", "Is `candidate` a yes?", "--save", "isyes", "--json"])
        self.assertEqual(code, 0, err)
        self.assertIn("saved as 'isyes'", err)
        code, out, _, _ = run(["q", "list"])
        self.assertIn("isyes", out)
        code, out, _, _ = run(["q", "show", "isyes", "--json"])
        spec = json.loads(out)
        self.assertEqual(spec["question"], "Is `candidate` a yes?")
        self.assertIn("measured", spec)
        code, out, _, _ = run(["yes", "--q", "isyes", "-s", "yes indeed"])
        self.assertEqual(code, 0)
        code, out, err, _ = run(["rank", "--q", "isyes", "--query", "q", "yes a", "no b", "maybe c", "--json"])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual([x["candidate"] for x in r["uncertain"]], ["maybe c"])
        code, out, err, _ = run(["batch", "--q", "isyes", "--input", "-", "--text-lines"], stdin="yes\n")
        self.assertEqual(code, 0, err)
        self.assertIn("isyes", json.loads(out.splitlines()[0])["answers"])
        code, out, _, _ = run(["q", "save", "manual", "-Q", "Is `candidate` x?", "--threshold", "0.6", "--band", "0.5", "0.7"])
        self.assertEqual(code, 0)
        code, out, _, _ = run(["q", "rm", "manual"])
        self.assertEqual(code, 0)
        code, _, _, _ = run(["yes", "--q", "manual", "-s", "x"])
        self.assertEqual(code, 2)
        code, out, _, _ = run(["session", "--plain", "--cwd", os.getcwd(), "--json"])
        self.assertGreater(json.loads(out)["jev"]["requests"], 0)

    def test_auth_backend_detection_and_config_backend(self):
        home = settings.HOME
        code, out, _, _ = run(["auth", "set", FAKE_OR_KEY])
        self.assertEqual(code, 0, out)
        self.assertIn("backend openrouter", out)
        self.assertEqual(settings.config()["base_url"], "https://openrouter.ai/api")
        self.assertEqual(oct(settings.KEY_FILE.stat().st_mode & 0o777), "0o600")
        code, out, _, _ = run(["config", "set", "backend", "typesafe"])
        self.assertEqual(settings.config()["model"], "jev-latest")
        code, out, _, _ = run(["config", "unset", "backend"])
        self.assertNotIn("base_url", settings.config())
        code, out, _, _ = run(["config", "set", "backend", "nope"])
        self.assertEqual(code, 2)
        code, out, _, _ = run(["config"])
        self.assertIn("backend", out)

    def test_hook_session_start_tags_and_configures(self):
        home = settings.HOME
        env_file = home / "env.sh"
        env = {"CLAUDE_ENV_FILE": str(env_file), "CLAUDE_PLUGIN_OPTION_API_KEY": FAKE_OR_KEY_2, "CLAUDE_PLUGIN_OPTION_BACKEND": ""}
        payload = {"session_id": "deadbeef-1234", "source": "startup", "cwd": str(home), "transcript_path": str(home / "t.jsonl"), "hook_event_name": "SessionStart"}
        with mock.patch.dict("os.environ", env):
            code, out, _, _ = run(["hook", "session-start"], stdin=json.dumps(payload))
            code2, out2, _, _ = run(["hook", "session-start"], stdin=json.dumps({**payload, "source": "resume"}))
        self.assertEqual(code, 0)
        self.assertIn("additionalContext", out)
        self.assertEqual(out2, "", "no context line on resume")
        self.assertEqual(env_file.read_text().count("JEV_SESSION"), 1, "written once")
        self.assertIn('export JEV_SESSION="session:deadbeef"', env_file.read_text())
        self.assertEqual(settings.KEY_FILE.read_text().strip(), FAKE_OR_KEY_2)
        self.assertEqual(settings.config()["base_url"], "https://openrouter.ai/api")
        marker = json.loads((settings.SESSIONS_DIR / "deadbeef-1234.json").read_text())
        self.assertEqual(marker["transcript"], str(home / "t.jsonl"))

    def test_usage_by_session(self):
        with mock.patch.dict("os.environ", {"JEV_SESSION": "session:abc12345"}):
            run(["yes", "q", "-s", "yes"])
        code, out, _, _ = run(["usage", "--by", "session", "--json"])
        self.assertIn("session:abc12345", json.loads(out))

    def test_watch_once_without_transcript(self):
        code, out, _, _ = run(["watch", "--once", "--cwd", str(settings.HOME)])
        self.assertEqual(code, 0)
        self.assertIn("no transcript found", out)


if __name__ == "__main__":
    unittest.main()
