"""Rule engine: learns project rules from scan data and enforces them."""

from __future__ import annotations

from typing import Any

from loci_guard.brain.memory import ProjectMemory


class RuleEngine:
    """Automatically derives constraints from project scan results."""

    def __init__(self, memory: ProjectMemory):
        self.memory = memory

    def auto_learn(self, scan: dict[str, Any]) -> list[dict]:
        """Analyze scan results and create constraints from detected patterns."""
        learned = []
        learned.extend(self._learn_naming_rules(scan))
        learned.extend(self._learn_convention_rules(scan))
        learned.extend(self._learn_architecture_rules(scan))
        learned.extend(self._learn_dependency_rules(scan))
        return learned

    def _learn_naming_rules(self, scan: dict) -> list[dict]:
        rules = []
        naming = scan.get("patterns", {}).get("naming", {})
        style = naming.get("file_naming")
        if style:
            total = naming.get("snake_count", 0) + naming.get("pascal_count", 0)
            if total >= 5:
                rule = {
                    "name": "file_naming_convention",
                    "rule": f"Python files must use {style} naming (detected in {total} files)",
                    "severity": "warning",
                }
                self.memory.add_constraint(auto_learned=True, **rule)
                rules.append(rule)
        return rules

    def _learn_convention_rules(self, scan: dict) -> list[dict]:
        rules = []
        conv = scan.get("conventions", {})

        if conv.get("docstring_ratio", 0) >= 0.6:
            rule = {
                "name": "require_docstrings",
                "rule": f"Functions should have docstrings (project rate: {conv['docstring_ratio']:.0%})",
                "severity": "warning",
            }
            self.memory.add_constraint(auto_learned=True, **rule)
            rules.append(rule)

        if conv.get("type_hint_ratio", 0) >= 0.6:
            rule = {
                "name": "require_type_hints",
                "rule": f"Functions should have return type hints (project rate: {conv['type_hint_ratio']:.0%})",
                "severity": "warning",
            }
            self.memory.add_constraint(auto_learned=True, **rule)
            rules.append(rule)

        line_len = conv.get("avg_max_line_length", 0)
        if 80 <= line_len <= 120:
            rule = {
                "name": "line_length",
                "rule": f"Keep lines under {line_len + 10} characters (project avg max: {line_len})",
                "severity": "info",
            }
            self.memory.add_constraint(auto_learned=True, **rule)
            rules.append(rule)

        return rules

    def _learn_architecture_rules(self, scan: dict) -> list[dict]:
        rules = []
        arch = scan.get("patterns", {}).get("architecture", [])
        if arch:
            rule = {
                "name": "architecture_pattern",
                "rule": f"Project follows {', '.join(arch)} pattern(s). New code should respect this structure.",
                "severity": "warning",
            }
            self.memory.add_constraint(auto_learned=True, **rule)
            rules.append(rule)
        return rules

    def _learn_dependency_rules(self, scan: dict) -> list[dict]:
        rules = []
        deps = scan.get("dependencies", {})
        for lang, dep_list in deps.items():
            if dep_list:
                rule = {
                    "name": f"known_dependencies_{lang}",
                    "rule": f"Prefer existing {lang} dependencies ({', '.join(dep_list[:10])}) before adding new ones.",
                    "severity": "info",
                }
                self.memory.add_constraint(auto_learned=True, **rule)
                rules.append(rule)
        return rules
