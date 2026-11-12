"""Tests for the astrocyte: calcium, forgetting, coupling, and context clearance."""

import json
import time

import pytest
from click.testing import CliRunner

from loci_guard.brain.astrocyte import (
    CAPTURE_GAIN,
    CORRIDOR_DECAY,
    HALF_LIFE_MEMORY,
    HALF_LIFE_ROOM,
    LINK_AFTER,
    MAX_STABILITY,
    MAX_STRENGTH,
    PREHEAT_LEVEL,
    SESSION_BUDGET,
    TAG_WINDOW,
    Astrocyte,
)
from loci_guard.brain.palace import MemoryPalace
from loci_guard.cli import main


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    palace = MemoryPalace(tmp_path)
    return palace, Astrocyte(palace, now=clock), clock


def test_calcium_spreads_upward_and_decays(setup):
    palace, astro, clock = setup
    astro.stimulate("billing/api/charge.py", "read")
    assert astro.calcium("billing/api/") == pytest.approx(1.0)
    assert astro.calcium("billing/") == pytest.approx(0.5)
    clock.advance(HALF_LIFE_ROOM)
    assert astro.calcium("billing/api/") == pytest.approx(0.5)


def test_calcium_wave_follows_corridors(setup):
    palace, astro, _ = setup
    a = palace.remember("refunds need a token", ["billing/"])
    b = palace.remember("tokens are signed in auth/jwt.py", ["auth/"])
    palace.link(a["id"], b["id"])
    astro.stimulate("billing/refund.py", "write")
    assert astro.calcium("auth/") == pytest.approx(2.0 * CORRIDOR_DECAY)


def test_unused_memories_fade_but_rules_never_do(setup):
    palace, astro, clock = setup
    note = palace.remember("webhook retries are 3", ["billing/"])
    rule = palace.remember("money is Decimal", ["billing/"], kind="constraint")
    decision = palace.remember("Postgres", ["billing/"], kind="decision")
    clock.advance(2.1 * HALF_LIFE_MEMORY)
    assert astro.is_dormant(note)
    assert not astro.is_dormant(rule)
    assert not astro.is_dormant(decision)
    assert astro.wake(note["id"])
    assert not astro.is_dormant(note)


def test_massed_recalls_teach_nothing_spaced_recalls_consolidate(setup):
    palace, astro, clock = setup
    massed = palace.remember("webhook retries are 3", ["billing/"])
    spaced = palace.remember("refunds go through the queue", ["billing/"])
    for _ in range(50):
        astro.reinforce([massed["id"]])
    assert astro.stability(massed) == pytest.approx(HALF_LIFE_MEMORY)
    assert astro.strength(massed) == pytest.approx(1.0)

    for _ in range(2):
        clock.advance(astro.stability(spaced))  # recall when it has half faded
        astro.reinforce([spaced["id"]])
    assert astro.stability(spaced) == pytest.approx(4 * HALF_LIFE_MEMORY)
    assert astro.strength(spaced) == pytest.approx(MAX_STRENGTH)


def test_stability_is_capped(setup):
    palace, astro, clock = setup
    note = palace.remember("webhook retries are 3", ["billing/"])
    for _ in range(20):
        clock.advance(astro.stability(note) * 3)
        astro.reinforce([note["id"]])
    assert astro.stability(note) == pytest.approx(MAX_STABILITY)


def test_blocked_write_captures_recent_weak_memories_in_its_room(setup):
    palace, astro, clock = setup
    old = palace.remember("an old note", ["billing/"])
    clock.advance(2 * TAG_WINDOW)
    recent = palace.remember("a fresh note", ["billing/"])
    elsewhere = palace.remember("an auth note", ["auth/"])
    for m in (recent, elsewhere):
        m["updated_at"] = clock.t
    astro.stimulate("billing/keys.py", "blocked")
    assert astro.stability(recent) == pytest.approx(CAPTURE_GAIN * HALF_LIFE_MEMORY)
    assert astro.stability(old) == pytest.approx(HALF_LIFE_MEMORY)
    assert astro.stability(elsewhere) == pytest.approx(HALF_LIFE_MEMORY)
    assert astro.report()["captures"] == 1
    astro.stimulate("billing/keys.py", "read")
    assert astro.report()["captures"] == 1


def test_old_state_files_still_load(setup):
    palace, astro, _ = setup
    note = palace.remember("webhook retries are 3", ["billing/"])
    astro.state["memories"][note["id"]] = {"s": 1.4, "t": note["updated_at"], "recalls": 2}
    assert astro.stability(note) == pytest.approx(HALF_LIFE_MEMORY)
    astro.reinforce([note["id"]])
    assert astro.state["memories"][note["id"]]["recalls"] == 3


def test_stale_when_its_places_are_gone(setup, tmp_path):
    palace, astro, _ = setup
    (tmp_path / "billing").mkdir()
    alive = palace.remember("ok", ["billing/"])
    gone = palace.remember("old", ["legacy/payments.py"])
    assert not astro.is_stale(alive)
    assert astro.is_stale(gone)


def test_coactivation_grows_corridor_across_rooms_only(setup):
    palace, astro, _ = setup
    a = palace.remember("billing fact", ["billing/"])
    b = palace.remember("auth fact", ["auth/"])
    c = palace.remember("another billing fact", ["billing/x.py", "billing/"])
    for _ in range(LINK_AFTER):
        astro.co_activate([a["id"]])
        astro.co_activate([b["id"], c["id"]])
    assert b["id"] in palace.memories[a["id"]]["links"]
    assert c["id"] not in palace.memories[a["id"]]["links"]
    assert astro.report()["auto_links"]


def test_load_shrinks_allowance_and_budget_runs_out(setup):
    _, astro, clock = setup
    fresh = astro.allowance("s1", "billing/")
    astro.spend("s1", 3000)
    assert astro.allowance("s1", "billing/") < fresh
    clock.advance(3600)
    astro.spend("s1", SESSION_BUDGET)
    assert astro.allowance("s1", "billing/") == 0
    astro.reset_session("s1")
    assert astro.allowance("s1", "billing/") == fresh


def test_select_skips_dormant_and_keeps_constraints_under_pressure(setup):
    palace, astro, clock = setup
    palace.remember("old note", ["billing/"])
    palace.remember("money is Decimal", ["billing/"], kind="constraint")
    clock.advance(3 * HALF_LIFE_MEMORY)
    astro.spend("s1", SESSION_BUDGET)
    text = astro.select("s1", "billing/invoice.py")
    assert "money is Decimal" in text
    assert "old note" not in text


def test_select_brings_memories_from_a_hot_nearby_room(setup):
    palace, astro, _ = setup
    palace.remember("tokens expire after 15 minutes", ["auth/"])
    palace.remember("invoices are immutable", ["billing/"])
    astro.stimulate("auth/jwt.py", "write")
    assert astro.calcium("auth/") >= PREHEAT_LEVEL
    text = astro.select("s1", "billing/invoice.py")
    assert "invoices are immutable" in text
    assert "tokens expire after 15 minutes" in text
    assert text.index("Nearby (areas you've been working in):") < text.index("tokens expire")
    assert astro.select("s1", "billing/invoice.py") is None


def test_astro_cli_report_and_wake(tmp_path):
    palace = MemoryPalace(tmp_path)
    note = palace.remember("webhook retries are 3", ["billing/"])
    runner = CliRunner()
    root = ["--root", str(tmp_path)]
    runner.invoke(main, [*root, "recall", "billing/"])
    report = runner.invoke(main, [*root, "astro"])
    assert report.exit_code == 0
    assert "billing/" in report.output and "1 active" in report.output
    assert runner.invoke(main, [*root, "astro", "--wake", note["id"]]).exit_code == 0
    assert runner.invoke(main, [*root, "astro", "--wake", "nope"]).exit_code != 0


def test_astrocyte_state_is_git_ignored(tmp_path):
    palace = MemoryPalace(tmp_path)
    Astrocyte(palace).stimulate("billing/x.py", "read")
    ignored = (tmp_path / ".loci" / ".gitignore").read_text().splitlines()
    assert "astrocyte.json" in ignored
    assert json.loads((tmp_path / ".loci" / "astrocyte.json").read_text())["rooms"]
