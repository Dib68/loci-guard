# loci-guard: ignore-file  (fixtures contain fake secrets on purpose)
"""Tests for the MCP server: protocol handshake, tools, shared state, and a real stdio round trip."""

import io
import json
import os
import subprocess
import sys

from click.testing import CliRunner

from loci_guard.brain.palace import MemoryPalace
from loci_guard.cli import main
from loci_guard.mcp_server import SUPPORTED_PROTOCOLS, TOOLS, Server

FAKE_AWS_KEY_LINE = 'K = "AKIAIOSFODNN7EXAMPLE"'  # loci-guard: ignore[SEC003]


def _call(server, tool, /, **arguments):
    response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                              "params": {"name": tool, "arguments": arguments}})
    result = response["result"]
    return result["content"][0]["text"], result["isError"]


def test_handshake_negotiates_protocol_and_lists_tools(tmp_path):
    server = Server(tmp_path)
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"]["name"] == "loci-guard" and "tools" in init["result"]["capabilities"]
    unknown = server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert unknown["result"]["protocolVersion"] == SUPPORTED_PROTOCOLS[0]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {t["name"] for t in TOOLS} and all("inputSchema" in t for t in tools)


def test_errors_follow_json_rpc(tmp_path):
    server = Server(tmp_path)
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}})["error"]["code"] == -32602
    text, failed = _call(server, "remember", text="x", at=["../outside.py"])
    assert failed and "inside the project" in text
    text, failed = _call(server, "remember")
    assert failed


def test_what_one_agent_remembers_another_recalls(tmp_path):
    hermes, claude = Server(tmp_path), Server(tmp_path)
    text, failed = _call(hermes, "remember", text="Refunds go through the billing queue", at=["billing/"])
    assert not failed and text.startswith("Remembered #")
    recalled, _ = _call(claude, "recall", path="billing/refund.py", session="claude-1")
    assert "Refunds go through the billing queue" in recalled
    again, _ = _call(claude, "recall", path="billing/refund.py", session="claude-1")
    assert again.startswith("Nothing new")
    found, _ = _call(claude, "search", query="refund")
    assert "matched: refund" in found


def test_constrain_intend_history_and_context(tmp_path):
    server = Server(tmp_path)
    _call(server, "constrain", name="no_eval", rule="Never use eval()", severity="critical")
    _call(server, "constrain", name="money", rule="Use Decimal", at=["billing/"])
    out, _ = _call(server, "intend", text="Update CHANGELOG.md", when=["billing/"])
    intent_id = out.split("#")[1].split()[0]
    context, _ = _call(server, "project_context")
    assert "Never use eval()" in context and "intention(s) fire in: billing/" in context
    assert "Update CHANGELOG.md" in _call(server, "recall", path="billing/x.py")[0]
    assert not _call(server, "intend_done", id=intent_id)[1]
    assert _call(server, "intend_done", id="nope")[1]
    assert "v1 (current): [WARNING] money: Use Decimal" in _call(server, "history", id="c-money")[0]


def test_check_code_blocks_secrets(tmp_path):
    server = Server(tmp_path)
    blocked, failed = _call(server, "check_code", code=FAKE_AWS_KEY_LINE, filename="k.py")
    assert not failed and blocked.startswith("BLOCKED") and "SEC003" in blocked
    assert _call(server, "check_code", code="x = 1\n")[0] == "PASSED: no issues."


def test_serve_loop_handles_bad_lines(tmp_path):
    stdin = io.StringIO('not json\n\n{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n[1, 2]\n')
    stdout = io.StringIO()
    Server(tmp_path).serve(stdin, stdout)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r.get("error", {}).get("code") for r in replies] == [-32700, None, -32600]


def test_real_stdio_round_trip(tmp_path):
    launcher = "import sys; from loci_guard.entry import main; sys.argv = ['loci-guard', '--root', sys.argv[1], 'mcp']; main()"
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "remember", "arguments": {"text": "Invoices are immutable after issue", "at": ["billing/"]}}},
    ]
    proc = subprocess.run(
        [sys.executable, "-c", launcher, str(tmp_path)],
        input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True, timeout=60, check=False,
    )
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2], proc.stderr
    assert any(m["text"] == "Invoices are immutable after issue" for m in MemoryPalace(tmp_path).memories.values())


def test_stdio_is_utf8_whatever_the_platform_default(tmp_path):
    """MCP is UTF-8 by spec; a BOM from a Windows shell must not break the first message."""
    launcher = "import sys; from loci_guard.entry import main; sys.argv = ['loci-guard', '--root', sys.argv[1], 'mcp']; main()"
    text = "Le fatture sono immutabili perché la città lo esige €"
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "remember", "arguments": {"text": text, "at": ["billing/"]}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "recall", "arguments": {"path": "billing/x.py"}}},
    ]
    raw = b"\xef\xbb\xbf" + "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages).encode("utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    proc = subprocess.run([sys.executable, "-c", launcher, str(tmp_path)], input=raw, capture_output=True,
                          timeout=60, check=False, env=env)
    replies = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3], proc.stderr.decode("utf-8", "replace")
    assert any(m["text"] == text for m in MemoryPalace(tmp_path).memories.values())
    assert text in replies[2]["result"]["content"][0]["text"]


def test_print_config(tmp_path):
    out = CliRunner().invoke(main, ["--root", str(tmp_path), "mcp", "--print-config"]).output
    entry = json.loads(out)["mcpServers"]["loci-guard"]
    assert entry["args"][-1] == "mcp" and str(tmp_path) in entry["args"]
