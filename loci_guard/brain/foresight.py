"""Foresight swarm: personas grounded in the palace forecast what a change will cause, and get scored.

Inspired by MiroFish's persona simulations, but built to be measured: every prediction carries
a probability and a way to check it, and resolved predictions score each persona with the
Brier score. Calibrated personas weigh more in the next forecast; poorly calibrated ones less.
Loci Guard never calls a model: the agent plays the personas (ideally one parallel subagent
each, so they can't anchor on each other) and records their answers here.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from loci_guard.brain.associative import AssociativeIndex
from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.store import JsonStore

STATE_FILE = "foresight.json"
MIN_RESOLVED = 3          # predictions a persona needs before its weight leaves 1.0
BASELINE_BRIER = 0.25     # what always answering 0.5 scores
MIN_WEIGHT, MAX_WEIGHT = 0.2, 2.0
MEMORIES_PER_PERSONA = 5
MAX_MAINTAINERS = 4

ARCHETYPES = (
    {"id": "security", "name": "Security reviewer", "lens": "secrets, injection, auth, data exposure, unsafe defaults",
     "bias": "assumes every input is hostile"},
    {"id": "tester", "name": "Test author", "lens": "what breaks, edge cases, missing or weakened tests",
     "bias": "assumes it is broken until a test proves otherwise"},
    {"id": "user", "name": "End user", "lens": "behaviour people will notice: outputs, errors, speed, compatibility",
     "bias": "doesn't read the changelog"},
    {"id": "operator", "name": "On-call operator", "lens": "failures in production, performance, logs, rollback",
     "bias": "thinks about 3 a.m. pages"},
    {"id": "skeptic", "name": "Skeptic", "lens": "why the change may be unnecessary, wrong or premature",
     "bias": "contrarian: looks for the reason not to"},
)
MAINTAINER = {"id": "maintainer", "lens": "this area's rules, decisions and past mistakes",
              "bias": "protective of how this area works today"}

PREDICTION_LINE = re.compile(r"^\s*PREDICT\s+(\S+)\s*\|\s*([0-9]*\.?[0-9]+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$", re.IGNORECASE)


def _id(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:8]


class Foresight:
    def __init__(self, palace: MemoryPalace, now=time.time):
        self.palace = palace
        self._now = now
        self.path = palace.dir / STATE_FILE
        self._store = JsonStore(self.path, {"sessions": {}, "predictions": {}, "scores": {}})
        self.state = self._store.data

    def save(self) -> None:
        self._store.save()

    # ---------- calibration ----------

    def score(self, persona_key: str) -> dict[str, Any]:
        stats = self.state["scores"].get(persona_key, {"n": 0, "brier_sum": 0.0, "hits": 0})
        n = stats["n"]
        brier = stats["brier_sum"] / n if n else None
        if n < MIN_RESOLVED:
            weight = 1.0
        else:
            # 1.0 for a persona as good as a coin flip, up to 2.0 for a perfect one, down to 0.2.
            weight = max(MIN_WEIGHT, min(MAX_WEIGHT, 2 - brier / BASELINE_BRIER))
        return {"n": n, "brier": brier, "weight": round(weight, 2), "calibrated": n >= MIN_RESOLVED}

    # ---------- a forecast ----------

    def personas(self, description: str, rooms: list[str]) -> list[dict[str, Any]]:
        related = AssociativeIndex(self.palace).recall(description, limit=MEMORIES_PER_PERSONA * 2)
        lessons = [m for m in self.palace.memories.values() if m["kind"] == "lesson"
                   and (not rooms or {self.palace.room_of(a) for a in m["anchors"]} & set(rooms))]
        people = []
        for room in rooms[:MAX_MAINTAINERS]:
            own = [m for m in self.palace.recall(room, include_atrium=False)
                   if m["kind"] in ("constraint", "lesson", "decision", "intention")]
            people.append({**MAINTAINER, "persona": f"maintainer:{room}", "name": f"Maintainer of {room}",
                           "memories": own[:MEMORIES_PER_PERSONA]})
        for archetype in ARCHETYPES:
            memories = lessons if archetype["id"] == "security" else related
            people.append({**archetype, "persona": archetype["id"], "memories": memories[:MEMORIES_PER_PERSONA]})
        for person in people:
            person["score"] = self.score(self._score_key(person["persona"]))
        return people

    def begin(self, description: str, places: list[str]) -> dict[str, Any]:
        rooms = sorted({self.palace.room_of(a) for a in (self.palace.anchor_for(p) for p in places) if a is not None} - {ATRIUM})
        session_id = _id(description, str(self._now()))
        people = self.personas(description, rooms)
        self.state["sessions"][session_id] = {
            "id": session_id, "description": description, "rooms": rooms, "created_at": self._now(),
            "personas": {p["persona"]: p["name"] for p in people},
        }
        self.save()
        return {"session": self.state["sessions"][session_id], "people": people}

    def work_order(self, forecast: dict[str, Any]) -> str:
        session, people = forecast["session"], forecast["people"]
        lines = [
            f"## Foresight {session['id']}: {session['description']}",
            f"Rooms: {', '.join(session['rooms']) or '(whole project)'}",
            "",
            "Play each persona below as an independent forecaster, ideally one parallel subagent per persona so they",
            "don't anchor on each other. Each answers only through its lens and memories, and gives 1-3 predictions:",
            "",
            "    PREDICT <persona> | <probability 0-1> | <what will happen> | <how we will know>",
            "",
            "Prefer concrete, checkable outcomes (a test fails, a log contains X, a reviewer asks for Y) over vague risks.",
            f"Then record them: `loci-guard foresee record {session['id']}` with the lines on stdin.",
        ]
        for person in people:
            s = person["score"]
            calibration = (f"weight {s['weight']}, Brier {s['brier']:.2f} over {s['n']}" if s["calibrated"]
                           else f"uncalibrated ({s['n']}/{MIN_RESOLVED} resolved)")
            lines += ["", f"### {person['name']}  (persona `{person['persona']}`, {calibration})",
                      f"Lens: {person['lens']}. Bias: {person['bias']}."]
            if person["memories"]:
                lines.append("Remembers:")
                lines += [f"- [{m['kind']}] {m['text']}" for m in person["memories"]]
        return "\n".join(lines)

    def record(self, session_id: str, text: str) -> list[dict[str, Any]]:
        session = self.state["sessions"].get(session_id)
        if session is None:
            raise ValueError(f"no foresight session {session_id}")
        recorded = []
        for line in text.splitlines():
            match = PREDICTION_LINE.match(line)
            if not match:
                continue
            persona, probability, claim, check = match.groups()
            if persona not in session["personas"]:
                raise ValueError(f"unknown persona '{persona}' for session {session_id}; expected one of {', '.join(session['personas'])}")
            prediction_id = _id(session_id, persona, claim)
            self.state["predictions"][prediction_id] = {
                "id": prediction_id, "session": session_id, "persona": persona,
                "probability": min(1.0, max(0.0, float(probability))), "claim": claim, "check": check,
                "rooms": session["rooms"], "created_at": self._now(), "outcome": None,
            }
            recorded.append(self.state["predictions"][prediction_id])
        self.save()
        return recorded

    def consensus(self, session_id: str) -> list[dict[str, Any]]:
        """The session's predictions with each one's probability weighted by its persona's track record."""
        out = [{**p, "weight": self.score(self._score_key(p["persona"]))["weight"], "weighted": round(self.weighted(p), 3)}
               for p in self.state["predictions"].values() if p["session"] == session_id]
        return sorted(out, key=lambda p: -p["weighted"])

    @staticmethod
    def _score_key(persona: str) -> str:
        return persona.split(":", 1)[0]  # all maintainers share one track record

    # ---------- outcomes ----------

    def open_predictions(self) -> list[dict[str, Any]]:
        return sorted((p for p in self.state["predictions"].values() if p["outcome"] is None), key=lambda p: p["created_at"])

    def evidence(self, prediction: dict[str, Any]) -> list[dict[str, Any]]:
        """Lessons recorded in the prediction's rooms after it was made: hints that it came true."""
        rooms = set(prediction["rooms"])
        return [m for m in self.palace.memories.values()
                # Lessons written by resolving other predictions are not independent evidence.
                if m["kind"] == "lesson" and m.get("source") != "foresight" and m["updated_at"] > prediction["created_at"]
                and (not rooms or {self.palace.room_of(a) for a in m["anchors"]} & rooms)]

    def resolve(self, prediction_id: str, happened: bool | None) -> dict[str, Any]:
        """`happened=None` voids a prediction that can no longer be checked; it scores nobody."""
        prediction = self.state["predictions"].get(prediction_id)
        if prediction is None:
            raise ValueError(f"no prediction {prediction_id}")
        if prediction["outcome"] is not None:
            raise ValueError(f"prediction {prediction_id} is already resolved")
        if happened is None:
            prediction.update(outcome="void", resolved_at=self._now())
            self.save()
            return prediction
        outcome = 1.0 if happened else 0.0
        prediction.update(outcome=outcome, resolved_at=self._now())
        stats = self.state["scores"].setdefault(self._score_key(prediction["persona"]), {"n": 0, "brier_sum": 0.0, "hits": 0})
        stats["n"] += 1
        stats["brier_sum"] += (prediction["probability"] - outcome) ** 2
        stats["hits"] += int((prediction["probability"] >= 0.5) == happened)
        self.save()
        if happened:
            # A foreseen outcome is knowledge about the area: keep it where it applies.
            self.palace.remember(
                f"Foreseen and it happened: {prediction['claim']} (checked by: {prediction['check']})",
                prediction["rooms"] or [ATRIUM], kind="lesson", source="foresight",
            )
        return prediction

    def scores(self) -> dict[str, dict[str, Any]]:
        keys = [a["id"] for a in ARCHETYPES] + ["maintainer"]
        return {key: self.score(key) for key in keys}

    def open_for(self, anchor: str) -> list[dict[str, Any]]:
        """Open predictions about the area being entered (project-wide ones surface at session start)."""
        chain = set(self.palace.ancestors(self.palace.room_of(anchor)))
        return [p for p in self.open_predictions() if p["rooms"] and set(p["rooms"]) & chain]

    def weighted(self, prediction: dict[str, Any]) -> float:
        return min(1.0, prediction["probability"] * self.score(self._score_key(prediction["persona"]))["weight"])


def parse_outcome(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered in ("yes", "y", "true", "1", "si", "sì"):
        return True
    if lowered in ("no", "n", "false", "0"):
        return False
    if lowered == "void":
        return None
    raise ValueError(f"outcome must be yes, no or void, not {value!r}")
