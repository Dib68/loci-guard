"""Gate: validates code against brain constraints + shield checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from loci_guard.brain.memory import ProjectMemory
from loci_guard.config import Config
from loci_guard.shield.analyzer import SEVERITY_ORDER, ShieldAnalyzer
from loci_guard.shield.patterns import Finding

HOOK_MARKER = "# Loci Guard pre-commit hook"


class GateValidator:
    """Validates staged, changed, or proposed code. Usable as a hook or a library."""

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        self.config = Config(self.root)
        self.memory = ProjectMemory(self.root)
        self.shield = ShieldAnalyzer(self.root, extra_ignore=self.config.get("ignore_paths", []))
        self.block_level = SEVERITY_ORDER.get(self.config.get("gate.block_severity", "critical"), 0)

    def validate_staged(self) -> dict[str, Any]:
        return self._validate_paths(self._git_files(["diff", "--cached", "--name-only", "--diff-filter=ACMR"]))

    def validate_changed_since(self, ref: str) -> dict[str, Any]:
        """Validate files changed between `ref` and HEAD (for CI on pull requests)."""
        return self._validate_paths(self._git_files(["diff", "--name-only", "--diff-filter=ACMR", f"{ref}...HEAD"]))

    def validate_files(self, files: list[str]) -> dict[str, Any]:
        return self._validate_paths([Path(f).resolve() for f in files if Path(f).is_file()])

    def validate_content(self, content: str, filename: str = "<ai-output>") -> dict[str, Any]:
        """Validate AI-generated content before it is written to disk."""
        findings = self.shield.scanner.scan_content(content, filename)
        findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))
        result = self._result(findings)
        result["constraint_violations"] = self._check_constraints(filename)
        return result

    def _validate_paths(self, paths: list[Path]) -> dict[str, Any]:
        if not paths:
            return {**self._result([]), "message": "No files to validate."}
        return self._result(self.shield.scan_files(paths))

    def _result(self, findings: list[Finding]) -> dict[str, Any]:
        blocking = [f for f in findings if SEVERITY_ORDER.get(f.severity, 99) <= self.block_level]
        return {
            "passed": not blocking,
            "total_findings": len(findings),
            "blocked_by": [f.to_dict() for f in blocking],
            "warnings": [f.to_dict() for f in findings if f not in blocking],
            "findings": [f.to_dict() for f in findings],
        }

    def _check_constraints(self, filename: str) -> list[dict]:
        violations = []
        stem = Path(filename).stem
        if not stem or stem.startswith("<"):
            return violations
        for c in self.memory.get_constraints():
            if c["name"] == "file_naming_convention" and Path(filename).suffix == ".py":
                if "snake_case" in c["rule"] and stem != stem.lower():
                    violations.append({
                        "constraint": c["name"],
                        "severity": c.get("severity", "warning"),
                        "message": f"'{Path(filename).name}' breaks the project rule: {c['rule']}",
                    })
        return violations

    def _git_files(self, args: list[str]) -> list[Path]:
        try:
            result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=self.root)
        except FileNotFoundError:
            return []
        if result.returncode != 0:
            return []
        return [self.root / line.strip() for line in result.stdout.splitlines() if line.strip()]

    def install_git_hook(self) -> bool:
        hooks_dir = self.root / ".git" / "hooks"
        if not hooks_dir.exists():
            return False
        hook_path = hooks_dir / "pre-commit"
        if hook_path.exists() and HOOK_MARKER not in hook_path.read_text(errors="ignore"):
            # Don't clobber a hook someone else installed; append to it instead.
            existing = hook_path.read_text(errors="ignore").rstrip("\n")
            hook_path.write_text(f"{existing}\n\n{HOOK_MARKER}\nloci-guard gate --staged || exit 1\n")
        else:
            hook_path.write_text(f"#!/bin/sh\n{HOOK_MARKER}\nloci-guard gate --staged || exit 1\n")
        hook_path.chmod(0o755)
        return True
