"""Astrocyte: a functional model of glial memory support, wrapped around the memory palace.

It does not simulate biology. It borrows four jobs astrocytes do and applies them to context:

- Calcium: each room integrates activity slowly (reads, writes, blocked writes) and the
  signal decays with a half-life. Activity spreads upward to containing rooms and along
  corridors to linked rooms (calcium waves), pre-heating areas you are likely to need.
- Gliotransmission: each memory has its own stability (half-life). Retrievability decays with
  it, and every recall raises stability in proportion to how far the memory had faded, so
  spaced recalls consolidate and massed ones barely do (the spacing effect). Faded memories
  go dormant: out of the rooms, still in the archive. Constraints and decisions never fade.
- Tagging and capture: a strong event in a room (a blocked write) consolidates the weak
  memories touched there in the previous hour.
- Coupling: memories from different rooms that keep activating together get a corridor.
- Clearance: injections in a short window raise the load and shrink the next allowance, and
  each session has a total budget, so the agent is never flooded with context.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.store import JsonStore

STATE_FILE = "astrocyte.json"

HALF_LIFE_ROOM = 30 * 60
HALF_LIFE_MEMORY = 30 * 24 * 3600
EVENT_WEIGHTS = {"read": 1.0, "write": 2.0, "blocked": 3.0, "recall": 0.5}
UPWARD_DECAY = 0.5
CORRIDOR_DECAY = 0.3
PREHEAT_LEVEL = 1.0
NEARBY_LIMIT = 2
MAX_HEAT = 5.0

SPACING_GAIN = 2.0  # recall at retrievability R multiplies stability by 1 + 2(1 - R)
MAX_STABILITY = 365 * 24 * 3600
MAX_STRENGTH = 3.0
DORMANT_BELOW = 0.25  # retrievability
PROTECTED_KINDS = ("constraint", "decision", "intention")

TAG_WINDOW = 60 * 60  # tags last about an hour, as in synaptic tagging and capture
CAPTURE_GAIN = 2.0
CAPTURE_EVENTS = ("blocked",)

CO_WINDOW = 10 * 60
LINK_AFTER = 3
MAX_PAIRS = 5000

BASE_ALLOWANCE = 1500
LOAD_WINDOW = 5 * 60
LOAD_SCALE = 3000
SESSION_BUDGET = 20_000


class Astrocyte:
    def __init__(self, palace: MemoryPalace, now: Callable[[], float] = time.time):
        self.palace = palace
        self._now = now
        self.path = palace.dir / STATE_FILE
        self._store = JsonStore(
            self.path, {"rooms": {}, "memories": {}, "window": [], "pairs": {}, "sessions": {}, "auto_links": []}
        )
        self.state = self._store.data

    def save(self) -> None:
        self.palace.dir.mkdir(exist_ok=True)
        self.palace._ensure_private_gitignore()
        self._store.save(indent=None)

    # ---------- calcium ----------

    def calcium(self, anchor: str) -> float:
        record = self.state["rooms"].get(anchor)
        if not record:
            return 0.0
        return record["ca"] * 0.5 ** ((self._now() - record["t"]) / HALF_LIFE_ROOM)

    def _raise(self, anchor: str, amount: float) -> None:
        self.state["rooms"][anchor] = {"ca": round(self.calcium(anchor) + amount, 4), "t": self._now()}

    def stimulate(self, path: str | Path, event: str) -> None:
        anchor = self.palace.anchor_for(path)
        if anchor is None:
            return
        amount = EVENT_WEIGHTS[event]
        room = self.palace.room_of(anchor)
        for depth, ancestor in enumerate(self.palace.ancestors(room)):
            if ancestor == ATRIUM:
                break
            self._raise(ancestor, amount * UPWARD_DECAY ** depth)

        targets: set[str] = set()
        for memory in self.palace.memories.values():
            if room not in memory["anchors"]:
                continue
            for linked_id in memory["links"]:
                linked = self.palace.memories.get(linked_id)
                if linked:
                    targets.update(self.palace.room_of(a) for a in linked["anchors"] if a != ATRIUM)
        for target in targets - {room, ATRIUM}:
            self._raise(target, amount * CORRIDOR_DECAY)
        if event in CAPTURE_EVENTS:
            self.capture(room)
        self.save()

    def heat_map(self) -> list[tuple[str, float]]:
        rooms = ((a, self.calcium(a)) for a in self.state["rooms"])
        return sorted(((a, c) for a, c in rooms if c >= 0.01), key=lambda pair: -pair[1])

    # ---------- forgetting curve with spacing (stability and retrievability) ----------

    def _record(self, memory: dict[str, Any]) -> dict[str, Any]:
        record = self.state["memories"].get(memory["id"])
        if record is None:
            return {"S": HALF_LIFE_MEMORY, "t": memory.get("updated_at", self._now()), "recalls": 0}
        if "S" not in record:  # state written before stability existed
            record = {"S": HALF_LIFE_MEMORY, "t": record.get("t", self._now()), "recalls": record.get("recalls", 0)}
        return record

    def stability(self, memory: dict[str, Any]) -> float:
        """The memory's own half-life in seconds; it grows with well-spaced recalls."""
        return self._record(memory)["S"]

    def retrievability(self, memory: dict[str, Any]) -> float:
        """Chance-like 0..1 that the memory is still alive: halves every `stability` seconds."""
        record = self._record(memory)
        return 0.5 ** (max(0.0, self._now() - record["t"]) / record["S"])

    def strength(self, memory: dict[str, Any]) -> float:
        """Retrievability scaled by how stable the memory has become (1 for a fresh memory)."""
        depth = 1 + math.log2(max(self.stability(memory), HALF_LIFE_MEMORY) / HALF_LIFE_MEMORY)
        value = min(self.retrievability(memory) * depth, MAX_STRENGTH)
        return max(value, 1.0) if memory["kind"] in PROTECTED_KINDS else value

    def is_dormant(self, memory: dict[str, Any]) -> bool:
        return memory["kind"] not in PROTECTED_KINDS and self.retrievability(memory) < DORMANT_BELOW

    def is_stale(self, memory: dict[str, Any]) -> bool:
        """Every place it hangs in is gone from the repo."""
        places = [a for a in memory["anchors"] if a != ATRIUM]
        return bool(places) and not any((self.palace.root / a).exists() for a in places)

    def reinforce(self, memory_ids: list[str]) -> None:
        """A recall. Stability grows with how much the memory had faded: spaced recalls teach, massed ones don't."""
        for memory_id in memory_ids:
            memory = self.palace.memories.get(memory_id)
            if memory is None:
                continue
            record = self._record(memory)
            gain = 1 + SPACING_GAIN * (1 - self.retrievability(memory))
            self.state["memories"][memory_id] = {
                **record,
                "S": min(record["S"] * gain, MAX_STABILITY),
                "t": self._now(),
                "recalls": record["recalls"] + 1,
            }

    def wake(self, memory_id: str) -> bool:
        memory = self.palace.memories.get(memory_id)
        if memory is None:
            return False
        record = self._record(memory)
        self.state["memories"][memory_id] = {**record, "S": max(record["S"], HALF_LIFE_MEMORY), "t": self._now()}
        self.save()
        return True

    # ---------- synaptic tagging and capture ----------

    def capture(self, room: str) -> list[str]:
        """A strong event in a room consolidates the weak memories tagged there recently."""
        now = self._now()
        captured = []
        for memory in self.palace.memories.values():
            if memory["kind"] == "candidate" or room not in {self.palace.room_of(a) for a in memory["anchors"]}:
                continue
            record = self._record(memory)
            if now - max(record["t"], memory.get("updated_at", 0)) > TAG_WINDOW:
                continue
            self.state["memories"][memory["id"]] = {
                **record,
                "S": min(record["S"] * CAPTURE_GAIN, MAX_STABILITY),
                "captured": record.get("captured", 0) + 1,
            }
            captured.append(memory["id"])
        self.state["captures"] = self.state.get("captures", 0) + len(captured)
        return captured

    # ---------- coupling ----------

    def co_activate(self, memory_ids: list[str]) -> list[tuple[str, str]]:
        """Count co-activations with recent memories from other rooms; link pairs that keep recurring."""
        now = self._now()
        window = [w for w in self.state["window"] if now - w["t"] <= CO_WINDOW]
        pairs = self.state["pairs"]
        created = []
        for memory_id in memory_ids:
            memory = self.palace.memories.get(memory_id)
            if memory is None:
                continue
            for entry in window:
                other = self.palace.memories.get(entry["id"])
                if other is None or other["id"] == memory_id or set(other["anchors"]) & set(memory["anchors"]):
                    continue
                key = "|".join(sorted((memory_id, other["id"])))
                pairs[key] = pairs.get(key, 0) + 1
                if pairs[key] == LINK_AFTER and self.palace.link(memory_id, other["id"]):
                    created.append((memory_id, other["id"]))
                    self.state["auto_links"].append(key)
        seen_now = set(memory_ids)
        self.state["window"] = [w for w in window if w["id"] not in seen_now] + [
            {"id": i, "t": now} for i in memory_ids
        ]
        if len(pairs) > MAX_PAIRS:
            self.state["pairs"] = {k: v for k, v in pairs.items() if v > 1}
        return created

    # ---------- clearance (context budget) ----------

    def _session(self, session_id: str) -> dict[str, Any]:
        return self.state["sessions"].setdefault(session_id, {"spent": 0, "recent": []})

    def allowance(self, session_id: str, room: str) -> int:
        session = self.state["sessions"].get(session_id, {"spent": 0, "recent": []})
        now = self._now()
        load = sum(chars for t, chars in session["recent"] if now - t <= LOAD_WINDOW)
        heat = min(self.calcium(room), MAX_HEAT)
        allowed = BASE_ALLOWANCE * (1 + heat / (2 * MAX_HEAT)) / (1 + load / LOAD_SCALE)
        return int(max(0, min(allowed, SESSION_BUDGET - session["spent"])))

    def spend(self, session_id: str, chars: int) -> None:
        session = self._session(session_id)
        now = self._now()
        session["spent"] += chars
        session["recent"] = [r for r in session["recent"] if now - r[0] <= LOAD_WINDOW] + [[now, chars]]

    def reset_session(self, session_id: str) -> None:
        if self.state["sessions"].pop(session_id, None) is not None:
            self.save()

    # ---------- the whole cycle for one room entry ----------

    def select(self, session_id: str, file_path: str, tool_name: str = "Read") -> str | None:
        """One room entry: the global workspace decides what is broadcast; this astrocyte sets its capacity."""
        from loci_guard.brain.workspace import room_broadcast  # workspace imports this module

        return room_broadcast(self.palace, self, session_id, file_path, tool_name).text

    def _nearby(self, here: set[str]) -> list[dict[str, Any]]:
        """A few strong memories from the hottest room outside the current path (the calcium wave)."""
        for anchor, level in self.heat_map():
            if level < PREHEAT_LEVEL:
                return []
            if anchor in here:
                continue
            own = [
                m for m in self.palace.memories.values()
                if anchor in m["anchors"] and m["kind"] != "candidate" and not self.is_dormant(m)
            ]
            own.sort(key=lambda m: -self.strength(m))
            return [{**m, "via": "heat"} for m in own[:NEARBY_LIMIT]]
        return []

    # ---------- report ----------

    def report(self) -> dict[str, Any]:
        memories = [m for m in self.palace.memories.values() if m["kind"] != "candidate"]
        return {
            "heat": self.heat_map(),
            "active": [m for m in memories if not self.is_dormant(m)],
            "dormant": [m for m in memories if self.is_dormant(m)],
            "stale": [m for m in memories if self.is_stale(m)],
            "auto_links": list(self.state["auto_links"]),
            "captures": self.state.get("captures", 0),
            "mean_stability_days": (
                sum(self.stability(m) for m in memories) / len(memories) / 86400 if memories else 0.0
            ),
        }
