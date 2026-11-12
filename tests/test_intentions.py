"""Tests for prospective memory: intentions that fire when their place is entered."""

import json

from click.testing import CliRunner

from loci_guard.brain.astrocyte import HALF_LIFE_MEMORY, Astrocyte
from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.wiki import Wiki
from loci_guard.brain.workspace import BROADCAST, room_broadcast, session_broadcast
from loci_guard.cli import main


def test_intention_fires_in_its_room_first_and_is_mandatory(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("money is Decimal", ["billing/"], kind="constraint")
    intent = palace.intend("Update CHANGELOG.md with the billing change", ["billing/"])
    result = room_broadcast(palace, Astrocyte(palace), "s1", "billing/invoice.py", dry_run=True)
    assert result.text.index("Intentions (do these now") < result.text.index("Rules (must hold)")
    outcome = next(o for o in result.outcomes if o.proposal.memory_id == intent["id"])
    assert outcome.proposal.mandatory and outcome.status == BROADCAST
    assert "auth" not in (room_broadcast(palace, Astrocyte(palace), "s1", "auth/login.py", dry_run=True).text or "")


def test_done_intention_stops_firing_and_reopening_works(tmp_path):
    palace = MemoryPalace(tmp_path)
    intent = palace.intend("Update CHANGELOG.md", ["billing/"])
    palace.complete(intent["id"])
    assert palace.recall("billing/x.py") == [] and palace.intentions() == []
    assert len(palace.intentions(open_only=False)) == 1
    assert "billing/" not in palace.map()
    palace.intend("Update CHANGELOG.md", ["billing/"])
    assert [m["id"] for m in palace.intentions()] == [intent["id"]]


def test_intentions_never_fade_and_stay_out_of_the_wiki(tmp_path):
    palace = MemoryPalace(tmp_path)
    intent = palace.intend("Update CHANGELOG.md", ["billing/"])
    astro = Astrocyte(palace, now=lambda: intent["updated_at"] + 10 * HALF_LIFE_MEMORY)
    assert not astro.is_dormant(intent)
    assert Wiki(palace).material("billing/") == []


def test_session_start_fires_atrium_intentions_and_mentions_room_ones(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.intend("Ask the user whether to bump the version", [ATRIUM])
    palace.intend("Update CHANGELOG.md", ["billing/"])
    text = session_broadcast(tmp_path, None, None, dry_run=True).text
    assert "Ask the user whether to bump the version" in text
    assert "1 open intention(s) will fire when you enter: billing/" in text


def test_intend_command_flow(tmp_path):
    runner = CliRunner()
    root = ["--root", str(tmp_path)]
    out = runner.invoke(main, [*root, "intend", "Update CHANGELOG.md", "--when", "billing/"]).output
    assert "will fire on: billing/" in out
    listing = runner.invoke(main, [*root, "intend", "--list"]).output
    intent_id = listing.split()[0].lstrip("#")
    assert runner.invoke(main, [*root, "intend", "--done", intent_id]).exit_code == 0
    assert "No open intentions." in runner.invoke(main, [*root, "intend", "--list"]).output
    assert runner.invoke(main, [*root, "intend", "--done", "nope"]).exit_code != 0
    assert runner.invoke(main, [*root, "intend"]).exit_code != 0


def test_entering_the_room_through_the_hook_shows_the_intention(tmp_path):
    MemoryPalace(tmp_path).intend("Update CHANGELOG.md", ["billing/"])
    payload = {"cwd": str(tmp_path), "session_id": "s1", "tool_name": "Edit",
               "tool_input": {"file_path": str(tmp_path / "billing" / "invoice.py")}}
    out = CliRunner().invoke(main, ["hook", "enter-room"], input=json.dumps(payload)).output
    assert "Update CHANGELOG.md" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
