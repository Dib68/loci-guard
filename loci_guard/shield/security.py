"""Security scanner: credentials, injections, OWASP patterns."""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from loci_guard.shield.patterns import (
    CREDENTIAL_PATTERNS,
    INJECTION_PATTERNS,
    Finding,
    Pattern,
)

MAX_FILE_BYTES = 1_000_000

# `loci-guard: ignore` silences every rule on that line; `ignore[SEC002,PRV001]` only those.
# `loci-guard: ignore-next-line[...]` does the same for the following line.
_SUPPRESS_RE = re.compile(r"loci-guard:\s*ignore(-next-line)?(?:\[([A-Z0-9,\s]+)\])?", re.IGNORECASE)
_ALL = "*"


_FILE_IGNORE = "loci-guard: ignore-file"
_STRING_TOKENS = {tokenize.STRING, tokenize.COMMENT} | {
    t for t in (getattr(tokenize, name, None) for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")) if t
}


def _python_non_code_spans(content: str) -> dict[int, list[tuple[int, float]]]:
    """1-based line -> column ranges covered by string literals or comments."""
    spans: dict[int, list[tuple[int, float]]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(content).readline):
            if tok.type not in _STRING_TOKENS:
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                start = scol if row == srow else 0
                end = ecol if row == erow else float("inf")
                spans.setdefault(row, []).append((start, end))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass  # partial snippets: whatever wasn't tokenized is treated as code
    return spans


def _in_spans(col: int, ranges: list[tuple[int, float]]) -> bool:
    return any(start <= col < end for start, end in ranges)


def _suppressions(lines: list[str]) -> dict[int, set[str]]:
    """Map 1-based line numbers to the rule ids suppressed on them."""
    suppressed: dict[int, set[str]] = {}
    for i, line in enumerate(lines, 1):
        match = _SUPPRESS_RE.search(line)
        if not match:
            continue
        target = i + 1 if match.group(1) else i
        ids = {_ALL} if not match.group(2) else {
            r.strip().upper() for r in match.group(2).split(",") if r.strip()
        }
        suppressed.setdefault(target, set()).update(ids)
    return suppressed


class SecurityScanner:
    """Scans files or raw code for pattern matches."""

    def __init__(self, patterns: list[Pattern] | None = None):
        self.patterns = list(patterns) if patterns is not None else CREDENTIAL_PATTERNS + INJECTION_PATTERNS

    def scan_file(self, filepath: Path) -> list[Finding]:
        try:
            if filepath.stat().st_size > MAX_FILE_BYTES:
                return []
            content = filepath.read_text(errors="ignore")
        except OSError:
            return []
        # Only files already on disk may opt out entirely; content an agent is about to
        # write never can, so the write hook can't be bypassed by adding the directive.
        if any(_FILE_IGNORE in line for line in content.splitlines()[:5]):
            return []
        return self.scan_content(content, str(filepath))

    def scan_content(self, content: str, filename: str = "<unknown>") -> list[Finding]:
        """Scan a string of code, e.g. AI-generated output before it is written."""
        findings = []
        lines = content.splitlines()
        suppressed = _suppressions(lines)
        suffix = Path(filename).suffix
        non_code = _python_non_code_spans(content) if suffix == ".py" else {}

        for pattern in self.patterns:
            if pattern.file_types and suffix not in pattern.file_types:
                continue
            regex = pattern.compiled
            for i, line in enumerate(lines, 1):
                matches = list(regex.finditer(line))
                if pattern.code_only and i in non_code:
                    matches = [m for m in matches if not _in_spans(m.start(), non_code[i])]
                if not matches:
                    continue
                if self._is_excluded(line, pattern.exclude_patterns):
                    continue
                ids = suppressed.get(i, set())
                if _ALL in ids or pattern.id in ids:
                    continue
                findings.append(
                    Finding(
                        rule_id=pattern.id,
                        severity=pattern.severity,
                        category=pattern.category,
                        message=pattern.message,
                        file=filename,
                        line=i,
                        snippet=line.strip()[:120],
                        fix_suggestion=pattern.fix_suggestion,
                    )
                )
        return findings

    def scan_directory(self, root: Path, ignore_spec=None) -> list[Finding]:
        all_findings = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if ignore_spec and ignore_spec.match_file(path.relative_to(root).as_posix()):
                continue
            all_findings.extend(self.scan_file(path))
        return all_findings

    @staticmethod
    def _is_excluded(line: str, exclude_patterns: list[str]) -> bool:
        return any(re.search(excl, line, re.IGNORECASE) for excl in exclude_patterns)
