"""Tests for one-step setup and the doctor."""

import json

from click.testing import CliRunner

from loci_guard.cli import main
from loci_guard.integrations import claude_hooks
from loci_guard.integrations.setup import (
    LOCAL_SETTINGS,
    SKILL_DIR,
    doctor,
    ensure_gitignored,
    install_skill,
    skill_source,
)


def _checks(root):
    return {c.name: c for c in doctor(root)}


def test_skill_source_is_found_from_a_clone():
    source = skill_source()
    assert source is not None and source.read_text(encoding="utf-8").startswith("---\nname: loci-guard")


def test_install_skill_and_gitignore_are_idempotent(tmp_path):
    target = install_skill(tmp_path)
    assert target == tmp_path / SKILL_DIR / "SKILL.md" and target.is_file()
    assert ensure_gitignored(tmp_path, LOCAL_SETTINGS)
    assert not ensure_gitignored(tmp_path, LOCAL_SETTINGS)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines() == [LOCAL_SETTINGS]


def test_doctor_on_an_empty_project_says_what_to_do(tmp_path):
    checks = _checks(tmp_path)
    assert not checks["project initialized"].ok and "loci-guard setup" in checks["project initialized"].fix
    assert not checks["hook PreToolUse"].ok
    assert not checks["skill installed"].ok


def test_doctor_flags_hooks_pointing_to_a_missing_executable(tmp_path):
    claude_hooks.install(tmp_path, str(tmp_path / "gone" / "loci_guard.exe"))
    checks = _checks(tmp_path)
    assert not checks["hook PreToolUse"].ok and "doesn't exist" in checks["hook PreToolUse"].fix
    assert not checks["local settings git-ignored"].ok


def test_doctor_flags_corrupt_state_and_outdated_skill(tmp_path):
    state = tmp_path / ".loci"
    state.mkdir()
    (state / "palace.json").write_text("{not json", encoding="utf-8")
    install_skill(tmp_path).write_text("old skill", encoding="utf-8")
    checks = _checks(tmp_path)
    assert not checks["palace.json readable"].ok
    assert checks["skill installed"].ok and not checks["skill up to date"].ok


def test_setup_command_prepares_a_fresh_project(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    fake = str(tmp_path / "bin" / "loci_guard.exe")
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "setup", "--no-ingest", "--command", fake])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".loci" / "config.yaml").is_file()
    assert (tmp_path / SKILL_DIR / "SKILL.md").is_file()
    hooks = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "SessionStart", "PreCompact", "SessionEnd"}
    assert LOCAL_SETTINGS in (tmp_path / ".gitignore").read_text()
    assert "need attention" in result.output  # the fake executable doesn't exist
    assert CliRunner().invoke(main, ["--root", str(tmp_path), "doctor"]).exit_code == 1
