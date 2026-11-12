"""Tests for the global workspace: competition, wiki bullets, broadcasts, and the dry-run command."""

import json

from click.testing import CliRunner

from loci_guard.brain.astrocyte import Astrocyte
from loci_guard.brain.memory import ProjectMemory
from loci_guard.brain.palace import MemoryPalace
from loci_guard.brain.wiki import Wiki
from loci_guard.brain.workspace import (
    BROADCAST,
    IGNITION,
    MANDATORY_FLOOR,
    Proposal,
    compete,
    render,
    room_broadcast,
    session_broadcast,
    wiki_proposals,
    winners,
)
from loci_guard.cli import main


def _p(key, salience, text=None, mandatory=False, memory_id=None, cites=(), group="Notes"):
    return Proposal(key, "test", group, text or key, salience, mandatory, memory_id, frozenset(cites))


def _status(outcomes):
    return {o.proposal.key: o.status for o in outcomes}


def test_mandatory_items_win_even_beyond_capacity():
    rule = _p("rule", 1.0, "r" * 300, mandatory=True)
    note = _p("note", 9.0, "n" * 50)
    status = _status(compete([note, rule], capacity=100))
    assert status == {"rule": BROADCAST, "note": "over capacity"}
    huge = _p("huge", 1.0, "h" * (MANDATORY_FLOOR + 10), mandatory=True)
    assert _status(compete([huge], capacity=0))["huge"] == "over capacity"


def test_salience_ignition_duplicates_and_seen():
    outcomes = compete(
        [_p("a", 5, "Same text!"), _p("b", 4, "same   TEXT"), _p("c", IGNITION / 2), _p("d", 3)],
        capacity=1000, seen={"d"},
    )
    assert _status(outcomes) == {"a": BROADCAST, "b": "duplicate", "c": "below ignition", "d": "already seen this session"}


def test_higher_salience_wins_scarce_capacity():
    outcomes = compete([_p("low", 2, "x" * 40), _p("high", 8, "y" * 40)], capacity=50)
    assert [p.key for p in winners(outcomes)] == ["high"]


def test_wiki_bullet_covered_by_broadcast_memory_is_dropped():
    rule = _p("mem", 10, "money is Decimal", mandatory=True, memory_id="m1")
    same = _p("wiki:rules:0", 9, "Decimal only", cites={"m1"})
    extra = _p("wiki:rules:1", 9, "Round half up", cites={"m1", "m2"})
    status = _status(compete([same, extra, rule], capacity=1000))
    assert status["wiki:rules:0"] == "covered by a broadcast memory"
    assert status["wiki:rules:1"] == BROADCAST
    later = _status(compete([same, rule], capacity=1000, seen={"mem", "m1"}))
    assert later["wiki:rules:0"] == "covered by a broadcast memory"


def test_wiki_proposals_parse_sections_bullets_and_citations():
    page = {"body": (
        "# billing/\nPayments live here [#a].\n- loose bullet [#b]\n\n"
        "## Pitfalls\n- Never float\n  for money [#c] [#d]\n\n## Rules\n- Decimal only [#e]\n"
    )}
    by_key = {p.key: p for p in wiki_proposals(page, "billing/")}
    assert by_key["wiki:billing/:about"].text == "Payments live here."
    assert by_key["wiki:billing/:notes:0"].text == "loose bullet"
    pitfall = by_key["wiki:billing/:pitfalls:0"]
    assert pitfall.text == "Never float for money" and pitfall.cites == {"c", "d"} and pitfall.group == "Pitfalls"
    assert by_key["wiki:billing/:rules:0"].salience > pitfall.salience > by_key["wiki:billing/:notes:0"].salience


def test_rules_and_pitfalls_beat_notes_under_pressure():
    notes = "\n".join(f"- long note number {i} " + "x" * 80 for i in range(12))
    page = {"body": f"# billing/\nPayments.\n\n## Notes\n{notes}\n\n## Pitfalls\n- Never float [#a]\n\n## Rules\n- Decimal only [#b]\n"}
    text = render("t", winners(compete(wiki_proposals(page, "billing/"), capacity=100)))
    assert "Decimal only" in text and "Never float" in text and "long note number" not in text
    assert "[#" not in text
    assert text.index("Rules (must hold):") < text.index("Pitfalls:")


def test_room_dry_run_changes_nothing(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("money is Decimal", ["billing/"], kind="constraint")
    astro = Astrocyte(palace)
    before = json.dumps(astro.state, sort_keys=True)
    result = room_broadcast(palace, astro, "s1", "billing/invoice.py", dry_run=True)
    assert "money is Decimal" in result.text
    assert json.dumps(astro.state, sort_keys=True) == before
    assert palace.seen_keys("s1") == set()
    assert room_broadcast(palace, astro, "s1", "billing/invoice.py").text
    assert palace.seen_keys("s1")


def test_room_broadcast_reports_why_items_lost(tmp_path):
    palace = MemoryPalace(tmp_path)
    covered = palace.remember("webhooks retry 3 times", ["billing/"])
    wiki = Wiki(palace)
    wiki.dir.mkdir(parents=True)
    wiki.page_path("billing/").write_text(f"# billing/\n- Webhooks retry three times [#{covered['id']}]\n", encoding="utf-8")
    wiki.finish("billing/")
    outcomes = room_broadcast(palace, Astrocyte(palace), "s1", "billing/x.py", dry_run=True).outcomes
    assert {o.proposal.key: o.status for o in outcomes}[covered["id"]] == "in the wiki page"


def test_session_broadcast_puts_rules_first_and_keeps_high_severity(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.add_constraint("no_eval", "Never use eval()", "critical")
    memory.add_decision("Use PostgreSQL", "Relational data")
    MemoryPalace(tmp_path).remember("billing rule", ["billing/"])
    result = session_broadcast(tmp_path, None, None, dry_run=True)
    text = result.text
    assert text.index("Rules (must hold):") < text.index("Decisions:") < text.index("Housekeeping:")
    assert any(o.proposal.mandatory and o.status == BROADCAST for o in result.outcomes)


def test_workspace_command_shows_the_competition(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("money is Decimal", ["billing/"], kind="constraint")
    palace.remember("old trivia", ["billing/"])
    runner = CliRunner()
    output = runner.invoke(main, ["--root", str(tmp_path), "workspace", "billing/invoice.py"]).output
    assert "MANDATORY broadcast" in output and "Broadcast:" in output
    assert runner.invoke(main, ["--root", str(tmp_path), "workspace"]).exit_code == 0
    assert not (tmp_path / ".loci" / "visits.json").exists()
