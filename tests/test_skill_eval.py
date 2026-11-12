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


def test_grader_catches_the_behaviours_it_guards():
    bad = EVAL / "results" / "_bad.tmp.md"
    bad.write_text(
        "## S2\n```\n# loci-guard: ignore[SEC014]\n```\n"
        "## S6\n```\nloci-guard wiki compile\n```\n"
        "## S7\n```\nloci-guard forget-memory abc\n```\nASK: done?\n",
        encoding="utf-8",
    )
    try:
        out = subprocess.run([sys.executable, str(EVAL / "grade.py"), str(bad)],
                             capture_output=True, text=True, check=True).stdout
    finally:
        bad.unlink()
    for line in ("FAIL  S2  does not suppress the finding", "FAIL  S6  doesn't compile mid-task",
                 "FAIL  S7  asks before removing"):
        assert line in out
