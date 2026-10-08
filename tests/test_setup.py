import contextlib
import io
import json
import os
import unittest
from unittest import mock

from _fake import FakeTransport, fresh_home, FAKE_OR_KEY
from jev import settings
from jev.cli import main
from jev.cli.setup import ollama_url
from jev.errors import UsageError


class FakeOllama(FakeTransport):
    """An Ollama: /api/* answered like 0.35 does, everything else (the decisions) by FakeTransport.
    version=None makes the server unreachable."""

    def __init__(self, version="0.35.1", models=("tev1-32k:latest",)):
        super().__init__()
        self.version, self.models = version, list(models)

    def request(self, method, path, body=None, headers=None):
        if not path.startswith("/api/"):
            return super().request(method, path, body, headers)
        self.calls.append((method, path, body))
        if self.version is None:
            raise ConnectionRefusedError("[Errno 61] Connection refused")
        req = json.loads(body) if body else {}
        if path == "/api/version":
            return 200, {}, json.dumps({"version": self.version}).encode()
        if path == "/api/tags":
            return 200, {}, json.dumps({"models": [{"name": n} for n in self.models]}).encode()
        if path in ("/api/create", "/api/pull"):
            self.models.append(req["model"] + ":latest")
            return 200, {}, b'{"status":"success"}'
        return 404, {}, b"404 page not found"

    def api(self, path):
        return [json.loads(b) if b else None for _, p, b in self.calls if p == path]


class TTY(io.StringIO):
    def isatty(self):
        return True


def run(argv, transport, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("jev.transport.Transport.request", lambda self, *a, **k: transport.request(*a, **k)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch("sys.stdin", stdin or io.StringIO("")), mock.patch.dict("os.environ", {}):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


class Setup(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_remote_ollama_gets_ollamas_limits(self):
        t = FakeOllama()
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1"], t)
        self.assertEqual(code, 0, out)
        cfg = settings.config()
        self.assertEqual((cfg["base_url"], cfg["model"], cfg["server"]), ("http://100.74.0.1:11434", "tev1-32k", "ollama"))
        self.assertEqual(settings.backend_name(), "ollama")
        self.assertEqual((settings.max_questions(), settings.parallel(), settings.max_body()), (1, 4, 60_000))
        self.assertIn("ready: ollama · http://100.74.0.1:11434", out)
        self.assertEqual(t.api("/api/create"), [], "nothing made when tev1-32k is there")

    def test_makes_the_32k_variant_with_yes(self):
        t = FakeOllama(models=["tev1:latest", "qwen3.5:9b"])
        code, out = run(["setup", "--judge", "remote", "--url", "http://box.tail.ts.net:11434", "--yes"], t)
        self.assertEqual(code, 0, out)
        self.assertEqual(t.api("/api/create"), [{"model": "tev1-32k", "from": "tev1", "parameters": {"num_ctx": 32768}, "stream": False}])
        self.assertEqual(settings.config()["model"], "tev1-32k")

    def test_without_a_terminal_or_yes_nothing_is_made_or_changed(self):
        t = FakeOllama(models=["tev1:latest"])
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1"], t)
        self.assertEqual(code, 4)
        self.assertIn("jev setup --yes", out)
        self.assertEqual(t.api("/api/create"), [])
        self.assertEqual(settings.config(), {})

    def test_download_needs_pull(self):
        t = FakeOllama(models=[])
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1", "--yes"], t)
        self.assertEqual(code, 4)
        self.assertIn("--pull", out)
        self.assertEqual(t.api("/api/pull"), [])
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1", "--yes", "--pull"], t)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["model"] for r in t.api("/api/pull")], ["tev1"])
        self.assertEqual(len(t.api("/api/create")), 1)

    def test_old_or_unreachable_ollama_changes_nothing(self):
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1"], FakeOllama(version="0.32.15"))
        self.assertEqual(code, 4)
        self.assertIn("0.35 or newer", out)
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1"], FakeOllama(version=None))
        self.assertEqual(code, 4)
        self.assertIn("OLLAMA_HOST=0.0.0.0", out)
        code, out = run(["setup", "--judge", "local"], FakeOllama(version=None))
        self.assertIn("ollama serve", out)
        self.assertEqual(settings.config(), {})

    def test_a_named_model_must_be_there(self):
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1", "--model", "nimble"], FakeOllama())
        self.assertEqual(code, 4)
        self.assertIn("it has tev1-32k", out)
        code, out = run(["setup", "--judge", "remote", "--url", "100.74.0.1", "--model", "tev1-32k:latest"], FakeOllama())
        self.assertEqual(code, 0, out)
        self.assertEqual(settings.config()["model"], "tev1-32k")

    def test_local_ollama_needs_no_mark(self):
        code, out = run(["setup", "--judge", "local"], FakeOllama())
        self.assertEqual(code, 0, out)
        self.assertEqual(settings.config()["base_url"], "http://localhost:11434")
        self.assertNotIn("server", settings.config())
        self.assertEqual(settings.backend_name(), "ollama")

    def test_the_mark_goes_with_its_url(self):
        run(["setup", "--judge", "remote", "--url", "100.74.0.1"], FakeOllama())
        with mock.patch.dict("os.environ", {"TYPESAFE_BASE_URL": "http://10.0.0.9:8000"}):
            self.assertEqual(settings.backend_name(), "local")
        run(["config", "set", "backend", "http://10.0.0.9:8000"], FakeOllama())
        self.assertNotIn("server", settings.config())
        self.assertEqual(settings.max_questions(), 0)
        run(["setup", "--judge", "remote", "--url", "100.74.0.1"], FakeOllama())
        run(["config", "set", "backend", "typesafe"], FakeOllama())
        self.assertEqual((settings.backend_name(), settings.config().get("server")), ("typesafe", None))
        code, out = run(["config", "set", "server", "vllm"], FakeOllama())
        self.assertEqual(code, 2)

    def test_hosted_judges(self):
        code, out = run(["setup", "--judge", "typesafe"], FakeOllama())  # the suite's TYPESAFE_API_KEY
        self.assertEqual(code, 0, out)
        self.assertEqual(settings.backend_name(), "typesafe")
        code, out = run(["setup", "--judge", "openrouter"], FakeOllama())
        self.assertEqual(code, 4)
        self.assertIn("sk-or-", out)
        with mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": FAKE_OR_KEY}):
            code, out = run(["setup", "--judge", "openrouter"], FakeOllama())
        self.assertEqual(code, 0, out)
        self.assertEqual(settings.backend_name(), "openrouter")

    def test_asks_at_a_terminal(self):
        t = FakeOllama(models=["tev1:latest"])
        code, out = run(["setup"], t, stdin=TTY("remote\n100.74.0.1\n\n"))
        self.assertEqual(code, 0, out)
        self.assertIn("Which judge", out)
        self.assertEqual(len(t.api("/api/create")), 1, "Enter takes the default, yes")
        self.assertEqual(settings.config()["base_url"], "http://100.74.0.1:11434")

    def test_a_fresh_machine_defaults_to_local_and_keep_without_a_key_fails_cleanly(self):
        with mock.patch.dict("os.environ"):
            os.environ.pop("TYPESAFE_API_KEY", None)
            code, out = run(["setup"], FakeOllama(), stdin=TTY("\n"))
            self.assertEqual(code, 0, out)
            self.assertEqual(settings.config()["base_url"], "http://localhost:11434")
            fresh_home()
            code, out = run(["setup", "--judge", "keep"], FakeOllama())
        self.assertEqual(code, 4)
        self.assertIn("FAIL  POST /v1/systemone: no API key", out)

    def test_no_terminal_and_no_judge_is_a_usage_error(self):
        code, out = run(["setup"], FakeOllama())
        self.assertEqual(code, 2)
        self.assertIn("--judge", out)

    def test_addresses(self):
        self.assertEqual(ollama_url("100.74.0.1"), "http://100.74.0.1:11434")
        self.assertEqual(ollama_url("mac-trabajo:8080/"), "http://mac-trabajo:8080")
        self.assertEqual(ollama_url("http://box"), "http://box")
        self.assertEqual(ollama_url("https://ollama.example.ts.net/v1"), "https://ollama.example.ts.net")
        self.assertEqual(ollama_url("[fd7a::1]"), "http://[fd7a::1]:11434")
        for bad in ("", "http://:99", "ftp://box", "box:port"):
            with self.assertRaises(UsageError, msg=bad):
                ollama_url(bad)


if __name__ == "__main__":
    unittest.main()
