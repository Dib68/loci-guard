"""Compiled wiki (Karpathy's LLM Wiki pattern) on top of the memory palace.

Raw sources are the palace memories and archived chats; they are never edited. The wiki is
one Markdown page per room, written by the agent from those sources and kept in
.loci/wiki/. Loci Guard never writes page prose: it decides which rooms need
work, hands the agent a work order, then checks and stamps the page the agent wrote.

Recompilation is driven by prediction errors: a page "predicts" its room. Only material it
did not account for (new memories, memories that changed since the page was compiled, such
as a lesson blocked again, and sources that disappeared) asks for a rewrite, weighted by how
surprising it is. Rooms with no surprises are left alone.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Callable

from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.text import facts

WIKI_DIR = "wiki"
SCHEMA_FILE = "SCHEMA.md"
INDEX_FILE = "index.md"
ATRIUM_PAGE = "_atrium.md"

SURPRISE = {"lesson": 3.0, "constraint": 3.0, "decision": 2.0, "note": 1.0, "chat": 1.0}
NOT_KNOWLEDGE = ("candidate", "intention")  # unconfirmed rules and to-dos don't belong on a page
CHANGED_BONUS = 1.0  # a known memory that changed means the page's prediction failed
REMOVED_WEIGHT = 1.0

_META = re.compile(r"\A<!-- loci-guard:wiki\n(?P<meta>.*?)\n-->\n?", re.DOTALL)
CITATION = re.compile(r"\[#([A-Za-z0-9_-]+)\]")
_MD_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")
_CODE_SPAN = re.compile(r"`([^`\s]+)`")
_PATHISH = re.compile(r"[\w.-]+(?:/[\w.-]+)+/?|[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|md|toml|yaml|yml|json)")

SCHEMA_TEXT = """# Wiki schema

This file tells the agent how to write and maintain the pages in this folder.
It is yours: edit it freely. Loci Guard creates it once and never overwrites it.

## One page per room
- A page describes one folder of the repository (its "room"): what lives there and the knowledge needed to change it safely.
- Write only the body. Loci Guard adds and updates the header comment itself when you run `loci-guard wiki done <room>`.

## Structure
Use these sections, skipping any that would be empty:
1. `# <room>` and one or two sentences on what this area is for.
2. `## Rules` - constraints that must hold here.
3. `## Pitfalls` - mistakes made here before (lessons) and how to avoid them.
4. `## Decisions` - what was chosen and why.
5. `## Notes` - other facts worth knowing.
6. `## See also` - links to related room pages, e.g. `[auth/](auth.md)`.

When context is tight, sections are loaded in this order, so put what matters most in Rules and Pitfalls.

## Every claim cites its source
- End each bullet with the id of the memory it comes from: `- Amounts are Decimal, never float [#c-money_is_decimal]`.
- Several sources: `[#a1b2c3d4e5] [#f6e5d4c3b2]`. Don't write claims without a source.

## Consolidate, don't copy
- Merge memories that say the same thing into one bullet citing all of them.
- Keep every precise detail of a source: numbers, names, paths, code. `wiki done` lists any it can't find on the page.
- When sources contradict each other, keep the newer one and say what it replaced.
- Summarize what past chats decided; don't paste chat text. Never write secrets.
- Keep a page under about 60 lines. Shorter is better: it is loaded into context.
"""


def _iso(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "never"


class Wiki:
    def __init__(self, palace: MemoryPalace, now: Callable[[], float] = time.time):
        self.palace = palace
        self._now = now
        self.dir = palace.dir / WIKI_DIR

    # ---------- pages ----------

    @staticmethod
    def page_name(room: str) -> str:
        return ATRIUM_PAGE if room == ATRIUM else room.rstrip("/").replace("/", "__") + ".md"

    def page_path(self, room: str) -> Path:
        return self.dir / self.page_name(room)

    def read_page(self, room: str) -> dict[str, Any] | None:
        path = self.page_path(room)
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        meta: dict[str, Any] = {"room": room, "compiled_at": None, "sources": []}
        match = _META.match(text)
        if match:
            for line in match.group("meta").splitlines():
                key, _, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if key == "compiled_at":
                    meta["compiled_at"] = float(value) if value else None
                elif key == "sources":
                    meta["sources"] = [s for s in value.split(",") if s]
            text = text[match.end():]
        return {"meta": meta, "body": text.strip()}

    def ensure_schema(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / SCHEMA_FILE
        if not path.exists():
            path.write_text(SCHEMA_TEXT, encoding="utf-8")
        return path

    # ---------- raw material and prediction errors ----------

    def material(self, room: str) -> list[dict[str, Any]]:
        """Memories that belong to this room's page: hung in the folder or in files directly in it."""
        return [
            m for m in self.palace.memories.values()
            if m["kind"] not in NOT_KNOWLEDGE and any(self.palace.room_of(a) == room for a in m["anchors"])
        ]

    def rooms(self) -> list[str]:
        rooms = {self.palace.room_of(a) for m in self.palace.memories.values() if m["kind"] not in NOT_KNOWLEDGE for a in m["anchors"]}
        if self.dir.is_dir():
            for path in self.dir.glob("*.md"):
                if path.name not in (SCHEMA_FILE, INDEX_FILE):
                    rooms.add(self._room_from_name(path.name))
        return sorted(rooms)

    @staticmethod
    def _room_from_name(name: str) -> str:
        return ATRIUM if name == ATRIUM_PAGE else name[:-3].replace("__", "/") + "/"

    def delta(self, room: str) -> dict[str, Any]:
        page = self.read_page(room)
        material = self.material(room)
        compiled_at = page["meta"]["compiled_at"] if page else None
        known = set(page["meta"]["sources"]) if page else set()
        current = {m["id"] for m in material}

        new = [m for m in material if m["id"] not in known]
        changed = [m for m in material if m["id"] in known and compiled_at and m["updated_at"] > compiled_at]
        removed = sorted(known - current)
        surprise = (
            sum(SURPRISE.get(m["kind"], 1.0) for m in new)
            + sum(SURPRISE.get(m["kind"], 1.0) + CHANGED_BONUS for m in changed)
            + REMOVED_WEIGHT * len(removed)
        )
        return {"room": room, "page": page is not None, "new": new, "changed": changed,
                "removed": removed, "surprise": surprise, "compiled_at": compiled_at}

    def status(self) -> list[dict[str, Any]]:
        """Every room with a page or with material, most surprising first."""
        rows = [self.delta(room) for room in self.rooms()]
        return sorted(rows, key=lambda d: (-d["surprise"], d["room"]))

    def pending(self) -> list[dict[str, Any]]:
        return [d for d in self.status() if d["surprise"] > 0]

    # ---------- the agent's work order ----------

    def work_order(self, room: str) -> str:
        schema = self.ensure_schema()
        d = self.delta(room)
        page_rel = self.page_path(room).relative_to(self.palace.root).as_posix()
        lines = [
            f"## Compile wiki page for {room or '(atrium)'}",
            f"Page: `{page_rel}` ({'update it' if d['page'] else 'create it'})",
            f"Follow `{schema.relative_to(self.palace.root).as_posix()}`. Write only the body; cite every claim as [#id].",
        ]
        if not d["surprise"]:
            lines.append("Nothing new since the last compilation. No rewrite needed.")
            return "\n".join(lines)

        def entry(m: dict[str, Any]) -> str:
            times = f" (x{m['count']})" if m.get("count", 1) > 1 else ""
            where = ", ".join(a or "(atrium)" for a in m["anchors"])
            if m["kind"] == "chat" and m.get("session_id"):
                return f"- [#{m['id']}] {m['kind']}: {m['text']} @{where}. Read what concerns this room: `loci-guard recall --session {m['session_id'][:8]}`"
            return f"- [#{m['id']}] {m['kind']}{times}: {m['text']} @{where}"

        if d["changed"]:
            lines.append("\nChanged since the page was written (the page didn't prevent these; give them weight):")
            lines += [entry(m) for m in d["changed"]]
        if d["new"]:
            lines.append("\nNew material the page doesn't cover yet:")
            lines += [entry(m) for m in sorted(d["new"], key=lambda m: -SURPRISE.get(m["kind"], 1.0))]
        if d["removed"]:
            lines.append("\nSources that no longer exist; remove what only they supported:")
            lines += [f"- [#{i}]" for i in d["removed"]]
        unchanged = [m for m in self.material(room) if m not in d["new"] and m not in d["changed"]]
        if unchanged and d["page"]:
            lines.append(f"\n{len(unchanged)} other source(s) are already on the page; keep them unless contradicted.")
        lines.append(f"\nWhen the page is written: `loci-guard wiki done {room or '.'}`")
        return "\n".join(lines)

    # ---------- finishing a compilation ----------

    def finish(self, room: str) -> dict[str, Any]:
        """Check the page the agent wrote, then stamp its header and refresh the index."""
        page = self.read_page(room)
        if page is None or not page["body"]:
            return {"ok": False, "errors": [f"No page body at {self.page_path(room)}"], "warnings": []}
        body = page["body"]
        errors, warnings = [], []

        # Imported here so room-entry hooks, which read pages, don't load the shield.
        from loci_guard.shield.patterns import CREDENTIAL_PATTERNS

        for p in CREDENTIAL_PATTERNS:
            if p.compiled.search(body):
                errors.append(f"{p.id}: {p.message}. Remove it; the wiki can be committed.")
        cited = set(CITATION.findall(body))
        dangling = sorted(c for c in cited if c not in self.palace.memories)
        if dangling:
            errors.append("Citations to memories that don't exist: " + ", ".join(f"[#{c}]" for c in dangling))
        errors += self._broken_links(body)
        if errors:
            return {"ok": False, "errors": errors, "warnings": warnings}

        for memory_id, missing in self.dropped_facts(body).items():
            warnings.append(
                f"[#{memory_id}] details missing from the page: {', '.join(missing)}. "
                "Add them, or confirm they are no longer true."
            )
        material_ids = {m["id"] for m in self.material(room)}
        uncited = sorted(material_ids - cited)
        if uncited:
            warnings.append(
                f"{len(uncited)} source(s) not cited (fine if merged or deliberately left out): "
                + ", ".join(f"[#{i}]" for i in uncited[:10])
            )
        header = (
            "<!-- loci-guard:wiki\n"
            f"room: {room}\n"
            f"compiled_at: {self._now()}\n"
            f"sources: {','.join(sorted(material_ids))}\n"
            "-->\n"
        )
        self.page_path(room).write_text(header + body + "\n", encoding="utf-8")
        self.write_index()
        return {"ok": True, "errors": [], "warnings": warnings}

    def dropped_facts(self, body: str) -> dict[str, list[str]]:
        """For each cited source, the precise details (numbers, names, paths, code) missing from the page."""
        page = CITATION.sub("", body).lower()
        dropped = {}
        for memory_id in sorted(set(CITATION.findall(body))):
            memory = self.palace.memories.get(memory_id)
            if memory is None or memory["kind"] in ("chat", "candidate"):
                continue
            missing = sorted(f for f in facts(memory["text"]) if f not in page)
            if missing:
                dropped[memory_id] = missing
        return dropped

    def _broken_links(self, body: str) -> list[str]:
        missing = [t for t in _MD_LINK.findall(body) if "://" not in t and not (self.dir / t).is_file()]
        return [f"Link to a page that doesn't exist: {t}" for t in missing]

    def write_index(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        lines = ["# Project wiki", "", "Compiled by the agent from Loci Guard's memory palace. One page per room.", ""]
        for room in self.rooms():
            page = self.read_page(room)
            if page is None:
                continue
            summary = next(
                (l.strip() for l in page["body"].splitlines() if l.strip() and not l.startswith("#")), ""
            )
            summary = CITATION.sub("", summary).strip()
            if len(summary) > 120:
                summary = summary[:120].rsplit(" ", 1)[0] + "..."
            lines.append(f"- [{room or '(atrium)'}]({self.page_name(room)}) - {summary} _(compiled {_iso(page['meta']['compiled_at'])})_")
        path = self.dir / INDEX_FILE
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    # ---------- lint ----------

    def lint(self) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []

        def add(level: str, room: str, message: str) -> None:
            issues.append({"level": level, "room": room or "(atrium)", "message": message})

        for room in self.rooms():
            page = self.read_page(room)
            if page is None:
                continue
            body = page["body"]
            if page["meta"]["compiled_at"] is None:
                add("error", room, "Page was never stamped: run `loci-guard wiki done` for it")
            for c in sorted(set(CITATION.findall(body))):
                if c not in self.palace.memories:
                    add("error", room, f"Cites [#{c}], which no longer exists")
            for message in self._broken_links(body):
                add("error", room, message)
            for memory_id, missing in self.dropped_facts(body).items():
                add("warning", room, f"Lost details of [#{memory_id}]: {', '.join(missing)}")
            for token in sorted(set(_CODE_SPAN.findall(body))):
                if _PATHISH.fullmatch(token) and not (self.palace.root / token).exists():
                    add("warning", room, f"Mentions `{token}`, which is not in the repo")
            if not self.material(room):
                add("warning", room, "No memories left for this room; delete the page or re-anchor them")
            d = self.delta(room)
            if d["surprise"] > 0:
                add("warning", room, f"Out of date: {len(d['new'])} new, {len(d['changed'])} changed, {len(d['removed'])} removed")
        return issues
