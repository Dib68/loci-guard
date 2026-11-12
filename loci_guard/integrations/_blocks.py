"""Write generated content into a marked block so hand-written file content survives."""

from __future__ import annotations

from pathlib import Path

START = "<!-- loci-guard:start -->"
END = "<!-- loci-guard:end -->"


def write_managed_block(path: Path, content: str) -> Path:
    block = f"{START}\n{content.rstrip()}\n{END}\n"
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return path
    existing = path.read_text(encoding="utf-8")
    start, end = existing.find(START), existing.find(END)
    if start != -1 and end > start:
        updated = existing[:start] + block + existing[end + len(END):].lstrip("\n")
    else:
        updated = existing.rstrip("\n") + "\n\n" + block
    path.write_text(updated, encoding="utf-8")
    return path
