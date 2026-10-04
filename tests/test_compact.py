import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from _fake import FakeTransport, fresh_home
from jev import compact, ledger, settings
from jev.client import Client
from test_cli import run

BIG = "x" * 2_000


def say(text, role="user"):
    return ("say", role, text, role == "user")


def call(i, tool, inp):
    return ("call", f"t{i}", tool, inp)


def res(i, text, error=False):
    return ("result", f"t{i}", text, error)


def claude_line(**o):
    return json.dumps(o, separators=(",", ":"))


def write_claude(path: Path, before_boundary: bool = True) -> None:
    lines = []
    if before_boundary:
        lines += [claude_line(type="user", sessionId="s", message={"role": "user", "content": "an old request"}),
                  claude_line(type="assistant", message={"content": [{"type": "tool_use", "id": "old1", "name": "Read", "input": {"file_path": "/r/old.py"}}]}),
                  claude_line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "old1", "content": BIG}]}),
                  claude_line(type="system", subtype="compact_boundary", content="Conversation compacted")]
    lines += [claude_line(type="user", isCompactSummary=True, message={"role": "user", "content": "summary of before"}),
              claude_line(type="user", message={"role": "user", "content": "fix the retry bug in src/queue.py"}),
              claude_line(type="assistant", message={"content": [{"type": "text", "text": "Reading the queue."}]}),
              claude_line(type="assistant", message={"content": [{"type": "tool_use", "id": "a1", "name": "Read", "input": {"file_path": "/r/src/queue.py"}}]}),
              claude_line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "a1", "content": [{"type": "text", "text": BIG}]}]}),
              claude_line(type="assistant", isSidechain=True, message={"content": [{"type": "tool_use", "id": "side", "name": "Read", "input": {}}]})]
    path.write_text("\n".join(lines) + "\n")


class Reading(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_claude_transcript_from_the_last_compaction(self):
        p = self.home / "t.jsonl"
        write_claude(p)
        events, host = compact.load(p)
        self.assertEqual(host, "claude")
        ids = [e[1] for e in events if e[0] == "call"]
        self.assertEqual(ids, ["a1"], "only what follows the last compaction, and no sidechain")
        prompts = [e[2] for e in events if e[0] == "say" and e[3]]
        self.assertEqual(prompts, ["fix the retry bug in src/queue.py"], "the compaction summary is not a prompt")

    def test_codex_rollout_replays_the_replacement_history(self):
        p = self.home / "rollout.jsonl"
        rec = lambda kind, payload: json.dumps({"timestamp": "t", "type": kind, "payload": payload}, separators=(",", ":"))  # noqa: E731
        p.write_text("\n".join([
            rec("session_meta", {"cwd": "/r"}),
            rec("response_item", {"type": "function_call", "name": "exec_command", "call_id": "gone", "arguments": json.dumps({"cmd": "ls"})}),
            rec("compacted", {"message": "", "replacement_history": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "codex context"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "make the tests pass"}]}]}),
            rec("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}),
            rec("response_item", {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": json.dumps({"cmd": "cat src/a.py"})}),
            rec("response_item", {"type": "function_call_output", "call_id": "c1", "output": [{"type": "input_text", "text": BIG}]}),
            rec("response_item", {"type": "reasoning", "encrypted_content": "zzz"})]) + "\n")
        events, host = compact.load(p)
        self.assertEqual(host, "codex")
        self.assertEqual([e[1] for e in events if e[0] == "call"], ["c1"])
        self.assertEqual([e[2] for e in events if e[0] == "say" and e[3]], ["make the tests pass"], "developer text and injected context are not prompts")

    def test_messages_from_the_mod(self):
        msgs = [{"role": "user", "text": "go", "toolUses": []},
                {"role": "assistant", "text": "", "toolUses": [{"tool_use_id": "u1", "tool": "Bash", "input": {"command": "make"}, "text": BIG}]},
                {"role": "user", "text": "", "toolUses": [], "toolResults": [{"tool_use_id": "u1", "text": BIG, "isError": False}]}]
        items, _ = compact._items(compact.from_messages(msgs))
        self.assertEqual([(i["id"], len(i["text"])) for i in items], [("u1", 2000)])


class Rules(unittest.TestCase):
    def plan(self, events, **kw):
        fresh_home()
        return compact.build(events, client=None, recent=kw.pop("recent", 0), **kw)

    def why(self, plan):
        return {r["id"]: (r["action"], r["why"]) for r in plan["decisions"]}

    def test_a_file_edited_or_read_again_later(self):
        p = self.plan([say("go"), call(1, "Read", {"file_path": "/r/a.py"}), res(1, BIG), call(2, "Edit", {"file_path": "/r/a.py"}), res(2, "ok"),
                       call(3, "Read", {"file_path": "/r/b.py", "offset": 1, "limit": 50}), res(3, BIG), call(4, "Read", {"file_path": "/r/b.py"}), res(4, BIG)])
        w = self.why(p)
        self.assertEqual(w["t1"], ("move", "edited-later"))
        self.assertEqual(w["t3"], ("move", "read-again"))
        self.assertEqual(w["t4"], ("keep", "unjudged"), "the latest read of b.py is not superseded")

    def test_shell_reads_relative_paths_and_codex_patches(self):
        patch = 'await tools.apply_patch({input: "*** Begin Patch\\n*** Update File: src/a.py\\n@@\\n-x\\n+y\\n*** End Patch"})'
        p = self.plan([say("go"), call(1, "Bash", {"command": "cd /r && sed -n '1,200p' src/a.py"}), res(1, BIG),
                       call(2, "exec", {"input": patch}), res(2, "Script completed"),
                       call(3, "Bash", {"command": "cat src/a.py src/c.py"}), res(3, BIG),
                       call(4, "Write", {"file_path": "/r/src/a.py", "content": "y"}), res(4, "written")])
        w = self.why(p)
        self.assertEqual(w["t1"], ("move", "edited-later"), "a relative path matches the absolute one")
        self.assertEqual(w["t3"][0], "keep", "c.py was not touched, so the result still says something")

    def test_a_command_run_again_and_a_retried_error(self):
        p = self.plan([say("go"), call(1, "Bash", {"command": "pytest -q"}), res(1, BIG), call(2, "Bash", {"command": "pytest  -q"}), res(2, BIG),
                       call(3, "Read", {"file_path": "/r/x.py"}), res(3, BIG + " no such file", error=True), call(4, "Read", {"file_path": "/r/x.py", "limit": 9}), res(4, "ok")])
        w = self.why(p)
        self.assertEqual(w["t1"], ("move", "ran-again"))
        self.assertEqual(w["t3"], ("move", "retried"))

    def test_the_latest_small_and_earlier_moved_results_stay(self):
        p = self.plan([say("go"), call(1, "Read", {"file_path": "/r/a.py"}), res(1, BIG), call(2, "Read", {"file_path": "/r/a.py"}), res(2, BIG),
                       call(3, "Bash", {"command": "ls"}), res(3, "short"), call(4, "Bash", {"command": "make"}), res(4, compact.STUB + " moved before")], recent=1)
        w = self.why(p)
        self.assertEqual(w["t1"], ("move", "read-again"))
        self.assertEqual(w["t2"], ("keep", "recent"))
        self.assertEqual(w["t3"], ("keep", "small"))
        self.assertNotIn("t4", w, "what an earlier compaction moved out is left as it is")

    def test_without_jev_the_block_repeats_nothing(self):
        p = self.plan([say("go"), call(1, "Read", {"file_path": "/r/a.py"}), res(1, BIG), call(2, "Bash", {"command": "make"}), res(2, BIG)],
                      apply=True, tag="session:abcd1234", recent=1)
        self.assertEqual(p["stats"]["repeated"], 0)
        self.assertNotIn("##", p["restore"], "recency alone does not say what the work still needs")
        self.assertNotIn("repeated below", p["restore"])
        self.assertIn("002-Bash.txt: Bash make", p["restore"])

    def test_a_written_file_shortens_the_call(self):
        p = self.plan([say("go"), call(1, "Write", {"file_path": "/r/new.py", "content": BIG}), res(1, "File created"), call(2, "Bash", {"command": "ls"}), res(2, "a")])
        inputs = [c for c in p["changes"] if c["kind"] == "input"]
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["input"]["file_path"], "/r/new.py")
        self.assertIn("written to /r/new.py", inputs[0]["input"]["content"])


class Judged(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def build(self, events, transport=None, **kw):
        t = transport or FakeTransport()
        with mock.patch("jev.transport.Transport.request", lambda self, *a, **k: t.request(*a, **k)):
            kw.setdefault("recent", 0)
            return compact.build(events, client=Client(label="hook:compact", retries=0), **kw), t

    def test_kept_cut_and_moved_by_the_bars(self):
        events = [say("fix the retry bug"), call(1, "Bash", {"command": "grep -rn retry"}), res(1, "yes " + BIG), call(2, "Bash", {"command": "npm ls"}),
                  res(2, "maybe " + BIG), call(3, "Bash", {"command": "du -sh *"}), res(3, BIG), say("Found it.", "assistant")]
        plan, t = self.build(events)
        w = {r["id"]: (r["action"], r["p"]) for r in plan["decisions"]}
        self.assertEqual(w["t1"], ("keep", 0.9))
        self.assertEqual(w["t2"], ("cut", 0.5))
        self.assertEqual(w["t3"], ("move", 0.1))
        self.assertEqual(len(t.calls), 1, "three results, one request: each inside its own question")
        body = json.loads(t.calls[0][2])
        self.assertIn("fix the retry bug", json.dumps(body["state"]))
        self.assertEqual(len(body["questions"]), 3)
        changes = {c["id"]: c["text"] for c in plan["changes"]}
        self.assertTrue(changes["t3"].startswith(compact.STUB))
        self.assertIn("lines cut here", changes["t2"])
        self.assertNotIn("t1", changes)

    def test_secrets_never_reach_the_api(self):
        key = "sk-" + "a" * 30
        plan, t = self.build([say(f"use token={key}"), call(1, "Bash", {"command": f"curl -H 'authorization: {key}'"}), res(1, f"{key} " + BIG)])
        self.assertNotIn(key, t.calls[0][2].decode())

    def test_what_jev_cannot_judge_stays_whole(self):
        plan, _ = self.build([say("go"), call(1, "Bash", {"command": "make"}), res(1, BIG)], transport=FakeTransport([500, 500, 500, 500, 500, 500]))
        self.assertEqual(plan["decisions"][0]["action"], "keep")
        self.assertEqual(plan["decisions"][0]["why"], "unjudged")
        self.assertIn("error", plan["stats"])

    def test_apply_saves_every_large_result_and_writes_the_block(self):
        events = [say("fix the retry bug"), call(1, "Read", {"file_path": "/r/a.py"}), res(1, "yes " + BIG), call(2, "Bash", {"command": "du -sh *"}), res(2, BIG),
                  call(3, "Bash", {"command": "make"}), res(3, "yes it built " + BIG)]
        plan, _ = self.build(events, apply=True, tag="session:abcd1234", recent=1)
        folder = Path(plan["dir"])
        self.assertTrue(folder.name.startswith("session-abcd1234-"))
        self.assertIn("/compacted/session-", str(folder) + "/")
        self.assertEqual(len(list(folder.glob("*.txt"))), 3)
        self.assertTrue(Path(plan["index"]).exists())
        moved = next(c for c in plan["changes"] if c["id"] == "t2")
        self.assertIn(str(folder), moved["text"], "the line left in place says where the text is")
        self.assertIn("## Bash make", plan["restore"], "the latest result Jev judged still needed comes back")
        self.assertIn("## Read /r/a.py", plan["restore"], "and so does an earlier one")
        self.assertNotIn("## Bash du", plan["restore"])
        self.assertEqual(plan["stats"]["repeated"], 2)
        self.assertLessEqual(len(plan["restore"]), compact.RESTORE_TOKENS * settings.CHARS_PER_TOKEN)

    def test_the_latest_results_are_judged_for_the_block_but_never_touched(self):
        events = [say("fix the retry bug"), call(1, "Read", {"file_path": "/r/a.py"}), res(1, "yes " + BIG),
                  call(2, "Read", {"file_path": "/r/a.py"}), res(2, "yes " + BIG), call(3, "Bash", {"command": "git log"}), res(3, BIG)]
        plan, t = self.build(events, apply=True, tag="session:abcd1234", recent=3)
        w = {r["id"]: (r["action"], r["why"], r["p"]) for r in plan["decisions"]}
        self.assertEqual(w["t1"], ("keep", "recent", None), "read again: settled by the rule, so not asked about")
        self.assertEqual(w["t3"], ("keep", "recent", 0.1), "judged not needed, still left whole for the summary")
        self.assertEqual(plan["changes"], [])
        self.assertEqual(len(json.loads(t.calls[0][2])["questions"]), 2)
        self.assertEqual(plan["restore"].count("## Read /r/a.py"), 1)
        self.assertNotIn("## Bash git log", plan["restore"], "a finished task's output is not repeated")

    def test_old_stores_are_pruned(self):
        old = settings.HOME / "compacted" / "session-old-1"
        old.mkdir(parents=True)
        os.utime(old, (time.time() - 30 * 86_400,) * 2)
        compact.prune_store()
        self.assertFalse(old.exists())


class HandOver(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()

    def test_the_block_is_handed_over_once(self):
        compact.save_state("session:abcd1234", {"restore": "the block", "stats": {}}, None, by="hook")
        self.assertIsNotNone(compact.recent_state("session:abcd1234"))
        self.assertEqual(compact.take_restore("session:abcd1234"), "the block")
        self.assertIsNone(compact.take_restore("session:abcd1234"))

    def test_report_counts_reads_of_saved_results(self):
        ledger.log_hook("compact", {"judged": 10, "kept": 4, "cut": 2, "moved": 4, "freed": 900})
        ledger.log_hook("compact-reread", {"cmd": "cat x"})
        rep = compact.report()
        self.assertEqual((rep["compactions"], rep["moved"], rep["rereads"]), (1, 4, 1))
        self.assertAlmostEqual(rep["reread_rate"], 1 / 6)


class Hooks(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        self.transcript = self.home / "t.jsonl"
        write_claude(self.transcript, before_boundary=False)

    def payload(self, **kw):
        return json.dumps({"session_id": "abcd1234-0000", "transcript_path": str(self.transcript), "cwd": str(self.home), **kw})

    def test_off_by_default(self):
        code, out, _, t = run(["hook", "pre-compact"], stdin=self.payload(hook_event_name="PreCompact", trigger="auto"))
        self.assertEqual((code, out, t.calls), (0, "", []))
        self.assertFalse((settings.HOME / "compacted").exists())

    def test_before_and_after_a_compaction(self):
        with mock.patch.dict("os.environ", {"JEV_COMPACT_MODE": "on", "JEV_COMPACT_RECENT": "0"}):
            code, out, err, t = run(["hook", "pre-compact"], stdin=self.payload(hook_event_name="PreCompact", trigger="auto"))
            self.assertEqual((code, out), (0, ""), err)
            row = [r for r in ledger.hook_rows() if r.get("hook") == "compact"][-1]
            self.assertEqual((row["judged"], row["host"], row["agent"]), (1, "claude", "session:abcd1234"))
            code, out, err, _ = run(["hook", "session-start"], stdin=self.payload(hook_event_name="SessionStart", source="compact"))
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertIn(compact.STUB, ctx)
            self.assertIn("/r/src/queue.py", ctx)
            code, out, _, _ = run(["hook", "session-start"], stdin=self.payload(hook_event_name="SessionStart", source="compact"))
            self.assertEqual(out, "", "the block is handed over once")

    def test_the_mod_judged_already(self):
        compact.save_state("session:abcd1234", {"restore": "x", "stats": {}}, None, by="mod")
        with mock.patch.dict("os.environ", {"JEV_COMPACT_MODE": "on"}):
            code, out, _, t = run(["hook", "pre-compact"], stdin=self.payload(hook_event_name="PreCompact", trigger="auto"))
        self.assertEqual(t.calls, [], "no second judgment of the same compaction")

    def test_a_saved_result_read_again_is_counted(self):
        cmd = f"cat {settings.HOME}/compacted/session-abcd1234-1/001-Read.txt"
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "abcd1234-0000",
                   "tool_response": {"stdout": "text", "stderr": "", "exit_code": 0}}
        run(["hook", "after-bash"], stdin=json.dumps(payload))
        self.assertEqual([r for r in ledger.hook_rows() if r.get("hook") == "compact-reread"][-1]["agent"], "session:abcd1234")

    def test_the_command(self):
        code, out, err, t = run(["compact", "--transcript", str(self.transcript), "--recent", "0", "--json"])
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["stats"]["judged"], 1)
        self.assertIsNone(plan["dir"], "nothing written without --apply")
        code, out, err, t = run(["compact", "--transcript", str(self.transcript), "--rules-only"])
        self.assertEqual((code, t.calls), (0, []))
        self.assertIn("jev compact", out)

    def test_the_mod_hands_messages_and_gets_changes(self):
        msgs = {"messages": [{"role": "user", "text": "go", "toolUses": []},
                             {"role": "assistant", "text": "", "toolUses": [{"tool_use_id": "u1", "tool": "Bash", "input": {"command": "du -sh *"}, "text": BIG}]},
                             {"role": "user", "text": "", "toolUses": [{"tool_use_id": "u2", "tool": "Bash", "input": {"command": "ls"}, "text": "a"}]}]}
        with mock.patch.dict("os.environ", {"JEV_AGENT": "session:abcd1234"}):
            code, out, err, t = run(["compact", "--messages", "-", "--apply", "--pruned", "--price-model", "claude-opus-5-5", "--recent", "0", "--json", "--compact"],
                                    stdin=json.dumps(msgs))
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual([c["id"] for c in plan["changes"]], ["u1"])
        row = [r for r in ledger.hook_rows() if r.get("hook") == "compact"][-1]
        self.assertTrue(row["pruned"])
        self.assertGreater(row["saved_usd"], 0)


class Codex(unittest.TestCase):
    def setUp(self):
        self.home = fresh_home()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ("CLAUDE_PROJECT_DIR", "PLUGIN_ROOT", "JEV_HOST"):
            os.environ.pop(name, None)

    def guard(self, cmd, mode="default"):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "permission_mode": mode, "turn_id": "t1",
                   "session_id": "abcd1234-0000", "cwd": str(self.home), "model": "gpt-5"}
        return run(["hook", "guard"], stdin=json.dumps(payload))

    def test_the_guard_warns_instead_of_asking(self):
        code, out, _, _ = self.guard("rm -rf yes-build")
        answer = json.loads(out)
        self.assertNotIn("hookSpecificOutput", answer, "Codex has no ask for a hook")
        self.assertIn("jev guard", answer["systemMessage"])
        self.assertEqual(ledger.hook_rows()[-1]["decision"], "warn")

    def test_claude_code_is_never_taken_for_codex(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.home)
        code, out, _, _ = self.guard("rm -rf yes-build")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_the_guard_denies_where_no_prompt_can_appear(self):
        code, out, _, _ = self.guard("rm -rf yes-build", mode="bypassPermissions")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_trimmed_output_replaces_the_result_as_feedback(self):
        from test_hooks import LONG_OUT
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "npm install"}, "turn_id": "t1",
                   "session_id": "abcd1234-0000", "cwd": str(self.home), "tool_response": LONG_OUT}
        code, out, err, _ = run(["hook", "after-bash"], stdin=json.dumps(payload))
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertEqual(answer["decision"], "block")
        self.assertTrue(answer["reason"].startswith("[jev trim] kept"))

    def test_the_codex_plugin_points_at_its_own_hooks(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
        hooks = json.loads((root / manifest["hooks"].removeprefix("./")).read_text())["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact", "Stop"})
        for groups in hooks.values():
            for h in groups[0]["hooks"]:
                self.assertIn("${PLUGIN_ROOT}/bin/jev", h["command"])
        claude = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], claude["version"])
        for name in ("sift", "tests", "review", "pr", "triage", "stats", "setup"):
            self.assertIn("allow_implicit_invocation: false", (root / "skills" / name / "agents" / "openai.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
