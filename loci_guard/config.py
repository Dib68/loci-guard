"""Project configuration: .loci/config.yaml. Only settings the code reads live here."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "brain": {"auto_learn": True},            # derive rules from the scan
    # high blocks injection (SQL, shell, eval, innerHTML), JWTs, DB URLs and PII in logs, not just
    # hardcoded keys: the mistakes AI-written code makes most. critical | high | medium
    "gate": {"block_severity": "high"},
    "ignore_paths": ["node_modules", ".venv", "venv", "__pycache__", "dist", "build"],
}


class Config:
    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        self.config_path = self.root / ".loci" / "config.yaml"
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        if not self.config_path.exists():
            return defaults
        try:
            user_config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            return defaults
        return self._merge(defaults, user_config) if isinstance(user_config, dict) else defaults

    def save(self) -> None:
        self.config_path.parent.mkdir(exist_ok=True)
        self.config_path.write_text(yaml.dump(self._data, default_flow_style=False, sort_keys=False), encoding="utf-8")

    def get(self, dotpath: str, default: Any = None) -> Any:
        """Get a value by dot path, e.g. 'gate.block_severity'."""
        current = self._data
        for part in dotpath.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    @staticmethod
    def _merge(base: dict, override: dict) -> dict:
        result = dict(base)
        for key, value in override.items():
            if isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = Config._merge(result[key], value)
            else:
                result[key] = value
        return result
