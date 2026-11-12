"""MCP server over stdio, so any MCP client (Hermes Agent, Codex, Cursor, Claude...) shares one palace.

Every agent connected to the same project reads and writes the same .loci/ state:
what one agent learns, the others recall. No dependencies: JSON-RPC 2.0, one message per line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from loci_guard import __version__

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Loci Guard keeps this project's memory and checks code for security issues. "
    "Call `project_context` at the start of a task and `recall` before working in a folder. "
    "Call `check_code` before writing code that touches secrets, SQL, shell, HTML, logs or CORS. "
    "Record rules and decisions the user states with `constrain` and `remember`."
)


def _strings(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


TOOLS: list[dict[str, Any]] = [
    {"name": "project_context", "description": "Project-wide rules, decisions, open intentions and the palace map.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "recall", "description": "What the project remembers about a file or folder (rules, pitfalls, decisions, wiki page).",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "File or folder, relative to the project root"},
         "session": {"type": "string", "description": "Your session id, so repeated calls don't repeat items"}},
         "required": ["path"]}},
    {"name": "search", "description": "Associative search over memories and archived chats.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "remember", "description": "Save a memory in a room. Near-duplicates merge, similar ones link.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "at": _strings("Files or folders it is about; empty = project-wide"),
         "kind": {"type": "string", "enum": ["note", "decision", "lesson", "constraint"]}}, "required": ["text"]}},
    {"name": "constrain", "description": "Add or update a named rule the project must follow.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "rule": {"type": "string"},
         "severity": {"type": "string", "enum": ["critical", "high", "medium", "warning", "info"]},
         "at": _strings("Folders it applies to; empty = project-wide")}, "required": ["name", "rule"]}},
    {"name": "intend", "description": "Prospective memory: something to do when a place is next entered.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "when": _strings("Files or folders that trigger it; empty = next session start")},
         "required": ["text"]}},
    {"name": "intend_done", "description": "Mark an intention as done.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "history", "description": "Every wording a memory has had and what each change dropped.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "foresee", "description": "Start a foresight swarm for a planned change: returns personas, each with "
                                        "its own memories, to play as independent forecasters.",
     "inputSchema": {"type": "object", "properties": {
         "description": {"type": "string"}, "at": _strings("Files or folders the change touches")},
         "required": ["description"]}},
    {"name": "foresee_record", "description": "Record lines `PREDICT <persona> | <0-1> | <what> | <how we'll know>`.",
     "inputSchema": {"type": "object", "properties": {"session": {"type": "string"}, "predictions": {"type": "string"}},
                     "required": ["session", "predictions"]}},
    {"name": "foresee_resolve", "description": "Say whether a prediction came true; this calibrates its persona.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "happened": {"type": "boolean"}},
                     "required": ["id", "happened"]}},
    {"name": "check_code", "description": "Scan code before writing it: secrets, injection, PII, debug leftovers.",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"}, "filename": {"type": "string", "description": "Used to pick language rules"}},
         "required": ["code"]}},
]


class ToolError(Exception):
    pass


class Server:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.handlers: dict[str, Callable[[dict], str]] = {
            "project_context": self._project_context, "recall": self._recall, "search": self._search,
            "remember": self._remember, "constrain": self._constrain, "intend": self._intend,
            "intend_done": self._intend_done, "history": self._history, "check_code": self._check_code,
            "foresee": self._foresee, "foresee_record": self._foresee_record, "foresee_resolve": self._foresee_resolve,
        }

    # ---------- JSON-RPC ----------

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        msg_id, method = message.get("id"), message.get("method")
        if msg_id is None:
            return None  # notifications (e.g. notifications/initialized) get no response
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            return self._result(msg_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "loci-guard", "version": __version__},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = message.get("params") or {}
            handler = self.handlers.get(params.get("name", ""))
            if handler is None:
                return self._error(msg_id, -32602, f"Unknown tool: {params.get('name')}")
            try:
                text, failed = handler(params.get("arguments") or {}), False
            except (ToolError, ValueError, KeyError) as error:
                text, failed = f"Error: {error}", True
            return self._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": failed})
        return self._error(msg_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "Parse error")
            else:
                response = self.handle(message) if isinstance(message, dict) else self._error(None, -32600, "Invalid request")
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()

    # ---------- tools ----------

    def _palace(self):
        from loci_guard.brain.palace import MemoryPalace

        return MemoryPalace(self.root)

    def _anchors(self, palace, places: list[str]) -> list[str]:
        from loci_guard.brain.palace import ATRIUM

        if not places:
            return [ATRIUM]
        anchors = [palace.anchor_for(p) for p in places]
        if any(a is None for a in anchors):
            raise ToolError("every path must be inside the project")
        return anchors

    def _project_context(self, args: dict) -> str:
        from loci_guard.brain.workspace import session_broadcast

        return session_broadcast(self.root, None, None, dry_run=True).text or "No project context yet."

    def _recall(self, args: dict) -> str:
        from loci_guard.brain.astrocyte import Astrocyte
        from loci_guard.brain.workspace import room_broadcast

        palace = self._palace()
        result = room_broadcast(palace, Astrocyte(palace), args.get("session") or "mcp", args["path"])
        return result.text or f"Nothing new for {args['path']} in this session."

    def _search(self, args: dict) -> str:
        from loci_guard.brain import archive
        from loci_guard.brain.associative import AssociativeIndex

        palace = self._palace()
        lines = [f"- [{m['kind'].upper()}] {m['text']} #{m['id']} ({m['match']})"
                 for m in AssociativeIndex(palace).recall(args["query"])]
        for hit in archive.search_archive(palace, args["query"], limit=5):
            lines.append(f"- [CHAT {hit['session_id'][:8]}] {hit['snippet'][:300]}")
        return "\n".join(lines) or "Nothing found."

    def _remember(self, args: dict) -> str:
        palace = self._palace()
        kind = args.get("kind") or "note"
        if kind not in ("note", "decision", "lesson", "constraint"):
            raise ToolError(f"kind must be note, decision, lesson or constraint, not {kind}")
        m = palace.remember(args["text"], self._anchors(palace, args.get("at") or []), kind=kind, source="mcp")
        encoding, other, score = palace.last_encoding
        note = {"integrated": f" (merged into an existing memory, {score:.0%} similar)",
                "related": f" (kept apart from #{other}, {score:.0%} similar, and linked)"}.get(encoding, "")
        lost = f" The new wording dropped: {', '.join(palace.last_lost)}." if palace.last_lost else ""
        return f"Remembered #{m['id']}{note}.{lost}"

    def _constrain(self, args: dict) -> str:
        severity = args.get("severity") or "warning"
        if args.get("at"):
            palace = self._palace()
            m = palace.remember(f"[{severity.upper()}] {args['name']}: {args['rule']}",
                                self._anchors(palace, args["at"]), kind="constraint", memory_id=f"c-{args['name']}")
            return f"Constraint #{m['id']} hung in {', '.join(m['anchors'])}."
        from loci_guard.brain.memory import ProjectMemory

        ProjectMemory(self.root).add_constraint(args["name"], args["rule"], severity)
        return f"Project-wide constraint {args['name']} saved."

    def _intend(self, args: dict) -> str:
        palace = self._palace()
        m = palace.intend(args["text"], self._anchors(palace, args.get("when") or []))
        return f"Intention #{m['id']} will fire on: {', '.join(a or 'next session start' for a in m['anchors'])}."

    def _intend_done(self, args: dict) -> str:
        if self._palace().complete(args["id"]) is None:
            raise ToolError(f"no intention #{args['id']}")
        return f"Intention #{args['id']} done."

    def _history(self, args: dict) -> str:
        versions = self._palace().history(args["id"])
        if versions is None:
            raise ToolError(f"no memory #{args['id']}")
        return "\n".join(f"v{i + 1}{' (current)' if v['until'] is None else ''}: {v['text']}" for i, v in enumerate(versions))

    def _foresight(self):
        from loci_guard.brain.foresight import Foresight

        return Foresight(self._palace())

    def _foresee(self, args: dict) -> str:
        foresight = self._foresight()
        places = args.get("at") or []
        self._anchors(foresight.palace, places)
        return foresight.work_order(foresight.begin(args["description"], places))

    def _foresee_record(self, args: dict) -> str:
        foresight = self._foresight()
        recorded = foresight.record(args["session"], args["predictions"])
        if not recorded:
            raise ToolError("no PREDICT lines found")
        lines = [f"Recorded {len(recorded)}. Calibrated consensus:"]
        lines += [f"- {p['weighted']:.0%} #{p['id']} {p['persona']}: {p['claim']}" for p in foresight.consensus(args["session"])]
        return "\n".join(lines)

    def _foresee_resolve(self, args: dict) -> str:
        foresight = self._foresight()
        p = foresight.resolve(args["id"], bool(args["happened"]))
        score = foresight.score(foresight._score_key(p["persona"]))
        return f"Resolved #{p['id']}; {p['persona']} has {score['n']} resolved, weight {score['weight']}."

    def _check_code(self, args: dict) -> str:
        from loci_guard.gate.validator import GateValidator

        result = GateValidator(self.root).validate_content(args["code"], args.get("filename") or "snippet.py")
        if not result["findings"]:
            return "PASSED: no issues."
        lines = [("BLOCKED" if not result["passed"] else "PASSED with warnings") + ":"]
        for f in result["findings"]:
            fix = f" Fix: {f['fix_suggestion']}" if f.get("fix_suggestion") else ""
            lines.append(f"- [{f['severity'].upper()} {f['rule_id']}] line {f['line']}: {f['message']}.{fix}")
        return "\n".join(lines)


def client_config(python: str, root: Path) -> dict[str, Any]:
    """The usual `mcpServers` entry; most MCP clients accept this shape."""
    return {"mcpServers": {"loci-guard": {
        "command": python, "args": ["-m", "loci_guard", "--root", str(root), "mcp"],
    }}}
