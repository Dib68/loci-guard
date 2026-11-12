"""Tests for pattern completion (associative recall) and pattern separation (encoding)."""

from click.testing import CliRunner

from loci_guard.brain.associative import AssociativeIndex
from loci_guard.brain.palace import MemoryPalace
from loci_guard.brain.text import similarity, tokens
from loci_guard.cli import main


def _ids(results):
    return [r["id"] for r in results]


def test_tokens_stem_and_drop_stopwords():
    assert tokens("Refunds and the refund queue") == {"refund", "queue"}
    assert tokens("I rimborsi passano sempre dalla coda") >= {"rimbor", "passan", "coda"}
    assert "sempre" not in tokens("sempre")
    assert similarity("rimborso automatico", "rimborsi automatici") == 1.0


def test_partial_cue_completes_through_the_room():
    palace = MemoryPalace_with()
    refund, invoice, login = palace["refund"], palace["invoice"], palace["login"]
    results = AssociativeIndex(palace["p"]).recall("refunds")
    assert _ids(results)[0] == refund["id"] and results[0]["match"] == "matched: refund"
    by_id = {r["id"]: r for r in results}
    assert by_id[invoice["id"]]["match"] == "by association"
    assert login["id"] not in by_id


def test_corridor_carries_activation_to_another_room():
    data = MemoryPalace_with()
    data["p"].link(data["refund"]["id"], data["login"]["id"])
    assert data["login"]["id"] in _ids(AssociativeIndex(data["p"]).recall("refunds"))


def test_path_in_query_seeds_its_room(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("Invoices are immutable after issue", ["billing/"])
    assert _ids(AssociativeIndex(palace).recall("billing/")) == [m["id"]]


def test_unknown_words_activate_nothing(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("Invoices are immutable after issue", ["billing/"])
    assert AssociativeIndex(palace).recall("kubernetes") == []


def MemoryPalace_with():
    import tempfile
    from pathlib import Path

    palace = MemoryPalace(Path(tempfile.mkdtemp()))
    return {
        "p": palace,
        "refund": palace.remember("Refund requests go through the billing queue", ["billing/"]),
        "invoice": palace.remember("Invoices are immutable after issue", ["billing/"]),
        "login": palace.remember("Login tokens expire after fifteen minutes", ["auth/"]),
    }


def test_near_duplicate_integrates_into_the_existing_memory(tmp_path):
    palace = MemoryPalace(tmp_path)
    first = palace.remember("Webhook retries happen three times with backoff", ["billing/"])
    second = palace.remember("Webhook retries happen three times with exponential backoff", ["billing/"])
    assert second["id"] == first["id"] and palace.last_encoding[0] == "integrated"
    assert second["count"] == 2 and "exponential" in second["text"]
    assert len(palace.memories) == 1


def test_similar_but_different_is_kept_apart_and_linked(tmp_path):
    palace = MemoryPalace(tmp_path)
    a = palace.remember("Stripe webhooks retry three times", ["billing/"])
    b = palace.remember("Stripe webhooks retry five times", ["billing/"])
    assert a["id"] != b["id"] and palace.last_encoding == ("related", a["id"], palace.last_encoding[2])
    assert b["id"] in palace.memories[a["id"]]["links"]


def test_separation_respects_rooms_kinds_and_short_texts(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("Webhook retries happen three times with backoff", ["billing/"])
    palace.remember("Webhook retries happen three times with backoff", ["auth/"])
    palace.remember("Webhook retries happen three times with backoff", ["billing/"], kind="decision")
    palace.remember("billing fact", ["billing/"])
    palace.remember("billing fact again", ["billing/"])
    assert len(palace.memories) == 5


def test_cli_search_and_remember_report_encoding(tmp_path):
    runner = CliRunner()
    root = ["--root", str(tmp_path)]
    runner.invoke(main, [*root, "remember", "Refund requests go through the billing queue", "--at", "billing/"])
    runner.invoke(main, [*root, "remember", "Invoices are immutable after issue", "--at", "billing/"])
    out = runner.invoke(main, [*root, "remember", "Refund requests always go through the billing queue", "--at", "billing/"]).output
    assert "merged into an existing memory" in out
    search = runner.invoke(main, [*root, "recall", "--search", "rimborso refunds"]).output
    assert "associative recall" in search and "by association" in search and "matched: refund" in search
