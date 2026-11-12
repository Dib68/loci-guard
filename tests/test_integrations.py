# loci-guard: ignore-file  (fixtures contain fake secrets on purpose)
"""Tests for suppression, memory upserts, managed blocks, and Claude Code hooks."""

import json

from click.testing import CliRunner

from loci_guard.brain.memory import ProjectMemory
from loci_guard.brain.rules import RuleEngine
from loci_guard.cli import main
from loci_guard.integrations import claude_hooks
from loci_guard.integrations._blocks import END, START, write_managed_block
from loci_guard.shield.security import SecurityScanner

AWS_LINE = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'


def test_inline_ignore_all_rules():
    findings = SecurityScanner().scan_content(f"{AWS_LINE}  # loci-guard: ignore", "a.py")
    assert findings == []


def test_inline_ignore_specific_rule_only():
    scanner = SecurityScanner()
    assert scanner.scan_content(f"{AWS_LINE}  # loci-guard: ignore[SEC003]", "a.py") == []
    assert scanner.scan_content(f"{AWS_LINE}  # loci-guard: ignore[SEC001]", "a.py")


def test_ignore_next_line():
    code = f"# loci-guard: ignore-next-line[SEC003]\n{AWS_LINE}\n{AWS_LINE}\n"
    findings = SecurityScanner().scan_content(code, "a.py")
    assert [f.line for f in findings if f.rule_id == "SEC003"] == [3]


def test_rescan_does_not_duplicate_learned_rules(tmp_path):
    scan = {
        "patterns": {"naming": {"file_naming": "snake_case", "snake_count": 9, "pascal_count": 0}},
        "conventions": {"docstring_ratio": 0.9},
        "dependencies": {},
    }
    memory = ProjectMemory(tmp_path)
    RuleEngine(memory).auto_learn(scan)
    first = len(memory.get_constraints())
    RuleEngine(memory).auto_learn(scan)
    assert len(ProjectMemory(tmp_path).get_constraints()) == first


def test_auto_learn_never_overwrites_manual_rule(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.add_constraint("require_docstrings", "Only public functions need docstrings", "info")
    RuleEngine(memory).auto_learn({"conventions": {"docstring_ratio": 0.9}, "patterns": {}, "dependencies": {}})
    rule = next(c for c in memory.get_constraints() if c["name"] == "require_docstrings")
    assert rule["rule"] == "Only public functions need docstrings"


def test_forget_removes_constraint(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.add_constraint("no_print", "No print()")
    assert memory.remove_constraint("no_print")
    assert not memory.remove_constraint("no_print")


def test_managed_block_keeps_user_content_and_is_idempotent(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("# My notes\nKeep this.\n", encoding="utf-8")
    write_managed_block(path, "generated v1")
    write_managed_block(path, "generated v2")
    text = path.read_text(encoding="utf-8")
    assert "Keep this." in text
    assert "generated v2" in text and "generated v1" not in text
    assert text.count(START) == 1 and text.count(END) == 1


def test_install_claude_hooks_merges_existing_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    claude_hooks.install(tmp_path)
    claude_hooks.install(tmp_path)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert data["hooks"]["PreToolUse"][0]["matcher"] == claude_hooks.WRITE_TOOLS
    assert len(data["hooks"]["SessionStart"]) == 1


def test_proposed_content_only_new_text():
    assert claude_hooks.proposed_content("Edit", {"old_string": "x", "new_string": "y"}) == "y"
    multi = {"edits": [{"new_string": "a"}, {"new_string": "b"}]}
    assert claude_hooks.proposed_content("MultiEdit", multi) == "a\nb"
    assert claude_hooks.proposed_content("Read", {"file_path": "x"}) == ""


def _pretool(tmp_path, tool_name, tool_input):
    payload = {"tool_name": tool_name, "tool_input": tool_input, "cwd": str(tmp_path)}
    return CliRunner().invoke(main, ["hook", "pretool"], input=json.dumps(payload))


def test_pretool_hook_blocks_insecure_write(tmp_path):
    result = _pretool(tmp_path, "Write", {"file_path": str(tmp_path / "s.py"), "content": AWS_LINE})
    assert result.exit_code == 2
    assert "SEC003" in result.output


SHELL_CODE = "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n"


def test_pretool_hook_blocks_high_severity_injection_by_default(tmp_path):
    # A fresh project once let shell=True, eval and SQL f-strings through silently: only critical blocked.
    result = _pretool(tmp_path, "Write", {"file_path": str(tmp_path / "run.py"), "content": SHELL_CODE})
    assert result.exit_code == 2 and "SEC014" in result.output


def test_findings_under_the_threshold_reach_the_agent_after_the_write(tmp_path):
    (tmp_path / ".loci").mkdir()
    (tmp_path / ".loci" / "config.yaml").write_text("gate:\n  block_severity: critical\n", encoding="utf-8")
    write = {"file_path": str(tmp_path / "run.py"), "content": SHELL_CODE}
    assert _pretool(tmp_path, "Write", write).exit_code == 0
    payload = {"tool_name": "Write", "tool_input": write, "cwd": str(tmp_path), "session_id": "s"}
    out = CliRunner().invoke(main, ["hook", "enter-room"], input=json.dumps(payload)).output
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "Loci Guard warnings" in context and "SEC014" in context


def test_pretool_hook_allows_clean_edit(tmp_path):
    edit = {"file_path": str(tmp_path / "s.py"), "old_string": "a", "new_string": "key = os.environ['KEY']"}
    assert _pretool(tmp_path, "Edit", edit).exit_code == 0


def test_pretool_hook_ignores_bad_payload():
    assert CliRunner().invoke(main, ["hook", "pretool"], input="not json").exit_code == 0


def test_pretool_hook_reads_payload_with_bom(tmp_path):
    payload = {"tool_name": "Write", "cwd": str(tmp_path),
               "tool_input": {"file_path": str(tmp_path / "s.py"), "content": AWS_LINE}}
    raw = b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
    assert CliRunner().invoke(main, ["hook", "pretool"], input=raw).exit_code == 2


def test_session_start_hook_prints_constraints(tmp_path):
    ProjectMemory(tmp_path).add_constraint("no_eval", "Never use eval()", "critical")
    result = CliRunner().invoke(main, ["hook", "session-start"], input=json.dumps({"cwd": str(tmp_path)}))
    assert result.exit_code == 0
    assert "Never use eval()" in result.output


def test_hooks_fall_back_to_root_when_the_payload_has_no_cwd(tmp_path):
    ProjectMemory(tmp_path).add_constraint("no_eval", "Never use eval()", "critical")
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "hook", "session-start"], input="{}")
    assert "Never use eval()" in result.output


def test_check_command_exit_codes(tmp_path):
    runner = CliRunner()
    blocked = runner.invoke(main, ["--root", str(tmp_path), "check", "--filename", "a.py", "-"], input=AWS_LINE)
    clean = runner.invoke(main, ["--root", str(tmp_path), "check", "--filename", "a.py", "-"], input="x = 1\n")
    assert blocked.exit_code == 1
    assert clean.exit_code == 0
