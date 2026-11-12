"""Hook entry points, kept free of the CLI's imports (click, rich) so they start fast.

They run after every file read and write, so startup time matters more than anywhere else.
Each takes the hook JSON on stdin and returns the process exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from loci_guard.brain.palace import MemoryPalace
from loci_guard.integrations import claude_hooks


def _payload() -> dict:
    # utf-8-sig drops the BOM that PowerShell pipes add; plain utf-8 would make json.loads fail.
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


DEFAULT_ROOT = Path(".")  # `loci-guard --root X hook ...` sets it


def _root(payload: dict) -> Path:
    return Path(payload.get("cwd") or DEFAULT_ROOT).resolve()


def pretool(payload: dict) -> int:
    """PreToolUse: block Write/Edit calls that add insecure code (exit 2 shows stderr to the model)."""
    if not payload:
        # Never break the user's session over a malformed payload, but don't fail silently either.
        sys.stderr.write("Loci Guard: unreadable PreToolUse payload, write not checked\n")
        return 0
    tool_input = payload.get("tool_input", {})
    content = claude_hooks.proposed_content(payload.get("tool_name", ""), tool_input)
    if not content.strip():
        return 0

    from loci_guard.gate.validator import GateValidator

    file_path = tool_input.get("file_path", "<ai-output>")
    root = _root(payload)
    result = GateValidator(root).validate_content(content, file_path)
    if result["passed"]:
        return 0
    if claude_hooks.is_initialized(root):
        claude_hooks.note_blocked_write(root, file_path, result["blocked_by"])
    sys.stderr.write(claude_hooks.format_block_message(file_path, result["blocked_by"]) + "\n")
    return 2


def session_start(payload: dict) -> int:
    """SessionStart: re-inject project memory and the palace map (also after compaction)."""
    root = _root(payload)
    if claude_hooks.is_initialized(root):
        sys.stdout.write(claude_hooks.session_context(root, payload.get("source"), payload.get("session_id")) + "\n")
    return 0


def enter_room(payload: dict) -> int:
    """PostToolUse on file tools: tell Claude what the workspace broadcasts for the room it entered."""
    root = _root(payload)
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path or not claude_hooks.is_initialized(root):
        return 0
    tool_name = payload.get("tool_name") or "Read"
    parts = [claude_hooks.room_context(root, file_path, payload.get("session_id") or "default", tool_name)]
    content = claude_hooks.proposed_content(tool_name, tool_input)
    if content.strip():
        # PreToolUse only blocks; findings under the threshold are told to the agent here.
        from loci_guard.gate.validator import GateValidator

        warnings = GateValidator(root).validate_content(content, file_path)["warnings"]
        parts.append(claude_hooks.format_warnings(file_path, warnings))
    text = "\n\n".join(p for p in parts if p)
    if text:
        sys.stdout.write(claude_hooks.additional_context("PostToolUse", text) + "\n")
    return 0


def archive_chat(payload: dict) -> int:
    """PreCompact / SessionEnd: archive the transcript into the palace."""
    root = _root(payload)
    transcript = payload.get("transcript_path")
    if not transcript or not claude_hooks.is_initialized(root):
        return 0
    from loci_guard.brain import archive  # pulls in the shield patterns; only this hook needs them

    try:
        archive.ingest(MemoryPalace(root), transcript)
    except (OSError, ValueError):
        pass  # archiving is best effort; never block compaction or exit
    if payload.get("hook_event_name") == "PreCompact":
        # PreCompact stdout becomes instructions for the compaction summary.
        sys.stdout.write(claude_hooks.PRECOMPACT_INSTRUCTIONS + "\n")
    return 0


HOOKS = {"pretool": pretool, "session-start": session_start, "enter-room": enter_room, "archive": archive_chat}


def run(name: str) -> int:
    handler = HOOKS.get(name)
    if handler is None:
        sys.stderr.write(f"Loci Guard: unknown hook '{name}' (expected one of {', '.join(HOOKS)})\n")
        return 1
    return handler(_payload())
