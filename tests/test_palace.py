# loci-guard: ignore-file  (fixtures contain fake secrets on purpose)
"""Tests for the memory palace, the chat archive, and the room/archive hooks."""

import json

from click.testing import CliRunner

from loci_guard.brain import archive
from loci_guard.brain.associative import AssociativeIndex
from loci_guard.brain.palace import ATRIUM, RECURRING_THRESHOLD, MemoryPalace
from loci_guard.cli import main
from loci_guard.integrations import claude_hooks


def test_anchors_and_ancestors(tmp_path):
    (tmp_path / "billing").mkdir()
    palace = MemoryPalace(tmp_path)
    assert palace.anchor_for(tmp_path / "billing") == "billing/"
    assert palace.anchor_for("billing/api/invoice.py") == "billing/api/invoice.py"
    assert palace.anchor_for(None) == ATRIUM
    assert palace.anchor_for(tmp_path.parent / "elsewhere.py") is None
    assert palace.ancestors("billing/api/invoice.py") == ["billing/api/invoice.py", "billing/api/", "billing/", ATRIUM]


def test_recall_is_nearest_first_and_ignores_sibling_rooms(tmp_path):
    palace = MemoryPalace(tmp_path)
    palace.remember("project-wide", [ATRIUM])
    palace.remember("billing rule", ["billing/"], kind="constraint")
    palace.remember("invoice detail", ["billing/invoice.py"])
    palace.remember("auth only", ["auth/"])
    texts = [m["text"] for m in palace.recall("billing/invoice.py")]
    assert texts == ["invoice detail", "billing rule", "project-wide"]
    assert "auth only" not in texts
    assert "project-wide" not in [m["text"] for m in palace.recall("billing/invoice.py", include_atrium=False)]


def test_corridor_brings_linked_memory(tmp_path):
    palace = MemoryPalace(tmp_path)
    token = palace.remember("tokens are signed in auth/jwt.py", ["auth/"], kind="decision")
    refund = palace.remember("refunds need a valid token", ["billing/"])
    assert palace.link(refund["id"], token["id"])
    recalled = palace.recall("billing/refund.py")
    linked = next(m for m in recalled if m["id"] == token["id"])
    assert linked["via"] == refund["id"]


def test_repeated_mistake_becomes_constraint(tmp_path):
    palace = MemoryPalace(tmp_path)
    for _ in range(RECURRING_THRESHOLD):
        lesson = palace.record_lesson("billing/invoice.py", "SEC003", "AWS key")
    assert lesson["kind"] == "constraint"
    assert lesson["count"] == RECURRING_THRESHOLD
    assert lesson["anchors"] == ["billing/"]
    assert len(palace.memories) == 1


def test_visits_are_per_session_and_reset(tmp_path):
    palace = MemoryPalace(tmp_path)
    m = palace.remember("billing rule", ["billing/"])
    assert palace.unseen("s1", [m]) == [m]
    palace.mark_seen("s1", [m["id"]])
    assert palace.unseen("s1", [m]) == []
    assert palace.unseen("s2", [m]) == [m]
    palace.reset_visits("s1")
    assert palace.unseen("s1", [m]) == [m]


def test_promote_and_forget(tmp_path):
    palace = MemoryPalace(tmp_path)
    a = palace.remember("always use decimals for money", ["billing/"], kind="candidate")
    b = palace.remember("other", ["billing/"])
    palace.link(a["id"], b["id"])
    assert palace.promote(a["id"])["kind"] == "constraint"
    assert palace.forget(a["id"])
    assert a["id"] not in palace.memories[b["id"]]["links"]


def _write_transcript(tmp_path, file_in_repo, create=True):
    if create:
        file_in_repo.parent.mkdir(parents=True, exist_ok=True)
        file_in_repo.touch()
    lines = [
        {"type": "custom-title", "customTitle": "Fix invoice totals", "sessionId": "abc12345-0000"},
        {"type": "user", "sessionId": "abc12345-0000", "timestamp": "2026-11-10T09:00:00Z",
         "message": {"role": "user", "content": "<system-reminder>noise</system-reminder>Fix the totals. "
                     "Usa sempre Decimal per i soldi, mai float. My key is AKIAIOSFODNN7EXAMPLE"}},
        {"type": "assistant", "sessionId": "abc12345-0000", "timestamp": "2026-11-10T09:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Switching totals to Decimal."},
             {"type": "tool_use", "name": "Edit", "input": {"file_path": str(file_in_repo), "old_string": "a", "new_string": "b"}},
         ]}},
        {"type": "assistant", "isSidechain": True, "sessionId": "abc12345-0000",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent chatter"}]}},
        {"type": "api-request-blob", "message": {"content": [{"type": "text", "text": "duplicate user text"}]}},
    ]
    path = tmp_path / "abc12345-0000.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return path


def test_ingest_hangs_chat_in_touched_room_and_redacts(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    transcript = _write_transcript(tmp_path, project / "billing" / "invoice.py")
    palace = MemoryPalace(project)
    session = archive.ingest(palace, transcript)

    assert session["title"] == "Fix invoice totals"
    assert session["rooms"] == ["billing/"]
    text = archive.read_session(palace, "abc1")
    assert "AKIAIOSFODNN7EXAMPLE" not in text and "[REDACTED:SEC003]" in text
    assert "noise" not in text and "subagent chatter" not in text and "duplicate user text" not in text
    assert any(m["kind"] == "chat" for m in palace.recall("billing/invoice.py"))
    assert any("Decimal" in m["text"] for m in palace.candidates())
    assert (project / ".loci" / ".gitignore").read_text().startswith("archive/")

    archive.ingest(palace, transcript)
    assert len([m for m in palace.memories.values() if m["kind"] == "chat"]) == 1


def test_reingesting_an_old_chat_does_not_bring_back_a_renamed_room(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    palace = MemoryPalace(project)
    transcript = _write_transcript(tmp_path, project / "billing" / "invoice.py")
    assert archive.ingest(palace, transcript)["rooms"] == ["billing/"]

    (project / "billing").rename(project / "payments")
    assert archive.ingest(palace, transcript)["rooms"] == [ATRIUM]
    assert palace.orphans() == []


def test_pasted_text_never_becomes_a_candidate_rule():
    turns = [("user", '<pasted_content id="ab12">\nNeurons are always active and never rest.\n'
                      '</pasted_content id="ab12">\nFrom now on always answer in Italian.', None)]
    assert archive.rule_candidates(turns) == ["From now on always answer in Italian."]


def test_search_finds_memories_and_chat_passages(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    palace = MemoryPalace(project)
    archive.ingest(palace, _write_transcript(tmp_path, project / "billing" / "invoice.py"))
    hits = archive.search_archive(palace, "decimal")
    assert hits and hits[0]["session_id"] == "abc12345-0000"
    assert [m["kind"] for m in AssociativeIndex(palace).recall("invoice")] == ["chat"]
    assert AssociativeIndex(palace).recall("decimal") == []  # only an unconfirmed candidate says it


def _hook(command, payload):
    return CliRunner().invoke(main, ["hook", command], input=json.dumps(payload))


def test_enter_room_whispers_once_per_session(tmp_path):
    MemoryPalace(tmp_path).remember("money is Decimal", ["billing/"], kind="constraint")
    payload = {"cwd": str(tmp_path), "session_id": "s1", "tool_name": "Read",
               "tool_input": {"file_path": str(tmp_path / "billing" / "invoice.py")}}
    first = _hook("enter-room", payload)
    output = json.loads(first.output)["hookSpecificOutput"]
    assert output["hookEventName"] == "PostToolUse"
    assert "money is Decimal" in output["additionalContext"]
    assert _hook("enter-room", payload).output.strip() == ""

    _hook("session-start", {"cwd": str(tmp_path), "session_id": "s1", "source": "compact"})
    assert "money is Decimal" in _hook("enter-room", payload).output


def test_hooks_stay_silent_in_uninitialized_projects(tmp_path):
    payload = {"cwd": str(tmp_path), "session_id": "s1", "tool_input": {"file_path": str(tmp_path / "a.py")}}
    assert _hook("enter-room", payload).output == ""
    assert _hook("archive", {"cwd": str(tmp_path), "transcript_path": str(tmp_path / "x.jsonl")}).output == ""
    assert not (tmp_path / ".loci").exists()


def test_blocked_write_becomes_lesson_in_its_room(tmp_path):
    (tmp_path / ".loci").mkdir()
    payload = {"cwd": str(tmp_path), "tool_name": "Write",
               "tool_input": {"file_path": str(tmp_path / "billing" / "keys.py"), "content": 'K = "AKIAIOSFODNN7EXAMPLE"'}}
    assert _hook("pretool", payload).exit_code == 2
    lessons = [m for m in MemoryPalace(tmp_path).recall("billing/other.py") if m["kind"] == "lesson"]
    assert lessons and lessons[0]["rule_id"] == "SEC003"


def test_archive_hook_on_precompact_prints_summary_instructions(tmp_path):
    project = tmp_path / "project"
    (project / ".loci").mkdir(parents=True)
    transcript = _write_transcript(tmp_path, project / "billing" / "invoice.py")
    result = _hook("archive", {"cwd": str(project), "transcript_path": str(transcript), "hook_event_name": "PreCompact"})
    assert claude_hooks.PRECOMPACT_INSTRUCTIONS in result.output
    assert MemoryPalace(project).sessions


def test_install_adds_palace_hooks(tmp_path):
    claude_hooks.install(tmp_path)
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    assert hooks["PostToolUse"][0]["matcher"] == claude_hooks.FILE_TOOLS
    assert set(hooks) == {"PreToolUse", "PostToolUse", "SessionStart", "PreCompact", "SessionEnd"}


def test_install_with_venv_path_goes_to_local_settings_and_updates_in_place(tmp_path):
    exe = r"C:\Users\me\my project\.venv\Scripts\loci-guard.exe"
    claude_hooks.install(tmp_path, exe)
    claude_hooks.install(tmp_path, exe.replace("me", "you"))
    claude_hooks.install(tmp_path, exe)
    assert not (tmp_path / ".claude" / "settings.json").exists()
    hooks = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())["hooks"]
    pretool = hooks["PreToolUse"]
    assert len(pretool) == 1
    assert pretool[0]["hooks"][0]["command"] == '"C:/Users/me/my project/.venv/Scripts/loci-guard.exe" hook pretool'
    assert hooks["SessionEnd"][0]["hooks"][0]["command"].endswith('loci-guard.exe" hook archive')


def test_install_with_a_python_path_uses_the_module_form_and_replaces_the_launcher(tmp_path):
    claude_hooks.install(tmp_path, r"C:\proj\.venv\Scripts\loci-guard.exe")
    claude_hooks.install(tmp_path, r"C:\proj\.venv\Scripts\python.exe")
    pretool = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())["hooks"]["PreToolUse"]
    assert len(pretool) == 1
    assert pretool[0]["hooks"][0]["command"] == '"C:/proj/.venv/Scripts/python.exe" -m loci_guard hook pretool'
    assert claude_hooks.command_prefix("/usr/bin/python3.12") == '"/usr/bin/python3.12" -m loci_guard'


def test_cli_remember_recall_and_map(tmp_path):
    runner = CliRunner()
    root = ["--root", str(tmp_path)]
    assert runner.invoke(main, [*root, "remember", "webhooks retry 3 times", "--at", "billing/"]).exit_code == 0
    assert "webhooks retry" in runner.invoke(main, [*root, "recall", "billing/webhook.py"]).output
    assert "billing/" in runner.invoke(main, [*root, "map"]).output
    outside = runner.invoke(main, [*root, "remember", "x", "--at", str(tmp_path.parent / "out.py")])
    assert outside.exit_code != 0
