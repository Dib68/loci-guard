"""Tests for memory versions and lost-detail detection (reconsolidation without losing pieces)."""

from click.testing import CliRunner

from loci_guard.brain.memory import ProjectMemory
from loci_guard.brain.palace import MAX_VERSIONS, MemoryPalace
from loci_guard.brain.text import facts, lost_details
from loci_guard.brain.wiki import Wiki
from loci_guard.cli import main


def test_facts_pick_precise_details():
    found = facts("Retry `charge()` 3 times in billing/client.py with max_retries; see SEC003 and retryPolicy at 2.5%")
    assert {"charge()", "3", "billing/client.py", "max_retries", "sec003", "retrypolicy", "2.5%"} <= found
    assert "times" not in found


def test_lost_details_reports_original_words():
    assert lost_details("Retry 3 times with exponential backoff", "Retry 3 times") == ["backoff", "exponential"]
    assert lost_details("Retry 3 times", "Retry 5 times") == ["3"]
    assert lost_details("Retry 3 times", "Retry 3 times") == []


def test_integration_keeps_the_old_wording_and_names_what_was_dropped(tmp_path):
    palace = MemoryPalace(tmp_path)
    first = palace.remember("Webhook retries happen three times with exponential backoff", ["billing/"])
    palace.remember("Webhook retries happen three times with backoff", ["billing/"])
    assert palace.last_encoding[0] == "integrated" and palace.last_lost == ["exponential"]
    versions = palace.history(first["id"])
    assert [v["text"] for v in versions] == [
        "Webhook retries happen three times with exponential backoff",
        "Webhook retries happen three times with backoff",
    ]
    assert versions[-1]["until"] is None and versions[0]["until"] is not None


def test_updating_by_id_keeps_history_but_identical_text_does_not(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("[HIGH] money: use Decimal", ["billing/"], kind="constraint", memory_id="c-money")
    palace.remember("[HIGH] money: use Decimal", ["billing/"], kind="constraint", memory_id="c-money")
    assert "versions" not in palace.memories["c-money"]
    palace.remember("[HIGH] money: use Decimal with 2 places", ["billing/"], kind="constraint", memory_id="c-money")
    assert len(palace.memories["c-money"]["versions"]) == 1 and palace.last_lost == []


def test_versions_are_capped(tmp_path):
    palace = MemoryPalace(tmp_path)
    for i in range(MAX_VERSIONS + 5):
        palace.remember(f"timeout is {i} seconds", ["api/"], memory_id="t")
    history = palace.history("t")
    assert len(history) == MAX_VERSIONS + 1
    assert history[-1]["text"] == f"timeout is {MAX_VERSIONS + 4} seconds"


def test_brain_keeps_history_for_hand_written_rules_only(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.add_constraint("timeouts", "API calls time out after 30 seconds")
    memory.add_constraint("timeouts", "API calls time out")
    assert memory.last_lost == ["30", "seconds"]
    rule = next(c for c in memory.get_constraints() if c["name"] == "timeouts")
    assert rule["versions"][0]["rule"] == "API calls time out after 30 seconds"
    memory.add_constraint("line_length", "Keep lines under 100", "info", auto_learned=True)
    memory.add_constraint("line_length", "Keep lines under 104", "info", auto_learned=True)
    assert "versions" not in next(c for c in memory.get_constraints() if c["name"] == "line_length")


def test_history_command_shows_every_wording_and_the_diff(tmp_path):
    runner = CliRunner()
    root = ["--root", str(tmp_path)]
    runner.invoke(main, [*root, "constrain", "timeouts", "API calls time out after 30 seconds"])
    out = runner.invoke(main, [*root, "constrain", "timeouts", "API calls time out after 10 seconds"]).output
    assert "the new wording dropped:" in out and "30" in out
    history = runner.invoke(main, [*root, "history", "timeouts"]).output
    assert "v1 (until" in history and "v2 (current): API calls time out after 10 seconds" in history
    assert "dropped: 30" in history and "added:   10" in history
    assert runner.invoke(main, [*root, "history", "nope"]).exit_code != 0


def test_wiki_flags_details_a_page_dropped(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("Webhooks retry 3 times via `retry_policy` in billing/webhooks.py", ["billing/"])
    chat = palace.remember("Chat 2026-11-12: billing work", ["billing/"], kind="chat")
    wiki = Wiki(palace)
    wiki.dir.mkdir(parents=True)
    wiki.page_path("billing/").write_text(
        f"# billing/\n- Webhooks retry a few times [#{m['id']}] [#{chat['id']}]\n", encoding="utf-8"
    )
    result = wiki.finish("billing/")
    assert result["ok"]
    warning = next(w for w in result["warnings"] if "details missing" in w)
    assert "retry_policy" in warning and "billing/webhooks.py" in warning and "3" in warning
    assert "2026" not in warning
    assert any("Lost details" in i["message"] for i in wiki.lint())

    wiki.page_path("billing/").write_text(
        f"# billing/\n- Webhooks retry 3 times [#{m['id']}]\n- Policy: `retry_policy` in `billing/webhooks.py` [#{m['id']}]\n",
        encoding="utf-8",
    )
    assert not any("details missing" in w for w in wiki.finish("billing/")["warnings"])
