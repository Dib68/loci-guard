"""Scans a codebase to extract patterns, conventions, and architecture."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pathspec


DEFAULT_IGNORE = [
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".loci",
    "*.pyc", "*.pyo", "*.egg-info",
]

LANG_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
}


class ProjectScanner:
    """Scans a project directory and extracts structural knowledge."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self._ignore_spec = self._build_ignore_spec()

    def _build_ignore_spec(self) -> pathspec.PathSpec:
        patterns = list(DEFAULT_IGNORE)
        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            patterns.extend(gitignore.read_text(errors="ignore").splitlines())
        return pathspec.GitIgnoreSpec.from_lines(patterns)

    def scan(self) -> dict[str, Any]:
        """Full project scan. Returns a structured knowledge map."""
        files = list(self._walk_files())
        return {
            "root": str(self.root),
            "stats": self._compute_stats(files),
            "languages": self._detect_languages(files),
            "structure": self._map_structure(files),
            "patterns": self._detect_patterns(files),
            "dependencies": self._detect_dependencies(),
            "conventions": self._detect_conventions(files),
        }

    def _walk_files(self):
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root)
            if not self._ignore_spec.match_file(str(rel)):
                yield path

    def _compute_stats(self, files: list[Path]) -> dict:
        total_lines = 0
        total_size = 0
        for f in files:
            total_size += f.stat().st_size
            if f.suffix in LANG_EXTENSIONS:
                try:
                    total_lines += len(f.read_text(errors="ignore").splitlines())
                except OSError:
                    pass
        return {
            "total_files": len(files),
            "total_lines": total_lines,
            "total_size_kb": round(total_size / 1024, 1),
        }

    def _detect_languages(self, files: list[Path]) -> dict[str, int]:
        counts: Counter = Counter()
        for f in files:
            lang = LANG_EXTENSIONS.get(f.suffix)
            if lang:
                counts[lang] += 1
        return dict(counts.most_common())

    def _map_structure(self, files: list[Path]) -> dict[str, list[str]]:
        """Map top-level directories and their purpose."""
        dirs: defaultdict[str, list] = defaultdict(list)
        for f in files:
            rel = f.relative_to(self.root)
            parts = rel.parts
            top = parts[0] if len(parts) > 1 else "."
            dirs[top].append(str(rel))
        return {
            k: v[:20] for k, v in sorted(dirs.items())  # cap for readability
        }

    def _detect_patterns(self, files: list[Path]) -> dict[str, Any]:
        """Detect naming conventions, architecture patterns, etc."""
        patterns = {
            "naming": self._detect_naming(files),
            "architecture": self._detect_architecture(files),
            "test_framework": self._detect_test_framework(files),
        }
        return patterns

    def _detect_naming(self, files: list[Path]) -> dict:
        py_files = [f for f in files if f.suffix == ".py"]
        if not py_files:
            return {}
        snake = sum(1 for f in py_files if re.match(r"^[a-z][a-z0-9_]*\.py$", f.name))
        camel = sum(1 for f in py_files if re.match(r"^[A-Z][a-zA-Z0-9]*\.py$", f.name))
        return {
            "file_naming": "snake_case" if snake >= camel else "PascalCase",
            "snake_count": snake,
            "pascal_count": camel,
        }

    def _detect_architecture(self, files: list[Path]) -> list[str]:
        markers = {
            "mvc": ["models", "views", "controllers"],
            "layered": ["services", "repositories", "controllers"],
            "modular": ["modules", "plugins", "extensions"],
            "microservices": ["docker-compose", "Dockerfile"],
            "monorepo": ["packages", "apps", "libs"],
        }
        dir_names = {p.parent.name for p in files}
        file_names = {p.name for p in files}
        detected = []
        for arch, signals in markers.items():
            if any(s in dir_names or s in file_names for s in signals):
                detected.append(arch)
        return detected

    def _detect_test_framework(self, files: list[Path]) -> str | None:
        file_names = {f.name for f in files}
        if "pytest.ini" in file_names or "conftest.py" in file_names:
            return "pytest"
        if "jest.config.js" in file_names or "jest.config.ts" in file_names:
            return "jest"
        if any(f.name.startswith("test_") for f in files if f.suffix == ".py"):
            return "pytest (inferred)"
        if any(f.name.endswith(".test.js") or f.name.endswith(".spec.ts") for f in files):
            return "jest (inferred)"
        return None

    def _detect_dependencies(self) -> dict[str, list[str]]:
        deps = {}
        req = self.root / "requirements.txt"
        if req.exists():
            lines = req.read_text(errors="ignore").splitlines()
            deps["python"] = [
                l.strip().split("==")[0].split(">=")[0].split("<=")[0]
                for l in lines
                if l.strip() and not l.startswith("#")
            ][:50]
        pkg = self.root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(errors="ignore"))
                all_deps = list(data.get("dependencies", {}).keys())
                all_deps += list(data.get("devDependencies", {}).keys())
                deps["javascript"] = all_deps[:50]
            except json.JSONDecodeError:
                pass
        pyproject = self.root / "pyproject.toml"
        if pyproject.exists() and "python" not in deps:
            names = self._pyproject_dependencies(pyproject)
            if names:
                deps["python"] = names[:50]
        return deps

    @staticmethod
    def _pyproject_dependencies(path: Path) -> list[str]:
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            return []
        try:
            data = tomllib.loads(path.read_text(errors="ignore"))
        except tomllib.TOMLDecodeError:
            return []
        specs = list(data.get("project", {}).get("dependencies", []))
        for group in data.get("project", {}).get("optional-dependencies", {}).values():
            specs.extend(group)
        names = []
        for spec in specs:
            match = re.match(r"[A-Za-z0-9_.-]+", spec.strip())
            if match and match.group(0) not in names:
                names.append(match.group(0))
        return names

    def _detect_conventions(self, files: list[Path]) -> dict[str, Any]:
        """Detect code conventions from Python files using AST."""
        py_files = [f for f in files if f.suffix == ".py"][:30]
        if not py_files:
            return {}

        docstring_count = 0
        type_hint_count = 0
        total_functions = 0
        max_line_lengths = []

        for pf in py_files:
            try:
                source = pf.read_text(errors="ignore")
                lines = source.splitlines()
                if lines:
                    max_line_lengths.append(max(len(l) for l in lines))
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        total_functions += 1
                        if (
                            node.body
                            and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                            and isinstance(node.body[0].value.value, str)
                        ):
                            docstring_count += 1
                        if node.returns:
                            type_hint_count += 1
            except (SyntaxError, UnicodeDecodeError):
                continue

        return {
            "docstring_ratio": round(docstring_count / max(total_functions, 1), 2),
            "type_hint_ratio": round(type_hint_count / max(total_functions, 1), 2),
            "avg_max_line_length": round(
                sum(max_line_lengths) / max(len(max_line_lengths), 1)
            ),
            "total_functions_sampled": total_functions,
        }
