import io
import json
import unittest
from unittest import mock

from _fake import FakeTransport, fresh_home
from jev import mcp, settings


def rpc(messages, transport=None):
    t = transport or FakeTransport()
    inp = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    out = io.StringIO()
    with mock.patch("jev.transport.Transport.request", lambda self, *a, **k: t.request(*a, **k)):
        mcp.serve(inp, out)
    return [json.loads(l) for l in out.getvalue().splitlines() if l.strip()], t


class Server(unittest.TestCase):
    def setUp(self):
        fresh_home()

    def test_initialize_list_and_call(self):
        replies, t = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "decide", "arguments": {"state": {"t": "yes please"}, "question": "Is `t` a yes?", "band": [0.4, 0.6]}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rank", "arguments": {"query": "q", "candidates": ["yes a", "maybe b", "no c"], "abstain": [0.4, 0.6]}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "cluster", "arguments": {"items": ["yes a", "yes b", "no c"]}}},
            {"jsonrpc": "2.0", "id": 6, "method": "ping"},
        ])
        by_id = {r["id"]: r for r in replies}
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "jev")
        names = [tool["name"] for tool in by_id[2]["result"]["tools"]]
        self.assertEqual(names, ["decide", "ask", "rank", "sift", "tests", "diff", "cluster", "session"])
        self.assertTrue(all("inputSchema" in tool for tool in by_id[2]["result"]["tools"]))
        d = by_id[3]["result"]
        self.assertFalse(d["isError"])
        self.assertEqual(d["structuredContent"]["answer"], "yes")
        self.assertEqual(json.loads(d["content"][0]["text"])["answer"], "yes")
        r = by_id[4]["result"]["structuredContent"]
        self.assertEqual([x["candidate"] for x in r["results"]], ["yes a", "no c"])
        self.assertEqual([x["candidate"] for x in r["uncertain"]], ["maybe b"])
        self.assertEqual(by_id[5]["result"]["structuredContent"]["clusters"][0]["size"], 2)
        self.assertEqual(by_id[6]["result"], {})
        self.assertNotIn(None, by_id)  # the notification got no reply

    def test_errors_are_replies_not_crashes(self):
        replies, _ = rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "decide", "arguments": {"state": "s"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
        ])
        by_id = {r["id"]: r for r in replies}
        self.assertTrue(by_id[1]["result"]["isError"])
        self.assertIn("unknown tool", by_id[1]["result"]["content"][0]["text"])
        self.assertTrue(by_id[2]["result"]["isError"])
        self.assertEqual(by_id[3]["error"]["code"], -32601)

    def test_parse_error(self):
        out = io.StringIO()
        mcp.serve(io.StringIO("{not json\n"), out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32700)

    def test_session_tool(self):
        replies, _ = rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "session", "arguments": {"cwd": str(settings.HOME)}}}])
        r = replies[0]["result"]["structuredContent"]
        self.assertIn("nothing decided", r["summary"])
        self.assertIsNone(r["model"])


if __name__ == "__main__":
    unittest.main()
