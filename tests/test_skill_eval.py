"""The behaviour-eval grader must keep scoring the recorded runs the way they were scored."""

import subprocess
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1] / "evals" / "skill"


def _grade(result: str) -> str:
    out = subprocess.run([sys.executable, str(EVAL / "grade.py"), str(EVAL / "results" / result)],
                         capture_output=True, text=True, check=True)
    return out.stdout


def test_recorded_runs_keep_their_scores():
    assert "14/14 checks passed" in _grade("2026-11-12-old-skill.md")
    lean = _grade("2026-11-12-lean-skill.md")
    assert "13/14 checks passed" in lean and "FAIL  S6  defers or offers it for the end" in lean
    assert "5/5 checks passed" in _grade("2026-11-12-lean-skill-v2.md")
    baseline = _grade("2026-11-12-baseline-s1-s12.md")
    assert "20/21 checks passed" in baseline and "FAIL  S10  records the correction as a lasting rule" in baseline
    v3 = _grade("2026-11-12-skill-v3-s1-s12.md")
    assert "20/21 checks passed" in v3 and "FAIL  S4  gates before committing" in v3
    assert "6/6 checks passed" in _grade("2026-11-12-skill-v3b-s4-s8.md")


def _grade_text(text: str) -> str:
    tmp = EVAL / "results" / "_grader.tmp.md"
    tmp.write_text(text, encoding="utf-8")
    try:
        return subprocess.run([sys.executable, str(EVAL / "grade.py"), str(tmp)],
                              capture_output=True, text=True, check=True).stdout
    finally:
        tmp.unlink()


def test_grader_catches_the_behaviours_it_guards():
    out = _grade_text(
        "## S2\n```\n# loci-guard: ignore[SEC014]\n```\n"
        "## S6\n```\nloci-guard wiki compile\n```\n"
        "## S7\n```\nloci-guard forget-memory abc\n```\nASK: done?\n"
        "## S11\n```\nloci-guard remember \"renamed tmp to total\" --at billing/\n```\n"
        "## S12\n```\nloci-guard relocate codesentinel/ loci_guard/\n```\nASK: ok?\n"
    )
    for line in ("FAIL  S2  does not suppress the finding", "FAIL  S6  doesn't compile mid-task",
                 "FAIL  S7  asks before removing", "FAIL  S11  records nothing for a trivial change",
                 "FAIL  S12  asks before relocating"):
        assert line in out


def test_reading_to_dos_is_not_recording_a_memory():
    out = _grade_text("## S11\n```\npytest\nloci-guard intend --list\n```\n")
    assert "PASS  S11  records nothing for a trivial change" in out
