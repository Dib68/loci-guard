"""Tests for the foresight swarm: grounded personas, recorded predictions, Brier calibration."""

import time

import pytest
from click.testing import CliRunner

from loci_guard.brain.astrocyte import Astrocyte
from loci_guard.brain.foresight import MAX_WEIGHT, MIN_WEIGHT, Foresight, parse_outcome
from loci_guard.brain.palace import MemoryPalace
from loci_guard.brain.workspace import room_broadcast
from loci_guard.cli import main
from loci_guard.mcp_server import Server


@pytest.fixture
def palace(tmp_path):
    p = MemoryPalace(tmp_path)
    p.remember("Refunds must go through the billing queue", ["billing/"], kind="constraint")
    p.record_lesson("billing/keys.py", "SEC003", "AWS key committed")
    return p


def _session(foresight, description="Add partial refunds", places=("billing/refunds.py",)):
    return foresight.begin(description, list(places))["session"]["id"]


def test_personas_are_grounded_in_the_palace(palace):
    people = {p["persona"]: p for p in Foresight(palace).begin("Add partial refunds", ["billing/refunds.py"])["people"]}
    assert set(people) == {"maintainer:billing/", "security", "tester", "user", "operator", "skeptic"}
    assert any("billing queue" in m["text"] for m in people["maintainer:billing/"]["memories"])
    assert any(m["kind"] == "lesson" for m in people["security"]["memories"])
    assert all(p["score"]["weight"] == 1.0 and not p["score"]["calibrated"] for p in people.values())


def test_record_parses_predict_lines_only(palace):
    foresight = Foresight(palace)
    sid = _session(foresight)
    recorded = foresight.record(sid, "\n".join([
        "Some reasoning first.",
        "PREDICT security | 0.7 | refund amounts get logged | grep logs for amount=",
        "PREDICT maintainer:billing/ | 1.4 | the queue is bypassed | review the diff",
    ]))
    assert [p["persona"] for p in recorded] == ["security", "maintainer:billing/"]
    assert recorded[1]["probability"] == 1.0
    with pytest.raises(ValueError, match="unknown persona"):
        foresight.record(sid, "PREDICT oracle | 0.5 | x | y")
    with pytest.raises(ValueError, match="no foresight session"):
        foresight.record("nope", "PREDICT security | 0.5 | x | y")


def test_brier_calibration_moves_weights(palace):
    foresight = Foresight(palace)
    sid = _session(foresight)
    lines = [f"PREDICT tester | 0.9 | tests break {i} | ci" for i in range(3)]
    lines += [f"PREDICT skeptic | 0.9 | change gets reverted {i} | git log" for i in range(3)]
    for p in foresight.record(sid, "\n".join(lines)):
        foresight.resolve(p["id"], happened=p["persona"] == "tester")
    tester, skeptic = foresight.score("tester"), foresight.score("skeptic")
    assert tester["brier"] == pytest.approx(0.01) and tester["weight"] == pytest.approx(1.96)
    assert skeptic["brier"] == pytest.approx(0.81) and skeptic["weight"] == MIN_WEIGHT
    assert tester["weight"] <= MAX_WEIGHT


def test_consensus_weights_by_track_record(palace):
    foresight = Foresight(palace)
    warmup = _session(foresight, "warm-up")
    for p in foresight.record(warmup, "\n".join(f"PREDICT skeptic | 0.9 | wrong {i} | x" for i in range(3))):
        foresight.resolve(p["id"], happened=False)
    sid = _session(foresight)
    foresight.record(sid, "PREDICT skeptic | 0.8 | refunds are unneeded | ask PM\nPREDICT tester | 0.5 | edge case on zero | test")
    consensus = foresight.consensus(sid)
    assert [p["persona"] for p in consensus] == ["tester", "skeptic"]
    assert consensus[1]["weighted"] == pytest.approx(0.16)


def test_resolution_teaches_the_palace_and_cannot_repeat(palace):
    foresight = Foresight(palace)
    sid = _session(foresight)
    p = foresight.record(sid, "PREDICT security | 0.8 | refund ids are guessable | try sequential ids")[0]
    foresight.resolve(p["id"], happened=True)
    lessons = [m for m in palace.recall("billing/refunds.py") if m["kind"] == "lesson"]
    assert any("Foreseen and it happened: refund ids are guessable" in m["text"] for m in lessons)
    with pytest.raises(ValueError, match="already resolved"):
        foresight.resolve(p["id"], happened=False)


def test_open_predictions_surface_in_their_rooms_with_evidence(palace):
    # Predictions made a minute ago: Windows' ~15 ms clock would otherwise stamp them and the
    # lesson below with the same time, making "recorded after the prediction" flaky.
    made_at = time.time() - 60
    foresight = Foresight(palace, now=lambda: made_at)
    sid = _session(foresight)
    p = foresight.record(sid, "PREDICT operator | 0.6 | refund retries flood the queue | queue depth alert")[0]
    project_wide = foresight.begin("Rename the package", [])["session"]["id"]
    foresight.record(project_wide, "PREDICT user | 0.5 | imports break | pip install")
    text = room_broadcast(palace, Astrocyte(palace), "s1", "billing/refunds.py", dry_run=True).text
    assert "Forecasts (open predictions" in text and "refund retries flood the queue" in text
    assert "imports break" not in text
    assert "imports break" not in (room_broadcast(palace, Astrocyte(palace), "s1", "docs/x.md", dry_run=True).text or "")
    palace.record_lesson("billing/queue.py", "QUA003", "bare except swallowed queue errors")
    assert any("queue" in m["text"] for m in foresight.evidence(p))


def test_void_closes_a_prediction_without_scoring(palace):
    foresight = Foresight(palace)
    p = foresight.record(_session(foresight), "PREDICT user | 0.9 | untestable claim | nothing")[0]
    foresight.resolve(p["id"], parse_outcome("void"))
    assert foresight.open_predictions() == [] and foresight.score("user")["n"] == 0


def test_parse_outcome():
    assert parse_outcome("Sì") and parse_outcome("yes") and parse_outcome("no") is False
    with pytest.raises(ValueError):
        parse_outcome("maybe")


def test_cli_flow(palace):
    runner = CliRunner()
    root = ["--root", str(palace.root)]
    order = runner.invoke(main, [*root, "foresee", "new", "Add partial refunds", "--at", "billing/refunds.py"]).output
    sid = order.split("## Foresight ")[1].split(":")[0]
    assert "persona `maintainer:billing/`" in order and "one parallel subagent per persona" in order
    recorded = runner.invoke(main, [*root, "foresee", "record", sid],
                             input="PREDICT security | 0.7 | refund amounts get logged | grep logs\n")
    assert recorded.exit_code == 0 and "70%" in recorded.output
    pid = recorded.output.split("#")[1].split()[0]
    assert pid in runner.invoke(main, [*root, "foresee", "open"]).output
    assert runner.invoke(main, [*root, "foresee", "resolve", pid, "no"]).exit_code == 0
    assert "No open predictions." in runner.invoke(main, [*root, "foresee", "open"]).output
    assert "security" in runner.invoke(main, [*root, "foresee", "scores"]).output
    assert runner.invoke(main, [*root, "foresee", "record", sid], input="nothing useful\n").exit_code != 0


def test_mcp_tools(palace):
    server = Server(palace.root)

    def call(tool, **arguments):
        result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": tool, "arguments": arguments}})["result"]
        return result["content"][0]["text"], result["isError"]

    order, _ = call("foresee", description="Add partial refunds", at=["billing/refunds.py"])
    sid = order.split("## Foresight ")[1].split(":")[0]
    recorded, failed = call("foresee_record", session=sid, predictions="PREDICT tester | 0.6 | zero-amount refund crashes | unit test")
    assert not failed and "60%" in recorded
    pid = recorded.split("#")[1].split()[0]
    assert not call("foresee_resolve", id=pid, happened=True)[1]
    assert call("foresee_resolve", id=pid, happened=True)[1]
