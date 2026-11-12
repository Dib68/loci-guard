"""Tests for the Brain module."""

import tempfile
from pathlib import Path

from loci_guard.brain.memory import ProjectMemory
from loci_guard.brain.rules import RuleEngine
from loci_guard.brain.scanner import ProjectScanner


def test_scanner_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        scanner = ProjectScanner(tmp)
        result = scanner.scan()
        assert result["stats"]["total_files"] == 0
        assert result["languages"] == {}


def test_scanner_detects_python():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "app.py").write_text("def hello():\n    return 'world'\n")
        (Path(tmp) / "utils.py").write_text("import os\n")
        scanner = ProjectScanner(tmp)
        result = scanner.scan()
        assert result["languages"]["python"] == 2
        assert result["stats"]["total_files"] >= 2


def test_scanner_detects_naming():
    with tempfile.TemporaryDirectory() as tmp:
        for name in ["my_module.py", "another_file.py", "third_one.py",
                      "fourth_util.py", "fifth_helper.py"]:
            (Path(tmp) / name).write_text("x = 1\n")
        scanner = ProjectScanner(tmp)
        result = scanner.scan()
        assert result["patterns"]["naming"]["file_naming"] == "snake_case"


def test_memory_persist():
    with tempfile.TemporaryDirectory() as tmp:
        mem = ProjectMemory(tmp)
        mem.add_decision("Use REST", "Team preference")
        mem.add_constraint("no_print", "Do not use print() in production code")

        mem2 = ProjectMemory(tmp)
        assert len(mem2.data["decisions"]) == 1
        assert mem2.data["decisions"][0]["description"] == "Use REST"
        assert len(mem2.get_constraints()) == 1


def test_agents_md_carries_project_context_and_keeps_user_text():
    from loci_guard.integrations import agents_md

    with tempfile.TemporaryDirectory() as tmp:
        mem = ProjectMemory(tmp)
        mem.store_scan({"languages": {"python": 10}, "stats": {"total_files": 10, "total_lines": 500}})
        mem.add_constraint("no_eval", "Never use eval()", severity="critical")
        target = Path(tmp) / "AGENTS.md"
        target.write_text("# Team notes\nKeep me.\n", encoding="utf-8")
        agents_md.write(Path(tmp))
        agents_md.write(Path(tmp))
        text = target.read_text(encoding="utf-8")
        assert "Keep me." in text and "[CRITICAL] no_eval: Never use eval()" in text and "python" in text
        assert text.count("## Loci Guard") == 1


def test_rule_engine_learns_naming():
    with tempfile.TemporaryDirectory() as tmp:
        mem = ProjectMemory(tmp)
        scan = {
            "patterns": {"naming": {"file_naming": "snake_case", "snake_count": 10, "pascal_count": 0}},
            "conventions": {},
            "dependencies": {},
        }
        engine = RuleEngine(mem)
        learned = engine.auto_learn(scan)
        assert any("naming" in r["name"] for r in learned)
        assert len(mem.get_constraints()) >= 1


def test_rule_engine_learns_conventions():
    with tempfile.TemporaryDirectory() as tmp:
        mem = ProjectMemory(tmp)
        scan = {
            "patterns": {"naming": {}},
            "conventions": {"docstring_ratio": 0.8, "type_hint_ratio": 0.7, "avg_max_line_length": 100},
            "dependencies": {},
        }
        engine = RuleEngine(mem)
        learned = engine.auto_learn(scan)
        names = [r["name"] for r in learned]
        assert "require_docstrings" in names
        assert "require_type_hints" in names
