"""Tests for the shared JSON store: three-way merge, atomic writes, corruption, real concurrency."""

import json
import subprocess
import sys

from loci_guard.brain.foresight import Foresight
from loci_guard.brain.palace import MemoryPalace
from loci_guard.brain.store import JsonStore, rebase


def test_rebase_keeps_both_sides_and_ours_wins_conflicts():
    base = {"a": 1, "b": 2, "gone": 0, "nested": {"x": 1}}
    ours = {"a": 1, "b": 20, "gone": 0, "mine": True, "nested": {"x": 1, "ours": 1}}
    theirs = {"a": 10, "b": 30, "theirs": True, "nested": {"x": 1, "theirs": 1}}
    rebase(base, ours, theirs)
    assert ours == {"a": 10, "b": 20, "mine": True, "theirs": True, "nested": {"x": 1, "ours": 1, "theirs": 1}}


def test_rebase_merges_set_like_lists_and_records_by_id():
    base = {"links": ["a", "b"], "rules": [{"id": "1", "rule": "old"}]}
    ours = {"links": ["a", "c"], "rules": [{"id": "1", "rule": "old"}, {"id": "2", "rule": "mine"}]}
    theirs = {"links": ["a", "b", "d"], "rules": [{"id": "1", "rule": "new"}, {"id": "3", "rule": "theirs"}]}
    rebase(base, ours, theirs)
    assert ours["links"] == ["a", "c", "d"]  # b stays removed, both additions kept
    assert {r["id"]: r["rule"] for r in ours["rules"]} == {"1": "new", "2": "mine", "3": "theirs"}


def test_two_stores_do_not_erase_each_other(tmp_path):
    path = tmp_path / "state.json"
    first, second = JsonStore(path, {"items": {}}), JsonStore(path, {"items": {}})
    first.data["items"]["a"] = 1
    second.data["items"]["b"] = 2
    first.save()
    second.save()
    assert json.loads(path.read_text(encoding="utf-8"))["items"] == {"a": 1, "b": 2}
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob("*.lock"))


def test_corrupt_file_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"items": {"a": 1', encoding="utf-8")
    store = JsonStore(path, {"items": {}})
    assert store.data == {"items": {}}
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == '{"items": {"a": 1'


def test_stale_lock_from_a_crashed_process_is_broken(tmp_path, monkeypatch):
    import loci_guard.brain.store as store_module

    monkeypatch.setattr(store_module, "STALE_LOCK", 0.0)
    path = tmp_path / "state.json"
    (tmp_path / "state.json.lock").write_text("12345")
    store = JsonStore(path, {})
    store.data["a"] = 1
    store.save()
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


WORKER = """
import sys
from loci_guard.brain.palace import MemoryPalace
root, worker = sys.argv[1], sys.argv[2]
names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
         "kilo", "lima", "mike", "november", "oscar"]
for name in names:
    MemoryPalace(root).remember(f"{worker}{name} zz{worker}{name} qq{worker}{name}", [f"{worker}/"])
"""


def test_concurrent_processes_lose_nothing(tmp_path):
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(tmp_path), w]) for w in ("north", "south", "east")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0]
    assert len(MemoryPalace(tmp_path).memories) == 45


def test_same_words_with_different_numbers_stay_separate(tmp_path):
    palace = MemoryPalace(tmp_path)
    a = palace.remember("The API timeout is 30 seconds", ["api/"])
    b = palace.remember("The API timeout is 5 seconds", ["api/"])
    assert a["id"] != b["id"] and palace.last_encoding[0] == "related"
    assert "30" in palace.memories[a["id"]]["text"]


def test_foresight_lessons_are_not_evidence_for_sibling_predictions(tmp_path):
    palace = MemoryPalace(tmp_path)
    made_at = palace.remember("seed", ["billing/"])["updated_at"] - 60
    foresight = Foresight(palace, now=lambda: made_at)
    sid = foresight.begin("Add refunds", ["billing/refunds.py"])["session"]["id"]
    first, second = foresight.record(sid, "PREDICT tester | 0.9 | totals break | ci\nPREDICT user | 0.5 | emails change | inbox")
    foresight.resolve(first["id"], happened=True)
    assert foresight.evidence(second) == []
    palace.record_lesson("billing/refunds.py", "SEC001", "API key")
    assert len(foresight.evidence(second)) == 1
