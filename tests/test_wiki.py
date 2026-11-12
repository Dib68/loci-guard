# loci-guard: ignore-file  (fixtures contain fake secrets on purpose)
"""Tests for the compiled wiki: prediction-error deltas, work orders, stamping, lint, injection."""

import time

from click.testing import CliRunner

from loci_guard.brain.astrocyte import Astrocyte
from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.wiki import SCHEMA_FILE, Wiki
from loci_guard.cli import main


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def _page(wiki, room, body):
    wiki.dir.mkdir(parents=True, exist_ok=True)
    wiki.page_path(room).write_text(body, encoding="utf-8")


def test_page_names_round_trip(tmp_path):
    wiki = Wiki(MemoryPalace(tmp_path))
    assert wiki.page_name("loci_guard/shield/") == "loci_guard__shield.md"
    assert wiki.page_name(ATRIUM) == "_atrium.md"
    assert wiki._room_from_name("loci_guard__shield.md") == "loci_guard/shield/"


def test_material_belongs_to_the_files_room_only(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("folder rule", ["billing/"])
    palace.remember("file note", ["billing/invoice.py"])
    palace.remember("deeper room", ["billing/api/"])
    palace.remember("unconfirmed", ["billing/"], kind="candidate")
    texts = {m["text"] for m in Wiki(palace).material("billing/")}
    assert texts == {"folder rule", "file note"}


def test_compile_cycle_driven_by_prediction_errors(tmp_path):
    palace = MemoryPalace(tmp_path)
    note = palace.remember("webhooks retry 3 times", ["billing/"])
    wiki = Wiki(palace)
    assert wiki.delta("billing/")["new"]

    _page(wiki, "billing/", f"# billing/\nPayments.\n\n## Notes\n- Webhooks retry 3 times [#{note['id']}]\n")
    assert wiki.finish("billing/")["ok"]
    assert wiki.pending() == []

    lesson = palace.record_lesson("billing/keys.py", "SEC003", "AWS key")
    _page(wiki, "billing/", wiki.page_path("billing/").read_text() + f"- Never commit keys [#{lesson['id']}]\n")
    assert wiki.finish("billing/")["ok"]
    time.sleep(0.01)
    palace.record_lesson("billing/keys.py", "SEC003", "AWS key")
    d = wiki.delta("billing/")
    assert [m["id"] for m in d["changed"]] == [lesson["id"]]
    assert "didn't prevent" in wiki.work_order("billing/")

    palace.forget(note["id"])
    assert note["id"] in wiki.delta("billing/")["removed"]


def test_surprise_puts_lessons_before_notes(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("a note", ["docs/"])
    palace.record_lesson("billing/x.py", "SEC001", "API key")
    order = [d["room"] for d in Wiki(palace).pending()]
    assert order.index("billing/") < order.index("docs/")


def test_work_order_lists_material_and_creates_schema(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("invoices are immutable", ["billing/"], kind="decision")
    wiki = Wiki(palace)
    order = wiki.work_order("billing/")
    assert f"[#{m['id']}]" in order and "create it" in order
    assert "loci-guard wiki done billing/" in order
    schema = wiki.dir / SCHEMA_FILE
    schema.write_text("my own rules", encoding="utf-8")
    wiki.work_order("billing/")
    assert schema.read_text(encoding="utf-8") == "my own rules"


def test_finish_rejects_secrets_dangling_citations_and_broken_links(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("fact", ["billing/"])
    wiki = Wiki(palace)
    _page(wiki, "billing/", f'- key is AKIAIOSFODNN7EXAMPLE [#{m["id"]}]\n')
    assert not wiki.finish("billing/")["ok"]
    _page(wiki, "billing/", "- something [#doesnotexist]\n")
    assert any("doesnotexist" in e for e in wiki.finish("billing/")["errors"])
    _page(wiki, "billing/", f"- fact [#{m['id']}]\n- see [auth/](auth.md)\n")
    assert any("auth.md" in e for e in wiki.finish("billing/")["errors"])
    assert not wiki.finish("nowhere/")["ok"]


def test_finish_stamps_header_and_index(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("fact", ["billing/"])
    other = palace.remember("other", ["billing/"])
    wiki = Wiki(palace)
    _page(wiki, "billing/", f"# billing/\nPayments live here [#{m['id']}]\n")
    result = wiki.finish("billing/")
    assert result["ok"] and other["id"] in result["warnings"][0]
    page = wiki.read_page("billing/")
    assert set(page["meta"]["sources"]) == {m["id"], other["id"]}
    assert page["body"].startswith("# billing/")
    index = (wiki.dir / "index.md").read_text(encoding="utf-8")
    assert "[billing/](billing.md)" in index and "Payments live here" in index
    wiki.finish("billing/")
    assert wiki.read_page("billing/")["body"].count("loci-guard:wiki") == 0


def test_lint_finds_problems(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("fact", ["billing/"])
    wiki = Wiki(palace)
    _page(wiki, "billing/", f"- fact [#{m['id']}] in `billing/gone.py`\n")
    wiki.finish("billing/")
    palace.forget(m["id"])
    _page(wiki, "legacy/", "- old stuff\n")
    messages = [i["message"] for i in wiki.lint()]
    assert any("no longer exists" in msg for msg in messages)
    assert any("billing/gone.py" in msg for msg in messages)
    assert any("never stamped" in msg for msg in messages)
    assert any("No memories left" in msg for msg in messages)


def test_room_entry_uses_page_instead_of_covered_memories(tmp_path):
    clock = Clock()
    palace = MemoryPalace(tmp_path)
    covered = palace.remember("webhooks retry 3 times", ["billing/"])
    rule = palace.remember("money is Decimal", ["billing/"], kind="constraint")
    wiki = Wiki(palace, now=clock)
    _page(wiki, "billing/", f"# billing/\n- Webhooks retry three times [#{covered['id']}]\n- Decimal only [#{rule['id']}]\n")
    clock.t = max(covered["updated_at"], rule["updated_at"]) + 1  # compiled after its sources
    wiki.finish("billing/")
    fresh = palace.remember("refunds go through the queue", ["billing/"])
    fresh["updated_at"] = clock.t + 1  # and this one arrived after the compilation

    text = Astrocyte(palace).select("s1", "billing/invoice.py")
    assert "(wiki: .loci/wiki/billing.md)" in text and "Webhooks retry three times" in text
    assert "webhooks retry 3 times" not in text
    assert "money is Decimal" in text
    assert "Decimal only" not in text  # its only source, the constraint, was already broadcast
    assert "refunds go through the queue (new since the wiki page)" in text
    assert Astrocyte(palace).select("s1", "billing/invoice.py") is None


def test_cli_wiki_flow(tmp_path):
    runner = CliRunner()
    root = ["--root", str(tmp_path)]
    palace = MemoryPalace(tmp_path)
    m = palace.remember("invoices are immutable", ["billing/"], kind="decision")
    assert "billing/" in runner.invoke(main, [*root, "wiki", "status"]).output
    assert f"[#{m['id']}]" in runner.invoke(main, [*root, "wiki", "compile"]).output
    assert runner.invoke(main, [*root, "wiki", "done", "billing"]).exit_code == 1
    _page(Wiki(palace), "billing/", f"# billing/\n- Invoices are immutable [#{m['id']}]\n")
    assert runner.invoke(main, [*root, "wiki", "done", "billing"]).exit_code == 0
    assert "up to date" in runner.invoke(main, [*root, "wiki", "compile"]).output
    assert "Invoices are immutable" in runner.invoke(main, [*root, "wiki", "show", "billing/"]).output
    assert runner.invoke(main, [*root, "wiki", "lint"]).exit_code == 0
