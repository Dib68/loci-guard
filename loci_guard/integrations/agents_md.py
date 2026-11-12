"""AGENTS.md: the instructions file read by Codex, Cursor, Copilot, Hermes and other agents.

It carries the same project context a Claude Code session gets at start, plus how to reach
Loci Guard, so there is one generator instead of one per tool.
"""

from __future__ import annotations

from pathlib import Path

from loci_guard.brain.workspace import session_broadcast
from loci_guard.integrations._blocks import write_managed_block

USAGE = """
## Security (non-negotiable)
- Never hardcode secrets, API keys or credentials; never log passwords or personal data.
- Use parameterized queries; never build SQL or shell commands from user input.

## Loci Guard
This project's memory and security checks are available as the `loci-guard` MCP server or command line:
`project_context` / `loci-guard context` at the start, `recall <path>` before working in a folder,
`check_code` / `loci-guard check` before writing risky code, `constrain` and `remember` for rules and
decisions the user states, `intend` for things to do later in a given place.
"""


def generate(root: Path) -> str:
    context = session_broadcast(root, None, None, dry_run=True).text or "# Project context (Loci Guard)"
    return context + "\n" + USAGE


def write(root: Path) -> Path:
    return write_managed_block(root / "AGENTS.md", generate(root))
