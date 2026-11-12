"""Main Shield analyzer that combines all scanners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pathspec

from loci_guard.shield.patterns import ALL_PATTERNS, Finding
from loci_guard.shield.security import SecurityScanner


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

DEFAULT_IGNORE = [
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    "dist", "build", "*.pyc", "*.pyo", "*.min.js", "*.min.css",
    "*.lock", "package-lock.json", "yarn.lock", ".loci",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.pdf", "*.zip",
    "*.woff", "*.woff2", "*.ttf", "*.exe", "*.dll", "*.so",
]


class ShieldAnalyzer:
    """Runs every security, privacy and quality rule in one pass over each file."""

    def __init__(self, root: str | Path, extra_ignore: list[str] | None = None):
        self.root = Path(root).resolve()
        self.scanner = SecurityScanner(ALL_PATTERNS)
        self._ignore_spec = self._build_ignore_spec(extra_ignore or [])

    def scan_files(self, files: list[Path]) -> list[Finding]:
        findings = []
        for f in files:
            rel = f.resolve().relative_to(self.root).as_posix() if f.resolve().is_relative_to(self.root) else f.name
            if self._ignore_spec.match_file(rel):
                continue
            findings.extend(self.scanner.scan_file(f))
        findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))
        return self._deduplicate(findings)

    def _build_ignore_spec(self, extra: list[str]) -> pathspec.PathSpec:
        patterns = list(DEFAULT_IGNORE) + list(extra)
        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            patterns.extend(gitignore.read_text(errors="ignore").splitlines())
        return pathspec.GitIgnoreSpec.from_lines(patterns)

    def full_scan(self) -> dict[str, Any]:
        """Run all scanners and return a structured report."""
        all_findings = self.scanner.scan_directory(self.root, self._ignore_spec)
        all_findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))

        unique = self._deduplicate(all_findings)

        return {
            "total_findings": len(unique),
            "by_severity": self._group_by(unique, "severity"),
            "by_category": self._group_by(unique, "category"),
            "findings": [f.to_dict() for f in unique],
            "summary": self._generate_summary(unique),
        }

    def scan_content(self, content: str, filename: str = "<ai-output>") -> dict[str, Any]:
        """Scan AI-generated content before it touches the filesystem."""
        all_findings = self.scanner.scan_content(content, filename)
        all_findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 99))
        unique = self._deduplicate(all_findings)
        return {
            "total_findings": len(unique),
            "findings": [f.to_dict() for f in unique],
            "blocked": any(f.severity in ("critical", "high") for f in unique),
            "summary": self._generate_summary(unique),
        }

    @staticmethod
    def _deduplicate(findings: list[Finding]) -> list[Finding]:
        seen = set()
        unique = []
        for f in findings:
            key = (f.rule_id, f.file, f.line)
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique

    @staticmethod
    def _group_by(findings: list[Finding], attr: str) -> dict[str, int]:
        groups: dict[str, int] = {}
        for f in findings:
            val = getattr(f, attr)
            groups[val] = groups.get(val, 0) + 1
        return groups

    @staticmethod
    def _generate_summary(findings: list[Finding]) -> str:
        if not findings:
            return "No issues found."
        critical = sum(1 for f in findings if f.severity == "critical")
        high = sum(1 for f in findings if f.severity == "high")
        medium = sum(1 for f in findings if f.severity == "medium")
        parts = []
        if critical:
            parts.append(f"{critical} CRITICAL")
        if high:
            parts.append(f"{high} high")
        if medium:
            parts.append(f"{medium} medium")
        return f"Found {len(findings)} issues: {', '.join(parts)}." if parts else f"Found {len(findings)} low/info issues."
