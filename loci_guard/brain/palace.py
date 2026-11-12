"""Memory palace: memories anchored to places in the repo, recalled when the agent enters them.

The castle is the repository. The atrium ("") holds project-wide memories, every folder is
a room, every file a smaller room inside it. A memory can hang in several rooms and be
linked to other memories (corridors). Entering a room recalls what hangs there and in the
rooms that contain it, nearest first, so only the relevant part of the castle is loaded.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path, PurePosixPath
from typing import Any

from loci_guard.brain.store import JsonStore
from loci_guard.brain.text import facts, lost_details, similarity, tokens

PALACE_FILE = "palace.json"
VISITS_FILE = "visits.json"
ATRIUM = ""
KINDS = ("constraint", "decision", "lesson", "note", "chat", "candidate", "intention")
RECURRING_THRESHOLD = 3
MAX_TRACKED_SESSIONS = 50
SEPARABLE_KINDS = ("note", "decision", "constraint")
INTEGRATE_AT = 0.8
RELATE_AT = 0.5
MIN_TOKENS_TO_COMPARE = 3
MAX_VERSIONS = 20
PRIVATE_FILES = ("archive/", "visits.json", "astrocyte.json")


def _now() -> float:
    return time.time()


def _make_id(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:10]


class MemoryPalace:
    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        self.dir = self.root / ".loci"
        self.path = self.dir / PALACE_FILE
        self._store = JsonStore(self.path, {"version": 1, "memories": {}, "sessions": {}})
        self._data = self._store.data
        # How the last remember() was encoded: ("new" | "related" | "integrated", other id, similarity).
        self.last_encoding: tuple[str, str | None, float] = ("new", None, 0.0)
        # Details the last remember() dropped when it replaced an older wording.
        self.last_lost: list[str] = []

    # ---------- storage ----------

    def save(self) -> None:
        self.dir.mkdir(exist_ok=True)
        self._ensure_private_gitignore()
        self._store.save()

    def _ensure_private_gitignore(self) -> None:
        # Chat archives, visit and astrocyte state are personal; the palace map itself is shared.
        ignore = self.dir / ".gitignore"
        lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
        missing = [entry for entry in PRIVATE_FILES if entry not in lines]
        if missing:
            ignore.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")

    @property
    def memories(self) -> dict[str, dict[str, Any]]:
        return self._data["memories"]

    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        return self._data["sessions"]

    # ---------- places ----------

    def anchor_for(self, path: str | Path | None) -> str | None:
        """Turn a path into a room anchor: 'billing/' for folders, 'billing/x.py' for files."""
        if path is None or str(path) in ("", ".", "/"):
            return ATRIUM
        raw = Path(path)
        full = raw if raw.is_absolute() else self.root / raw
        try:
            rel = full.resolve().relative_to(self.root)
        except ValueError:
            return None
        posix = rel.as_posix()
        if posix in ("", "."):
            return ATRIUM
        is_dir = full.is_dir() or str(path).endswith(("/", "\\"))
        return posix.rstrip("/") + "/" if is_dir else posix

    @staticmethod
    def ancestors(anchor: str) -> list[str]:
        """The room and every room containing it, nearest first, ending at the atrium."""
        if anchor == ATRIUM:
            return [ATRIUM]
        chain = [anchor]
        parent = PurePosixPath(anchor.rstrip("/")).parent
        while str(parent) not in (".", ""):
            chain.append(f"{parent.as_posix()}/")
            parent = parent.parent
        chain.append(ATRIUM)
        return chain

    @staticmethod
    def room_of(anchor: str) -> str:
        """The folder a file lives in (a folder anchor is its own room)."""
        if anchor == ATRIUM or anchor.endswith("/"):
            return anchor
        parent = PurePosixPath(anchor).parent
        return ATRIUM if str(parent) == "." else f"{parent.as_posix()}/"

    def common_room(self, anchors: list[str]) -> str:
        rooms = {self.room_of(a) for a in anchors if a is not None}
        if not rooms:
            return ATRIUM
        if len(rooms) == 1:
            return rooms.pop()
        common = os.path.commonpath([r.rstrip("/") or "." for r in rooms])
        return ATRIUM if common in ("", ".") else PurePosixPath(common).as_posix() + "/"

    # ---------- memories ----------

    def remember(
        self,
        text: str,
        anchors: list[str] | None = None,
        kind: str = "note",
        source: str = "user",
        memory_id: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        anchors = sorted({a for a in (anchors or [ATRIUM]) if a is not None}) or [ATRIUM]
        self.last_encoding = ("new", None, 0.0)
        self.last_lost = []
        similar, score = (None, 0.0)
        if memory_id is None and kind in SEPARABLE_KINDS:
            similar, score = self._most_similar(text, kind, anchors)
            # Same words but different numbers, names or paths ("timeout 30 s" / "timeout 5 s")
            # are different memories: never merge those, only link them.
            if similar is not None and score >= INTEGRATE_AT and facts(text) == facts(similar["text"]):
                # Pattern integration: the same memory said again; the newer wording wins,
                # and the old one goes into the memory's history instead of being lost.
                self.last_lost = self._revise(similar, text)
                similar.update(
                    anchors=sorted(set(similar["anchors"]) | set(anchors)),
                    count=similar.get("count", 1) + 1, updated_at=_now(),
                )
                self.save()
                self.last_encoding = ("integrated", similar["id"], score)
                return similar
        memory_id = memory_id or _make_id(kind, text, *anchors)
        existing = self.memories.get(memory_id)
        if existing:
            self.last_lost = self._revise(existing, text)
            existing.update(kind=kind, anchors=anchors, updated_at=_now(), **extra)
        else:
            self.memories[memory_id] = {
                "id": memory_id,
                "kind": kind,
                "text": text,
                "anchors": anchors,
                "links": [],
                "count": 1,
                "source": source,
                "created_at": _now(),
                "updated_at": _now(),
                **extra,
            }
            if similar is not None and score >= RELATE_AT:
                # Pattern separation: kept apart so they don't blur, but linked as related.
                self.memories[memory_id]["links"].append(similar["id"])
                similar["links"].append(memory_id)
                self.last_encoding = ("related", similar["id"], score)
        self.save()
        return self.memories[memory_id]

    @staticmethod
    def _revise(memory: dict[str, Any], new_text: str) -> list[str]:
        """Replace a memory's text, keeping the old wording in its history. Returns what was dropped."""
        old = memory["text"]
        if new_text == old:
            return []
        versions = memory.setdefault("versions", [])
        versions.append({"text": old, "since": memory.get("updated_at"), "until": _now()})
        del versions[:-MAX_VERSIONS]
        memory["text"] = new_text
        return lost_details(old, new_text)

    def history(self, memory_id: str) -> list[dict[str, Any]] | None:
        """Every wording the memory has had, oldest first, ending with the current one."""
        memory = self.memories.get(memory_id)
        if memory is None:
            return None
        return memory.get("versions", []) + [{"text": memory["text"], "since": memory.get("updated_at"), "until": None}]

    def _most_similar(self, text: str, kind: str, anchors: list[str]) -> tuple[dict | None, float]:
        """The closest memory of the same kind in the same rooms, if the text is long enough to judge."""
        if len(tokens(text)) < MIN_TOKENS_TO_COMPARE:
            return None, 0.0
        rooms = {self.room_of(a) for a in anchors}
        best, best_score = None, 0.0
        for m in self.memories.values():
            if m["kind"] != kind or not rooms & {self.room_of(a) for a in m["anchors"]}:
                continue
            if len(tokens(m["text"])) < MIN_TOKENS_TO_COMPARE:
                continue
            score = similarity(text, m["text"])
            if score > best_score:
                best, best_score = m, score
        return best, best_score

    def forget(self, memory_id: str) -> bool:
        if memory_id not in self.memories:
            return False
        del self.memories[memory_id]
        for m in self.memories.values():
            if memory_id in m["links"]:
                m["links"].remove(memory_id)
        self.save()
        return True

    def link(self, a: str, b: str) -> bool:
        if a == b or a not in self.memories or b not in self.memories:
            return False
        for x, y in ((a, b), (b, a)):
            if y not in self.memories[x]["links"]:
                self.memories[x]["links"].append(y)
        self.save()
        return True

    def promote(self, memory_id: str, kind: str = "constraint") -> dict[str, Any] | None:
        memory = self.memories.get(memory_id)
        if memory is None:
            return None
        memory.update(kind=kind, source="user", updated_at=_now())
        self.save()
        return memory

    def record_lesson(self, file_path: str, rule_id: str, message: str) -> dict[str, Any] | None:
        """Hang a blocked mistake in the room where it happened; repeats become constraints."""
        anchor = self.anchor_for(file_path)
        if anchor is None:
            return None
        room = self.room_of(anchor)
        memory_id = _make_id("lesson", rule_id, room)
        memory = self.memories.get(memory_id)
        if memory is None:
            return self.remember(
                f"{rule_id}: {message}. It was blocked here before; don't repeat it.",
                anchors=[room], kind="lesson", source="shield", memory_id=memory_id, rule_id=rule_id,
            )
        memory["count"] += 1
        memory["updated_at"] = _now()
        if memory["count"] >= RECURRING_THRESHOLD and memory["kind"] == "lesson":
            memory["kind"] = "constraint"
            memory["text"] = (
                f"{rule_id}: {message}. Recurring mistake in this area "
                f"(blocked {memory['count']} times). Check for it before every write here."
            )
        self.save()
        return memory

    # ---------- recall ----------

    def recall(self, path: str | Path | None, include_atrium: bool = True, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """Memories visible from a place: its own room first, then the rooms containing it, then corridors."""
        anchor = self.anchor_for(path)
        if anchor is None:
            return []
        chain = self.ancestors(anchor)
        if not include_atrium:
            chain = [a for a in chain if a != ATRIUM]
        depth = {a: i for i, a in enumerate(chain)}
        kinds = kinds or tuple(k for k in KINDS if k != "candidate")

        found: dict[str, tuple[int, dict]] = {}
        for m in self.memories.values():
            if m["kind"] not in kinds or (m["kind"] == "intention" and m.get("status") != "open"):
                continue
            hits = [depth[a] for a in m["anchors"] if a in depth]
            if hits:
                found[m["id"]] = (min(hits), m)

        corridor_rank = len(chain)
        for _, m in list(found.values()):
            for linked_id in m["links"]:
                linked = self.memories.get(linked_id)
                if linked and linked_id not in found and linked["kind"] in kinds:
                    found[linked_id] = (corridor_rank, {**linked, "via": m["id"]})

        kind_rank = {"intention": -1, "constraint": 0, "lesson": 1, "decision": 2, "note": 3, "chat": 4, "candidate": 5}
        ordered = sorted(
            found.values(),
            key=lambda pair: (pair[0], kind_rank.get(pair[1]["kind"], 9), -pair[1].get("count", 1), -pair[1]["updated_at"]),
        )
        return [m for _, m in ordered]

    # ---------- prospective memory: "when I'm in X, do Y" ----------

    def intend(self, text: str, anchors: list[str]) -> dict[str, Any]:
        """An event-based intention: it fires whenever its place is entered, until marked done."""
        return self.remember(text, anchors, kind="intention", status="open",
                             memory_id=_make_id("intention", text, *sorted(anchors)))

    def complete(self, memory_id: str) -> dict[str, Any] | None:
        memory = self.memories.get(memory_id)
        if memory is None or memory["kind"] != "intention":
            return None
        memory.update(status="done", done_at=_now(), updated_at=_now())
        self.save()
        return memory

    def intentions(self, open_only: bool = True) -> list[dict[str, Any]]:
        return sorted(
            (m for m in self.memories.values()
             if m["kind"] == "intention" and (not open_only or m.get("status") == "open")),
            key=lambda m: m["created_at"],
        )

    def candidates(self) -> list[dict[str, Any]]:
        return sorted(
            (m for m in self.memories.values() if m["kind"] == "candidate"),
            key=lambda m: -m["updated_at"],
        )

    def map(self) -> dict[str, dict[str, int]]:
        """Room -> {kind: count}, for drawing the castle."""
        rooms: dict[str, dict[str, int]] = {}
        for m in self.memories.values():
            if m["kind"] == "intention" and m.get("status") != "open":
                continue
            for a in m["anchors"]:
                room = rooms.setdefault(a, {})
                room[m["kind"]] = room.get(m["kind"], 0) + 1
        return dict(sorted(rooms.items()))

    # ---------- per-session visits (what was already whispered this session) ----------

    def _visits_path(self) -> Path:
        return self.dir / VISITS_FILE

    def _visits(self) -> JsonStore:
        return JsonStore(self._visits_path(), {})

    def seen_keys(self, session_id: str) -> set[str]:
        return set(self._visits().data.get(session_id, []))

    def unseen(self, session_id: str, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = self.seen_keys(session_id)
        return [m for m in memories if m["id"] not in seen]

    def mark_seen(self, session_id: str, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        store = self._visits()
        seen = store.data.setdefault(session_id, [])
        seen.extend(i for i in memory_ids if i not in seen)
        for old in list(store.data)[:-MAX_TRACKED_SESSIONS]:
            del store.data[old]
        self.dir.mkdir(exist_ok=True)
        self._ensure_private_gitignore()
        store.save(indent=None)

    def reset_visits(self, session_id: str) -> None:
        store = self._visits()
        if store.data.pop(session_id, None) is not None:
            store.save(indent=None)


def format_memories(memories: list[dict[str, Any]], max_chars: int = 2000) -> str:
    lines, used = [], 0
    for m in memories:
        where = ", ".join(a or "(atrium)" for a in m["anchors"])
        times = f" x{m['count']}" if m.get("count", 1) > 1 else ""
        if m.get("versions"):
            times += f" rev{len(m['versions']) + 1}"
        via = ""
        if m.get("via") == "heat":
            via = " (from a nearby area you've been working in)"
        elif m.get("via"):
            via = f" (linked from {m['via']})"
        line = f"- [{m['kind'].upper()}{times}] {m['text']}  @{where} #{m['id']}{via}"
        if used + len(line) > max_chars:
            lines.append(f"- ... {len(memories) - len(lines)} more: run `loci-guard recall <path>`")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
