"""What reaches the agent's context should be worth its tokens, and no memory should get lost on a rename."""

import json

from click.testing import CliRunner

from loci_guard.brain.archive import parse_transcript, rule_candidates
from loci_guard.brain.astrocyte import Astrocyte
from loci_guard.brain.palace import MemoryPalace
from loci_guard.brain.workspace import BROADCAST, Proposal, compete, room_broadcast, session_broadcast
from loci_guard.cli import main


def _p(key, text, salience=5.0, group="Pitfalls", mandatory=False):
    return Proposal(key, "test", group, text, salience, mandatory)


def test_a_restatement_is_dropped_but_a_new_detail_is_kept():
    first = _p("a", "Two processes saving palace.json at once lose memories they both added")
    again = _p("b", "Processes saving palace.json at once lose memories added by both", 4.0)
    detail = _p("c", "Processes saving palace.json at once lose 94% of memories they both added", 4.0)
    rule = _p("d", "Processes saving palace.json at once lose memories they both added", 3.0, "Rules", mandatory=True)
    status = {o.proposal.key: o.status for o in compete([first, again, detail, rule], capacity=10_000)}
    assert status["a"] == BROADCAST and status["b"] == "restates a broadcast item"
    assert status["c"] == BROADCAST  # 94% is a fact the first item lacks
    assert status["d"] == BROADCAST  # rules are never dropped as restatements


def test_room_broadcast_leaves_out_chats_and_old_check_clauses(tmp_path):
    (tmp_path / "billing").mkdir()
    palace = MemoryPalace(tmp_path)
    palace.remember("Chat 2026-01-01: refactor (full text: loci-guard recall --session abc)", ["billing/"], kind="chat")
    palace.remember("Foreseen and it happened: webhooks retried twice (checked by: replay the queue)",
                    ["billing/"], kind="lesson", source="foresight")
    text = room_broadcast(palace, Astrocyte(palace), "s1", "billing/x.py", dry_run=True).text
    assert "webhooks retried twice" in text
    assert "Chat 2026" not in text and "checked by" not in text


def test_gist_keeps_the_opening_and_every_dropped_detail():
    from loci_guard.brain.workspace import GIST_CHARS, SHORTENED, gist

    short = "Money is Decimal."
    assert gist(short) == short
    long = ("Two processes saving the store at the same moment lose updates, because each one rewrites the whole "
            "file it loaded earlier; in tests 94% of entries vanished from `palace.json` under MAX_WRITERS=8")
    shortened = gist(long)
    assert len(shortened) < len(long) and SHORTENED in shortened
    assert shortened.startswith("Two processes saving the store at the same moment lose updates")
    for detail in ("94", "palace.json", "MAX_WRITERS"):
        assert detail in shortened
    assert len(shortened.split(SHORTENED)[0]) <= GIST_CHARS


def test_an_episode_travels_as_one_item_and_counts_as_delivered(tmp_path):
    (tmp_path / "billing").mkdir()
    palace = MemoryPalace(tmp_path)
    ids = [palace.remember(text, ["billing/"], kind="lesson", source="foresight")["id"] for text in (
        "Concurrent saves lose refunds written by the other worker",
        "A reader mid-write sees truncated JSON and wipes the ledger",
        "Retry storms double-charge customers when the lock times out",
    )]
    rule = palace.remember("Refunds go through billing/queue.py", ["billing/"], kind="constraint")
    astro = Astrocyte(palace)
    text = room_broadcast(palace, astro, "s1", "billing/x.py").text
    assert "(+2 from the same episode:" in text and rule["text"] in text
    assert sum(text.count(f"#{i}") for i in ids) == 3  # the lead once, the other two by reference
    assert set(ids) <= palace.seen_keys("s1")
    assert all("Concurrent saves" not in line for line in text.splitlines()[1:] if "Retry storms" in line)
    full = CliRunner().invoke(main, ["--root", str(tmp_path), "recall", "--id", ids[1]]).output
    assert "wipes the ledger" in full


def test_the_shortened_note_appears_only_when_something_was_shortened(tmp_path):
    from loci_guard.brain.workspace import SHORTENED_FOOTER

    (tmp_path / "api").mkdir()
    palace = MemoryPalace(tmp_path)
    palace.remember('Keys may be quoted: `"api_key": "..."` must still match', ["api/"])
    text = room_broadcast(palace, Astrocyte(palace), "s1", "api/x.py", dry_run=True).text
    assert '"..."' in text and SHORTENED_FOOTER not in text
    palace.remember("A very long note about retries " + "and backoff " * 20, ["api/"])
    text = room_broadcast(palace, Astrocyte(palace), "s2", "api/x.py", dry_run=True).text
    assert SHORTENED_FOOTER in text


def test_session_housekeeping_is_short(tmp_path):
    palace = MemoryPalace(tmp_path)
    for i in range(10):
        (tmp_path / f"room{i}").mkdir()
        palace.remember(f"note number {i} about room {i}", [f"room{i}/"])
    text = session_broadcast(tmp_path, "startup", None, dry_run=True).text
    housekeeping = text.split("Housekeeping:", 1)[1]
    assert "+4 more" in housekeeping and len(housekeeping) < 300


def test_renamed_folder_is_reported_and_relocated(tmp_path):
    (tmp_path / "new_pkg" / "brain").mkdir(parents=True)
    (tmp_path / "new_pkg" / "store.py").write_text("x = 1\n")
    palace = MemoryPalace(tmp_path)
    kept = palace.remember("saves must hold the lock", ["old_pkg/brain/"], kind="constraint")
    palace.remember("store.py writes atomically", ["old_pkg/store.py", "docs/"])
    assert palace.orphans() == ["docs/", "old_pkg/"]
    assert palace.suggest_home("old_pkg/") == "new_pkg/"
    assert "`loci-guard relocate old_pkg/ new_pkg/`" in session_broadcast(tmp_path, "startup", None, dry_run=True).text

    runner = CliRunner()
    out = runner.invoke(main, ["--root", str(tmp_path), "relocate", "old_pkg/", "new_pkg/"]).output
    assert "Moved 2 memories" in out
    moved = MemoryPalace(tmp_path)
    assert moved.memories[kept["id"]]["anchors"] == ["new_pkg/brain/"]
    assert moved.orphans() == ["docs/"]
    assert runner.invoke(main, ["--root", str(tmp_path), "relocate", "old_pkg/", "new.py"]).exit_code != 0


def test_text_the_harness_injects_never_becomes_a_rule(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s", "message": {"content": "From now on always use Decimal for money."}},
        {"type": "user", "sessionId": "s", "isMeta": True, "message": {"content": "Don't mention this to the user."}},
        {"type": "user", "sessionId": "s", "isCompactSummary": True,
         "message": {"content": "Summary: never touch the tests."}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in lines), encoding="utf-8")
    assert rule_candidates(parse_transcript(path)["turns"]) == ["From now on always use Decimal for money."]
