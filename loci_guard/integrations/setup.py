"""One-step setup and the doctor that checks it."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path
from typing import NamedTuple

from loci_guard.integrations.claude_hooks import is_ours

SKILL_DIR = Path(".claude") / "skills" / "loci-guard"
LOCAL_SETTINGS = ".claude/settings.local.json"
HOOK_EVENTS = {
    "PreToolUse": "hook pretool",
    "PostToolUse": "hook enter-room",
    "SessionStart": "hook session-start",
    "PreCompact": "hook archive",
    "SessionEnd": "hook archive",
}
SLOW_HOOK_MS = 1000


def skill_dir() -> Path | None:
    """The skill folder bundled in the wheel, or the repo copy when running from a clone."""
    bundled = resources.files("loci_guard") / "skill"
    if (bundled / "SKILL.md").is_file():
        return Path(str(bundled))
    repo = Path(__file__).resolve().parents[2] / "skills" / "loci-guard"
    return repo if (repo / "SKILL.md").is_file() else None


def skill_source() -> Path | None:
    folder = skill_dir()
    return folder / "SKILL.md" if folder else None


def skill_files(folder: Path) -> list[Path]:
    """SKILL.md and its reference files, relative to the skill folder."""
    return sorted(p.relative_to(folder) for p in folder.rglob("*") if p.is_file() and p.suffix == ".md")


def install_skill(root: Path) -> Path | None:
    """Copy the whole skill (SKILL.md + references/) into .claude/skills/loci-guard/. Never deletes."""
    source = skill_dir()
    if source is None:
        return None
    target = root / SKILL_DIR
    for rel in skill_files(source):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, target / rel)
    return target / "SKILL.md"


def ensure_gitignored(root: Path, entry: str) -> bool:
    """Add `entry` to the project's .gitignore if missing. Returns True when it was added."""
    path = root / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if entry in lines:
        return False
    path.write_text("\n".join(lines + [entry]) + "\n", encoding="utf-8")
    return True


# ---------- doctor ----------

class Check(NamedTuple):
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _hook_commands(root: Path) -> dict[str, tuple[str, str]]:
    """event -> (command, settings file) for the Loci Guard hooks found in project settings."""
    found: dict[str, tuple[str, str]] = {}
    for name in ("settings.local.json", "settings.json"):
        path = root / ".claude" / name
        if not path.is_file():
            continue
        try:
            hooks = json.loads(path.read_text(encoding="utf-8") or "{}").get("hooks", {})
        except json.JSONDecodeError:
            continue
        for event, subcommand in HOOK_EVENTS.items():
            for entry in hooks.get(event, []):
                for hook in entry.get("hooks", []):
                    command = hook.get("command", "")
                    if is_ours(command) and command.endswith(subcommand) and event not in found:
                        found[event] = (command, name)
    return found


def _resolve(command: str) -> list[str] | None:
    argv = shlex.split(command, posix=True)
    if not argv:
        return None
    exe = argv[0]
    if Path(exe).is_file() or shutil.which(exe):
        return argv
    return None


def doctor(root: Path) -> list[Check]:
    checks = [Check("python", sys.version_info >= (3, 10), sys.version.split()[0], "Loci Guard needs Python 3.10+")]

    state = root / ".loci"
    checks.append(Check("project initialized", state.is_dir(), str(state), "run `loci-guard setup`"))
    for name in ("palace.json", "memory.json", "astrocyte.json"):
        path = state / name
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
                checks.append(Check(f"{name} readable", True, f"{path.stat().st_size / 1024:.1f} KB"))
            except (json.JSONDecodeError, OSError) as error:
                checks.append(Check(f"{name} readable", False, str(error), f"move {path} aside; it will be recreated"))

    commands = _hook_commands(root)
    for event in HOOK_EVENTS:
        if event not in commands:
            checks.append(Check(f"hook {event}", False, "not installed", "run `loci-guard integrate --claude-hooks`"))
            continue
        command, source = commands[event]
        argv = _resolve(command)
        checks.append(Check(
            f"hook {event}", argv is not None, f"{source}: {command}",
            "" if argv else "the executable doesn't exist; rerun `loci-guard integrate --claude-hooks` from your environment",
        ))

    session = commands.get("SessionStart")
    argv = _resolve(session[0]) if session else None
    if argv:
        started = time.perf_counter()
        try:
            result = subprocess.run(
                argv, input=json.dumps({"cwd": str(root), "session_id": "doctor"}),
                capture_output=True, text=True, timeout=30, check=False,
            )
            elapsed = (time.perf_counter() - started) * 1000
            ok = result.returncode == 0 and elapsed < SLOW_HOOK_MS
            detail = f"exit {result.returncode}, {elapsed:.0f} ms"
            checks.append(Check("hook runs", ok, detail, "" if ok else (result.stderr.strip()[:200] or "hook is slow")))
        except (OSError, subprocess.TimeoutExpired) as error:
            checks.append(Check("hook runs", False, str(error), "run the hook command by hand to see the error"))

    if any(source == "settings.local.json" for _, source in commands.values()):
        ignored = (root / ".gitignore").is_file() and LOCAL_SETTINGS in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        checks.append(Check("local settings git-ignored", ignored, LOCAL_SETTINGS,
                            "" if ignored else f"add `{LOCAL_SETTINGS}` to .gitignore: it holds a machine-specific path"))

    installed = root / SKILL_DIR
    source = skill_dir()
    if not (installed / "SKILL.md").is_file():
        checks.append(Check("skill installed", False, str(installed / "SKILL.md"), "run `loci-guard setup`"))
    else:
        checks.append(Check("skill installed", True, str((installed / "SKILL.md").relative_to(root))))
        stale = [] if source is None else [
            rel.as_posix() for rel in skill_files(source)
            if not (installed / rel).is_file() or (installed / rel).read_bytes() != (source / rel).read_bytes()
        ]
        checks.append(Check(
            "skill up to date", not stale,
            "matches this version" if not stale else f"missing or older: {', '.join(stale)}",
            "" if not stale else "run `loci-guard setup` to refresh it",
        ))
    return checks
