"""Claude Code hooks: block insecure writes, whisper room memories, archive chats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loci_guard.brain.astrocyte import Astrocyte
from loci_guard.brain.palace import MemoryPalace
from loci_guard.brain.workspace import session_broadcast

PRETOOL_COMMAND = "loci-guard hook pretool"
SESSION_COMMAND = "loci-guard hook session-start"
ENTER_ROOM_COMMAND = "loci-guard hook enter-room"
ARCHIVE_COMMAND = "loci-guard hook archive"
WRITE_TOOLS = "Write|Edit|MultiEdit"
FILE_TOOLS = "Read|Write|Edit|MultiEdit"


def proposed_content(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Return only the text the tool is about to add, so old code doesn't re-trigger findings."""
    if tool_name == "Write":
        return tool_input.get("content", "")
    if tool_name == "Edit":
        return tool_input.get("new_string", "")
    if tool_name == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in tool_input.get("edits", []))
    return ""


def format_block_message(file_path: str, blocking: list[dict]) -> str:
    lines = [f"Loci Guard blocked this write to {file_path}:"]
    for f in blocking:
        lines.append(f"- [{f['severity'].upper()} {f['rule_id']}] line {f['line']}: {f['message']}")
        if f.get("fix_suggestion"):
            lines.append(f"  Fix: {f['fix_suggestion']}")
    lines.append(
        "Rewrite the code to fix these issues. If a finding is a false positive, "
        "add `loci-guard: ignore[RULE_ID]` as a comment on that line."
    )
    return "\n".join(lines)


WARN_SEVERITIES = ("critical", "high", "medium")


def format_warnings(file_path: str, warnings: list[dict]) -> str | None:
    """Findings under the block threshold, said once after the write instead of disappearing."""
    shown = [f for f in warnings if f["severity"] in WARN_SEVERITIES]
    if not shown:
        return None
    lines = [f"Loci Guard warnings for what was just written to {file_path} (not blocked; fix them if they apply):"]
    for f in shown:
        lines.append(f"- [{f['severity'].upper()} {f['rule_id']}] line {f['line']}: {f['message']}"
                     + (f" Fix: {f['fix_suggestion']}" if f.get("fix_suggestion") else ""))
    return "\n".join(lines)


def is_initialized(root: Path) -> bool:
    return (root / ".loci").is_dir()


def session_context(root: Path, source: str | None, session_id: str | None) -> str:
    """Project-wide context re-sent on startup and after compaction or /clear, chosen by the workspace."""
    return session_broadcast(root, source, session_id).text or ""


def room_context(root: Path, file_path: str, session_id: str, tool_name: str = "Read") -> str | None:
    """Memories of the room being entered that this session hasn't been told yet, dosed by the astrocyte."""
    return Astrocyte(MemoryPalace(root)).select(session_id, file_path, tool_name)


def note_blocked_write(root: Path, file_path: str, blocking: list[dict]) -> None:
    palace = MemoryPalace(root)
    for f in blocking:
        palace.record_lesson(file_path, f["rule_id"], f["message"])
    Astrocyte(palace).stimulate(file_path, "blocked")


def additional_context(event: str, text: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


PRECOMPACT_INSTRUCTIONS = (
    "Loci Guard has archived the full transcript of this session. In the summary keep: "
    "rules and preferences the user stated, decisions taken and their reasons, and files changed. "
    "Details can be recovered later with `loci-guard recall --search`."
)


# Earlier versions were called CodeSentinel; their hooks are updated in place, not duplicated.
OUR_NAMES = ("loci-guard", "loci_guard", "codesentinel")


def is_ours(command: str) -> bool:
    return any(name in command for name in OUR_NAMES)


def command_prefix(executable: str) -> str:
    """How a hook invokes Loci Guard: the bare command, a launcher path, or `python -m loci_guard`."""
    if executable == "loci-guard":
        return executable
    # Not Path.as_posix(): on Linux a Windows path's backslashes would survive it.
    quoted = '"' + executable.replace("\\", "/") + '"'
    is_python = executable.replace("\\", "/").rsplit("/", 1)[-1].lower().startswith("python")
    return f"{quoted} -m loci_guard" if is_python else quoted


def install(project_root: str | Path, executable: str = "loci-guard") -> Path:
    """Add Loci Guard hooks to Claude Code settings, keeping any existing settings.

    `executable` lets hooks work when loci-guard isn't on PATH (e.g. inside a virtualenv).
    A full path only works on this machine, so it goes to the personal settings.local.json;
    the plain `loci-guard` command goes to the shared settings.json.
    """
    name = "settings.json" if executable == "loci-guard" else "settings.local.json"
    settings_path = Path(project_root).resolve() / ".claude" / name
    settings_path.parent.mkdir(exist_ok=True)
    settings: dict[str, Any] = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")

    # Forward slashes and quotes keep a Windows path with spaces valid in the hook shell.
    prefix = command_prefix(executable)
    hooks = settings.setdefault("hooks", {})
    for event, subcommand, matcher in (
        ("PreToolUse", PRETOOL_COMMAND, WRITE_TOOLS),
        ("PostToolUse", ENTER_ROOM_COMMAND, FILE_TOOLS),
        ("SessionStart", SESSION_COMMAND, None),
        ("PreCompact", ARCHIVE_COMMAND, None),
        ("SessionEnd", ARCHIVE_COMMAND, None),
    ):
        _add_hook(hooks, event, subcommand.replace("loci-guard", prefix, 1), subcommand, matcher)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings_path


def _add_hook(hooks: dict, event: str, command: str, canonical: str, matcher: str | None = None) -> None:
    """Add the hook, or update ours in place (e.g. a new executable path) instead of duplicating it."""
    subcommand = canonical.split(" ", 1)[1]
    entries = hooks.setdefault(event, [])
    for entry in entries:
        for h in entry.get("hooks", []):
            existing = h.get("command", "")
            if is_ours(existing) and existing.endswith(subcommand):
                h["command"] = command
                if matcher:
                    entry["matcher"] = matcher
                return
    entry: dict[str, Any] = {"hooks": [{"type": "command", "command": command}]}
    if matcher:
        entry = {"matcher": matcher, **entry}
    entries.append(entry)
