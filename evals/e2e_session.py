"""A new Claude Code session, end to end, through the installed plugin's hooks and MCP server as Claude Code calls them,
on the judge you configured (`jev setup`). Nothing touches your own ledger or settings: jev's home is a copy of your
config in a temporary folder. Every check prints PASS or FAIL with what it saw.

    python3 evals/e2e_session.py [--repo] [--keep]      (--repo: this checkout instead of the installed plugin)

It needs git and a working judge; the guard and MCP checks ask it a handful of questions."""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

work = Path(tempfile.mkdtemp(prefix="jev-e2e-"))
REPO = work / "repo"
# The installed plugin, or with --repo this checkout.
ROOT = Path(__file__).resolve().parent.parent if "--repo" in sys.argv else \
    sorted((Path.home() / ".claude/plugins/cache/gastongelhorn/jevmate").glob("1.*"), key=lambda p: tuple(int(x) for x in p.name.split(".")))[-1]
JEV = str(ROOT / "bin" / "jev")
SID = f"e2e{uuid.uuid4().hex[:5]}-0000-4000-8000-{uuid.uuid4().hex[:12]}"
TAG = f"session:{SID[:8]}"
REAL_HOME = next((p for p in (os.environ.get("JEV_HOME"), Path.home() / ".config/jev", Path.home() / ".config/typesafe") if p and Path(p).exists()), None)
HOME = work / "jev-home"
HOME.mkdir()
for name in ("config.json", "api_key"):  # the judge you set up, nothing else
    if REAL_HOME and (Path(REAL_HOME) / name).exists():
        (HOME / name).write_bytes((Path(REAL_HOME) / name).read_bytes())
HOOKS_LOG = HOME / "hooks.log"
USAGE = HOME / "usage.jsonl"
ENV = dict(os.environ, JEV_HOME=str(HOME), CLAUDE_ENV_FILE=str(work / "env.sh"), JEV_CLAUDE_SETTINGS=str(work / "claude-settings.json"), JEV_HOST="claude",
           SHELFLIFE_CONTEXT_OFF="1", CLAUDE_PROJECT_DIR=str(REPO), JEV_COMPACT_MODE="on")
for name in ("JEV_GUARD_MOD", "JEV_AGENT", "JEV_SESSION"):
    ENV.pop(name, None)
results = []


def check(name, ok, seen=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  · {seen}" if seen else ""))


def hook(which, payload, env=None):
    started = time.perf_counter()
    # as hooks.json has Claude Code run it: through a shell
    p = subprocess.run(["sh", "-c", f'python3 "{JEV}" hook {which} 2>/dev/null'], input=json.dumps(payload), capture_output=True, text=True,
                       env=env or ENV, timeout=180)
    return p.stdout.strip(), round(time.perf_counter() - started, 2)


def rows(path, since):
    out = []
    for line in open(path):
        if TAG in line:
            r = json.loads(line)
            if r["ts"] >= since:
                out.append(r)
    return out


def transcript():
    t = work / f"{SID}.jsonl"
    now = datetime.now(timezone.utc)
    lines = [json.dumps({"type": "user", "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "message": {"role": "user", "content": "bump the version and publish it"}})]
    for i in range(3):
        lines.append(json.dumps({"type": "assistant", "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "uuid": f"u{i}",
                                 "message": {"id": f"m{i}", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "ok"}],
                                             "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 120_000 + i * 5000,
                                                       "cache_creation_input_tokens": 3000}}}))
    big = "\n".join(f"line {n}: build output that goes on for a while" for n in range(400))
    lines.append(json.dumps({"type": "assistant", "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "uuid": "u9",
                             "message": {"id": "m9", "model": "claude-opus-5-5", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make build", "description": "Build the assets"}}],
                                         "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 130_000, "cache_creation_input_tokens": 0}}}))
    lines.append(json.dumps({"type": "user", "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}]}}))
    t.write_text("\n".join(json.dumps(json.loads(x), separators=(",", ":")) for x in lines) + "\n")  # as Claude Code writes it
    return t


subprocess.run(["git", "init", "-q", "-b", "main", str(REPO)], check=True)
(REPO / "README.md").write_text("# demo\nversion = 1\n")
subprocess.run(["git", "-C", str(REPO), "add", "README.md"], check=True)
subprocess.run(["git", "-C", str(REPO), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init"], check=True)
subprocess.run(["git", "clone", "-q", "--bare", str(REPO), str(work / "remote.git")], check=True)
subprocess.run(["git", "-C", str(REPO), "remote", "add", "origin", str(work / "remote.git")], check=True)
since = datetime.now().isoformat(timespec="seconds")
tpath = transcript()
base = {"session_id": SID, "cwd": str(REPO), "transcript_path": str(tpath)}

# 1. Session start: tags the session, ties the MCP server's process to it, sets the auto-compact window.
out, secs = hook("session-start", {**base, "hook_event_name": "SessionStart", "source": "startup"})
envfile = (work / "env.sh").read_text() if (work / "env.sh").exists() else ""
check("SessionStart tags the agent's commands", f'JEV_SESSION="{TAG}"' in envfile, envfile.strip())
settings = json.loads((work / "claude-settings.json").read_text()) if (work / "claude-settings.json").exists() else {}
check("SessionStart sets Claude Code's auto-compact window", settings.get("autoCompactWindow") == 400_000, f"{settings} in {secs} s")
check("SessionStart binds this process for the MCP server", (HOME / f"sessions/by-pid/{os.getpid()}.json").exists())

# 2. The guard, in bypass mode, on what the agent would run.
cases = [("git status --short && git log --oneline -1", "read-only", None),
         ("mkdir -p /tmp/jev-e2e && printf 'hello\\n' > /tmp/jev-e2e/note.txt", "scratch", None),
         ("sed -i '' 's/version = 1/version = 2/' README.md", "project-edit", None),
         ("npm run build -- --prod", None, "-"),
         ("git -c user.name=t commit -qam bump && git push -q --force origin main", None, "deny")]
for cmd, why, decision in cases:
    out, secs = hook("guard", {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "permission_mode": "bypassPermissions"})
    row = rows(HOOKS_LOG, since)[-1]
    if why:
        check(f"guard settles by rule ({why}): {cmd[:50]}", row.get("why") == why and "p" not in row and not out, f"{secs} s, row why={row.get('why')}")
    else:
        got = json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "-"
        check(f"guard asks the model, decision {decision}: {cmd[:50]}", "p" in row and got == decision,
              f"p={row.get('p')} outside={row.get('outside')} decision={got} {secs} s (row ms {row.get('ms')})")

# 3. No verdict in time on a dangerous command: refused in bypass mode; with the mod, held and asked.
down = dict(ENV, TYPESAFE_BASE_URL="http://127.0.0.1:9", JEV_BASE_URL="http://127.0.0.1:9", JEV_GUARD_TIMEOUT="2")
out, secs = hook("guard", {**base, "tool_name": "Bash", "tool_input": {"command": "rm -rf ./dist"}, "permission_mode": "bypassPermissions"}, env=down)
row = rows(HOOKS_LOG, since)[-1]
check("no verdict on rm -rf: refused in bypass mode", '"deny"' in out and row.get("why") == "unjudged", f"{secs} s, {out[:120]}")
mod = dict(down, JEV_GUARD_MOD="1")
hook("guard", {**base, "tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "permission_mode": "bypassPermissions"}, env=mod)
out, _ = hook("guard", {**base, "tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "permission_mode": "bypassPermissions", "judge": True}, env=mod)
verdict = json.loads(out.splitlines()[-1]) if out else {}
check("no verdict on git push, with the mod: the person is asked", verdict.get("decision") == "ask" and verdict.get("why") == "unjudged", str(verdict)[:120])
out, _ = hook("guard", {**base, "tool_name": "Bash", "tool_input": {"command": "make lint"}, "permission_mode": "bypassPermissions"}, env=down)
check("no verdict on a harmless command: it runs, counted unchecked", "hookSpecificOutput" not in out and "err" in rows(HOOKS_LOG, since)[-1])

# 4. The MCP server, started by "Claude Code" (this process), tags its calls with the session.
srv = subprocess.Popen(["python3", JEV, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=ENV)
msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "decide", "arguments": {"state": "the sky is green", "question": "Is `state` true?"}}}]
outs = srv.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=120)[0]
answer = [json.loads(l) for l in outs.splitlines() if l.strip()][-1]
p = (answer.get("result") or {}).get("structuredContent", {}).get("p")
tagged = [r for r in rows(USAGE, since) if r.get("cmd") == "mcp:decide"]
check("MCP decide answers with tev1", isinstance(p, float), f"p={p}")
check("MCP call is tagged with the session", bool(tagged), f"{len(tagged)} row(s) tagged {TAG}")

# 5. After the command ran: the guard remembers it; a person's answer is recorded for the mod.
hook("after-bash", {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "npm run build -- --prod"},
                    "tool_response": {"stdout": "built", "stderr": "", "exit_code": 0, "interrupted": False}})
hook("record", {"hook": "guard-answer", "session_id": SID, "answer": "refuse", "p": 0.97, "cmd": "git push -q --force origin main"})
check("the person's answer is recorded", any(r.get("hook") == "guard-answer" for r in rows(HOOKS_LOG, since)))

# 6. Before a compaction, on the local model: saved and listed, no model call, in well under a second.
n_before = len(rows(USAGE, since))
out, secs = hook("pre-compact", {**base, "hook_event_name": "PreCompact", "trigger": "auto"})
row = [r for r in rows(HOOKS_LOG, since) if r.get("hook") == "compact"]
check("PreCompact on the local model: no question, quick", row and "err" not in row[-1] and len(rows(USAGE, since)) == n_before and secs < 3,
      f"{secs} s, row={ {k: row[-1].get(k) for k in ('judged', 'kept', 'requests', 'dir')} if row else None}")
out, _ = hook("session-start", {**base, "hook_event_name": "SessionStart", "source": "compact"})
check("after the compaction the block lists the saved results", "saved in" in out and "Build the assets" in out, out[:160])

# 7. What the line above the prompt and the report show for this session.
s = json.loads(subprocess.run([JEV, "session", "--json", "--compact", "--session", SID, "--cwd", str(REPO), "--transcript", str(tpath)],
                              capture_output=True, text=True, env=ENV).stdout)
sf = s["jev"]["safety"]
ok_rows = [r for r in rows(USAGE, since) if "err" not in r]
check("session: only this session's answered rows", s["jev"]["requests"] == len(ok_rows), f"requests={s['jev']['requests']} of {len(ok_rows)}")
check("session: questions, stopped and unchecked", sf["asked"] >= 1 and sf["stopped"] == 1 and sf["unchecked"] >= 3, str(sf))
check("session: the judge is local; only the MCP decision counts as reading kept out", s["jev"]["local_judge"] is True and s["jev"]["saved_total"] < 0.01
      and list(s["jev"]["labels"]) == ["mcp:decide"],
      f"saved_total={s['jev']['saved_total']:.4f} labels={s['jev']['labels']}")
r = json.loads(subprocess.run([JEV, "report", "--days", "0.05", "--json"], capture_output=True, text=True, env=ENV).stdout)
g = r["jev"]["guard"]
check("report: the guard's commands, answers, unchecked and waits", g["commands"] == 8 and g["answers"]["refuse"] == 1 and g["unchecked"] == 3
      and g["wait_ms"]["p50"] is not None, json.dumps(g))
print(f"\n{sum(results)}/{len(results)} passed · jevmate {'checkout' if '--repo' in sys.argv else ROOT.name} · session {SID}" + (f" · {work}" if "--keep" in sys.argv else ""))
if "--keep" not in sys.argv:
    import shutil
    shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if all(results) else 1)
