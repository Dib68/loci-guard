"""Global workspace: specialised modules propose, one arbiter decides what reaches the agent.

Borrowed from Global Workspace Theory. The brain, the palace, the wiki and the astrocyte
each propose items with a salience. Constraints are mandatory. Everything else competes for
a capacity the astrocyte sets; items below the ignition threshold never reach the stage.
Duplicates are removed across modules, including wiki bullets whose cited sources were
already broadcast. What wins is broadcast as one block, and the result is fed back:
memories are reinforced and co-activated, keys are marked seen, budget is spent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

from loci_guard.brain.astrocyte import MAX_HEAT, Astrocyte
from loci_guard.brain.foresight import Foresight
from loci_guard.brain.memory import ProjectMemory
from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.wiki import CITATION, Wiki

GROUPS = ("About", "Intentions", "Rules", "Pitfalls", "Forecasts", "Decisions", "Notes", "Linked", "Nearby",
          "Project", "Housekeeping")
GROUP_TITLES = {
    "Intentions": "Intentions (do these now; then `loci-guard intend --done ID`)",
    "Rules": "Rules (must hold)",
    "Forecasts": "Forecasts (open predictions about this area, weighted by each persona's track record)",
    "Nearby": "Nearby (areas you've been working in)",
}
FORECAST_SALIENCE = 8.0
KIND_GROUP = {"intention": "Intentions", "constraint": "Rules", "lesson": "Pitfalls", "decision": "Decisions",
              "note": "Notes", "chat": "Notes"}
KIND_SALIENCE = {"intention": 11.0, "constraint": 10.0, "lesson": 7.0, "decision": 5.0, "note": 3.0, "chat": 2.0}
MANDATORY_KINDS = ("constraint", "intention")
SECTION_GROUP = {"rules": "Rules", "pitfalls": "Pitfalls", "decisions": "Decisions", "notes": "Notes", "see also": "Notes"}
SECTION_SALIENCE = {"rules": 9.0, "pitfalls": 8.0, "decisions": 5.0, "notes": 2.5, "see also": 1.0}

IGNITION = 0.5
MANDATORY_FLOOR = 600
SESSION_CAPACITY = 4000
SURPRISE_GAIN = 1.5
LINKED_GAIN = 0.6
NEARBY_GAIN = 0.4
DEPTH_DECAY = 0.3
ITEM_OVERHEAD = 3

BROADCAST = "broadcast"


# NamedTuple rather than dataclass: hooks import this on every file operation, and
# dataclasses (with inspect) costs ~20 ms of startup there.
class Proposal(NamedTuple):
    key: str
    source: str
    group: str
    text: str
    salience: float
    mandatory: bool = False
    memory_id: str | None = None
    cites: frozenset[str] = frozenset()

    @property
    def cost(self) -> int:
        return len(self.text) + ITEM_OVERHEAD


class Outcome(NamedTuple):
    proposal: Proposal
    status: str


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def compete(proposals: list[Proposal], capacity: int, seen: set[str] | frozenset[str] = frozenset()) -> list[Outcome]:
    """Decide every proposal's fate. Mandatory items first, then by salience, within capacity."""
    ordered = sorted(proposals, key=lambda p: (not p.mandatory, -p.salience))
    # Memory proposals use the memory id as key, so `seen` also says what the agent already holds.
    outcomes, texts, broadcast_ids, used = [], set(), set(seen), 0
    for p in ordered:
        norm = _normalize(p.text)
        if p.key in seen:
            status = "already seen this session"
        elif norm in texts:
            status = "duplicate"
        elif p.cites and p.cites <= broadcast_ids:
            status = "covered by a broadcast memory"
        elif not p.mandatory and p.salience < IGNITION:
            status = "below ignition"
        elif used + p.cost > (max(capacity, MANDATORY_FLOOR) if p.mandatory else capacity):
            status = "over capacity"
        else:
            status = BROADCAST
            used += p.cost
            texts.add(norm)
            if p.memory_id:
                broadcast_ids.add(p.memory_id)
        outcomes.append(Outcome(p, status))
    return outcomes


def winners(outcomes: list[Outcome]) -> list[Proposal]:
    return [o.proposal for o in outcomes if o.status == BROADCAST]


def render(title: str, chosen: list[Proposal], footer: str = "") -> str:
    lines = [title]
    for group in GROUPS:
        items = [p for p in chosen if p.group == group]
        if not items:
            continue
        if group == "About":
            lines += [p.text for p in items]
            continue
        lines.append(f"{GROUP_TITLES.get(group, group)}:")
        for p in items:
            suffix = f" #{p.memory_id}" if p.memory_id else ""
            lines.append(f"- {p.text}{suffix}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


# ---------- processors: what each module proposes ----------

def _strip_citations(text: str) -> str:
    return re.sub(r"\s*\[#[A-Za-z0-9_-]+\]", "", text).strip()


def _split_items(lines: list[str]) -> tuple[list[str], list[str]]:
    """Separate prose lines from bullets; a bullet's continuation lines join it."""
    prose, items = [], []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip())
        elif items and line[:1].isspace():
            items[-1] += " " + stripped
        else:
            prose.append(stripped)
    return prose, items


def wiki_proposals(page: dict[str, Any], room: str) -> list[Proposal]:
    """A compiled page proposes its intro and each bullet separately, so bullets compete on their own."""
    intro, *sections = re.split(r"\n(?=## )", page["body"])
    prose, loose = _split_items(intro.splitlines())
    proposals = []
    if prose:
        proposals.append(Proposal(f"wiki:{room}:about", "wiki", "About", _strip_citations(" ".join(prose)), 6.0))
    parsed = [("notes", loose)] if loose else []
    for section in sections:
        heading, *body = section.splitlines()
        section_prose, section_items = _split_items(body)
        parsed.append((heading.lstrip("# ").strip().lower(), section_items + section_prose))
    for name, items in parsed:
        for i, item in enumerate(items):
            proposals.append(Proposal(
                key=f"wiki:{room}:{name}:{i}",
                source="wiki",
                group=SECTION_GROUP.get(name, "Notes"),
                text=_strip_citations(item),
                salience=SECTION_SALIENCE.get(name, 2.0),
                cites=frozenset(CITATION.findall(item)),
            ))
    return proposals


def memory_proposal(m: dict[str, Any], salience: float, source: str, group: str | None = None, suffix: str = "") -> Proposal:
    return Proposal(
        key=m["id"],
        source=source,
        group=group or KIND_GROUP.get(m["kind"], "Notes"),
        text=m["text"] + suffix,
        salience=salience,
        mandatory=m["kind"] in MANDATORY_KINDS,
        memory_id=m["id"],
    )


# ---------- broadcasts ----------

class Broadcast(NamedTuple):
    text: str | None
    outcomes: list[Outcome]
    capacity: int


def room_broadcast(
    palace: MemoryPalace,
    astro: Astrocyte,
    session_id: str,
    file_path: str,
    tool_name: str = "Read",
    dry_run: bool = False,
) -> Broadcast:
    anchor = palace.anchor_for(file_path)
    if anchor is None:
        return Broadcast(None, [], 0)
    if not dry_run:
        astro.stimulate(file_path, "read" if tool_name == "Read" else "write")
    room = palace.room_of(anchor)
    chain = palace.ancestors(anchor)
    depth = {a: i for i, a in enumerate(chain)}

    page = Wiki(palace).read_page(room)
    covered = set(page["meta"]["sources"]) if page else set()
    compiled_at = (page["meta"]["compiled_at"] or 0) if page else 0

    proposals: list[Proposal] = []
    skipped: list[Outcome] = []
    for m in palace.recall(file_path, include_atrium=False):
        if astro.is_dormant(m):
            skipped.append(Outcome(memory_proposal(m, 0, "palace"), "dormant"))
            continue
        # Intentions are to-dos, never wiki material, so "newer than the page" doesn't apply to them.
        newer = bool(page) and m["kind"] != "intention" and (m["id"] not in covered or m["updated_at"] > compiled_at)
        if page and not newer and m["kind"] not in MANDATORY_KINDS:
            skipped.append(Outcome(memory_proposal(m, 0, "palace"), "in the wiki page"))
            continue
        heat = max((astro.calcium(palace.room_of(a)) for a in m["anchors"]), default=0.0)
        nearness = 1 / (1 + DEPTH_DECAY * min((depth.get(a, len(chain)) for a in m["anchors"]), default=len(chain)))
        salience = KIND_SALIENCE.get(m["kind"], 2.0) * astro.strength(m) * (1 + min(heat, MAX_HEAT) / MAX_HEAT) * nearness
        if m.get("via"):
            proposals.append(memory_proposal(m, salience * LINKED_GAIN, "corridor", group="Linked"))
        elif newer:
            proposals.append(memory_proposal(m, salience * SURPRISE_GAIN, "palace", suffix=" (new since the wiki page)"))
        else:
            proposals.append(memory_proposal(m, salience, "palace"))

    for m in astro._nearby(set(chain)):
        proposals.append(memory_proposal(
            m, KIND_SALIENCE.get(m["kind"], 2.0) * astro.strength(m) * NEARBY_GAIN, "astrocyte", group="Nearby",
        ))
    foresight = Foresight(palace)
    for p in foresight.open_for(anchor):
        likely = foresight.weighted(p)
        proposals.append(Proposal(
            f"forecast:{p['id']}", "foresight", "Forecasts",
            f"{likely:.0%} likely ({p['persona']}): {p['claim']}. Check: {p['check']}. "
            f"Once known: `loci-guard foresee resolve {p['id']} yes|no`",
            FORECAST_SALIENCE * likely,
        ))
    if page:
        proposals += wiki_proposals(page, room)

    capacity = astro.allowance(session_id, room)
    outcomes = compete(proposals, capacity, palace.seen_keys(session_id)) + skipped
    chosen = winners(outcomes)
    if not chosen:
        if not dry_run:
            astro.save()
        return Broadcast(None, outcomes, capacity)

    held = sum(1 for o in outcomes if o.status == "over capacity")
    footer = f"[{held} more held back to keep context small: loci-guard recall {room}]" if held else ""
    title = f"Loci Guard workspace for {room or '(atrium)'}"
    if page:
        title += f" (wiki page: {Wiki(palace).page_path(room).relative_to(palace.root).as_posix()})"
    text = render(title + ":", chosen, footer)

    if not dry_run:
        memory_ids = [p.memory_id for p in chosen if p.memory_id]
        palace.mark_seen(session_id, [p.key for p in chosen])
        astro.reinforce(memory_ids)
        astro.co_activate(memory_ids)
        astro.spend(session_id, len(text))
        astro.save()
    return Broadcast(text, outcomes, capacity)


def session_broadcast(root: Path, source: str | None, session_id: str | None, dry_run: bool = False) -> Broadcast:
    """Project-wide context at startup, resume, /clear and after compaction."""
    palace = MemoryPalace(root)
    astro = Astrocyte(palace)
    if session_id and source in ("compact", "clear") and not dry_run:
        # The context was wiped: reopen the rooms and refill the budget.
        palace.reset_visits(session_id)
        astro.reset_session(session_id)

    brain = ProjectMemory(root).data
    proposals: list[Proposal] = []

    scan = brain.get("scan", {})
    langs, stats = scan.get("languages", {}), scan.get("stats", {})
    if langs:
        summary = ", ".join(f"{k} ({v} files)" for k, v in langs.items())
        proposals.append(Proposal("brain:languages", "brain", "Project",
                                  f"{summary}; {stats.get('total_files', '?')} files, {stats.get('total_lines', '?')} lines", 1.5))
    for c in [c for c in brain.get("constraints", []) if c.get("active", True)]:
        severity = c.get("severity", "warning")
        proposals.append(Proposal(
            key=f"brain:{c['name']}", source="brain", group="Project" if severity == "info" else "Rules",
            text=f"[{severity.upper()}] {c['name']}: {c['rule']}",
            salience={"info": 2.5}.get(severity, 6.0), mandatory=severity in ("critical", "high"),
        ))
    for d in brain.get("decisions", [])[-10:]:
        proposals.append(Proposal(f"brain:decision:{d['id']}", "brain", "Decisions",
                                  f"{d['description']} (reason: {d['reason']})", 5.0))

    for m in palace.recall(ATRIUM):
        if m["kind"] in KIND_GROUP and m["kind"] != "chat" and not astro.is_dormant(m):
            proposals.append(memory_proposal(m, KIND_SALIENCE[m["kind"]] * astro.strength(m), "palace"))

    rooms = {a: k for a, k in palace.map().items() if a != ATRIUM}
    if rooms:
        listing = ", ".join(f"{a} ({sum(k.values())})" for a, k in list(rooms.items())[:15])
        proposals.append(Proposal("house:map", "palace", "Housekeeping",
                                  f"Memory palace rooms: {listing}. Their memories appear when you read or edit files there; "
                                  "look ahead with `loci-guard recall <path>`, search past chats with `loci-guard recall --search`.", 1.5))
    pending = Wiki(palace).pending()
    if pending:
        names = ", ".join(d["room"] or "(atrium)" for d in pending[:5])
        proposals.append(Proposal("house:wiki", "wiki", "Housekeeping",
                                  f"{len(pending)} wiki page(s) need compiling ({names}). When this session's work is done, "
                                  "offer to compile them: `loci-guard wiki compile`.", 1.2))
    elsewhere = [m for m in palace.intentions() if ATRIUM not in m["anchors"]]
    if elsewhere:
        places = sorted({a for m in elsewhere for a in m["anchors"]})
        proposals.append(Proposal("house:intentions", "palace", "Housekeeping",
                                  f"{len(elsewhere)} open intention(s) will fire when you enter: {', '.join(places[:8])}. "
                                  "List them with `loci-guard intend --list`.", 1.3))
    unresolved = Foresight(palace).open_predictions()
    if unresolved:
        proposals.append(Proposal("house:foresight", "foresight", "Housekeeping",
                                  f"{len(unresolved)} prediction(s) await an outcome. When one is known, resolve it so the "
                                  "personas get calibrated: `loci-guard foresee open`.", 1.1))
    waiting = len(palace.candidates())
    if waiting:
        proposals.append(Proposal("house:candidates", "archive", "Housekeeping",
                                  f"{waiting} rule(s) stated in past chats await confirmation: `loci-guard recall --candidates`. "
                                  "Ask the user before promoting any.", 1.0))

    outcomes = compete(proposals, SESSION_CAPACITY)
    chosen = winners(outcomes)
    text = render("# Project context (Loci Guard)", chosen) if chosen else None
    return Broadcast(text, outcomes, SESSION_CAPACITY)
