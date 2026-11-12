"""Chat archive: Claude Code transcripts, redacted, hung in the palace rooms they touched."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from loci_guard.brain.palace import ATRIUM, MemoryPalace, _make_id
from loci_guard.shield.patterns import CREDENTIAL_PATTERNS, PRIVACY_PATTERNS

ARCHIVE_DIR = "archive"
FILE_KEYS = ("file_path", "notebook_path")
MAX_ROOMS_PER_CHAT = 5
MAX_CANDIDATES_PER_CHAT = 5

REDACT_PATTERNS = CREDENTIAL_PATTERNS + [p for p in PRIVACY_PATTERNS if p.id in ("PRV001", "PRV002")]

_NOISE_TAGS = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|local-command-stderr|"
    r"command-name|command-message|command-args|user-prompt-submit-hook)>.*?</\1>",
    re.DOTALL,
)

# Pasted documents are not the user's own words, so they never become rules.
_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content\b[^>]*>", re.DOTALL)

# Sentences where the user states a standing preference or corrects the agent.
_RULE_CUES = re.compile(
    r"\b(sempre|mai|non (?:usare|fare|toccare|mettere|aggiungere|scrivere|cambiare)|evita|preferisco|"
    r"ricorda(?:ti)?|d'ora in poi|da ora in poi|always|never|don't|do not|stop doing|prefer|"
    r"remember|from now on)\b",
    re.IGNORECASE,
)


def transcripts_dir(project_root: str | Path) -> Path:
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(Path(project_root).resolve()))
    return Path.home() / ".claude" / "projects" / encoded


def redact(text: str) -> str:
    out = []
    for line in text.splitlines():
        for p in REDACT_PATTERNS:
            if any(re.search(e, line, re.IGNORECASE) for e in p.exclude_patterns):
                continue
            line = p.compiled.sub(f"[REDACTED:{p.id}]", line)
        out.append(line)
    return "\n".join(out)


def _clean(text: str) -> str:
    return _NOISE_TAGS.sub("", text).strip()


def _text_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_transcript(path: Path) -> dict[str, Any]:
    session_id, title, files, turns = path.stem, None, [], []
    first_ts = last_ts = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"user"' not in line and '"assistant"' not in line and "custom-title" not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = record.get("type")
            if kind == "custom-title":
                title = record.get("customTitle") or title
                continue
            if kind not in ("user", "assistant") or record.get("isSidechain"):
                continue
            # Text the harness injects (usage notes, compaction summaries, skill bodies) is not the user's.
            if record.get("isMeta") or record.get("isCompactSummary"):
                continue
            session_id = record.get("sessionId", session_id)
            ts = record.get("timestamp")
            first_ts = first_ts or ts
            last_ts = ts or last_ts
            content = record.get("message", {}).get("content")
            if kind == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_input = block.get("input") or {}
                        files.extend(tool_input[k] for k in FILE_KEYS if isinstance(tool_input.get(k), str))
            text = _clean(_text_blocks(content))
            if not text:
                continue
            role = "user" if kind == "user" else "assistant"
            if turns and turns[-1][0] == role:
                turns[-1] = (role, turns[-1][1] + "\n\n" + text, turns[-1][2])
            else:
                turns.append((role, text, ts))
    if not title:
        first_user = next((t for r, t, _ in turns if r == "user"), "")
        title = first_user.splitlines()[0][:80] if first_user else "(untitled chat)"
    return {
        "session_id": session_id,
        "title": redact(title),
        "started_at": first_ts,
        "ended_at": last_ts,
        "files": files,
        "turns": turns,
    }


def rule_candidates(turns: list[tuple[str, str, Any]]) -> list[str]:
    found = []
    for role, text, _ in turns:
        if role != "user":
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", _PASTED.sub("", text)):
            sentence = sentence.strip()
            if 12 <= len(sentence) <= 300 and _RULE_CUES.search(sentence) and sentence not in found:
                found.append(redact(sentence))
    return found[:MAX_CANDIDATES_PER_CHAT]


def ingest(palace: MemoryPalace, transcript_path: str | Path) -> dict[str, Any] | None:
    """Archive one transcript and hang it in the rooms whose files it touched."""
    path = Path(transcript_path)
    if not path.is_file():
        return None
    parsed = parse_transcript(path)
    if not parsed["turns"]:
        return None

    sid = parsed["session_id"]
    rooms = Counter(
        palace.room_of(anchor)
        for anchor in (palace.anchor_for(f) for f in parsed["files"])
        if anchor is not None
    )
    anchors = [room for room, _ in rooms.most_common(MAX_ROOMS_PER_CHAT)] or [ATRIUM]

    archive_dir = palace.dir / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    body = [f"# {parsed['title']}", f"session {sid} | {parsed['started_at']} -> {parsed['ended_at']}", ""]
    for role, text, _ in parsed["turns"]:
        body.append(f"**{'User' if role == 'user' else 'Claude'}:** {redact(text)}\n")
    (archive_dir / f"{sid}.md").write_text("\n".join(body), encoding="utf-8")

    date = (parsed["started_at"] or "")[:10]
    palace.remember(
        f"Chat {date}: {parsed['title']} (full text: loci-guard recall --session {sid[:8]})",
        anchors=anchors, kind="chat", source="archive", memory_id=_make_id("chat", sid), session_id=sid,
    )
    for stale in [m["id"] for m in palace.memories.values()
                  if m["kind"] == "candidate" and m.get("session_id") == sid]:
        del palace.memories[stale]
    for sentence in rule_candidates(parsed["turns"]):
        palace.remember(
            sentence, anchors=anchors, kind="candidate", source="archive",
            memory_id=_make_id("candidate", sid, sentence), session_id=sid,
        )

    palace.sessions[sid] = {
        "id": sid,
        "title": parsed["title"],
        "started_at": parsed["started_at"],
        "ended_at": parsed["ended_at"],
        "rooms": anchors,
        "turns": len(parsed["turns"]),
    }
    palace.save()
    return palace.sessions[sid]


def ingest_all(palace: MemoryPalace) -> list[dict[str, Any]]:
    folder = transcripts_dir(palace.root)
    if not folder.is_dir():
        return []
    results = [ingest(palace, p) for p in sorted(folder.glob("*.jsonl"))]
    return [r for r in results if r]


def find_session(palace: MemoryPalace, prefix: str) -> str | None:
    matches = [sid for sid in palace.sessions if sid.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def read_session(palace: MemoryPalace, prefix: str) -> str | None:
    sid = find_session(palace, prefix)
    if sid is None:
        return None
    path = palace.dir / ARCHIVE_DIR / f"{sid}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def search_archive(palace: MemoryPalace, query: str, limit: int = 8, context: int = 1) -> list[dict[str, Any]]:
    """Find the chat passages that mention the query terms, best matches first."""
    terms = [t for t in query.lower().split() if len(t) > 1]
    folder = palace.dir / ARCHIVE_DIR
    if not terms or not folder.is_dir():
        return []
    hits = []
    for path in folder.glob("*.md"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            score = sum(line.lower().count(t) for t in terms)
            if score:
                snippet = "\n".join(lines[max(0, i - context): i + context + 1])
                hits.append({"session_id": path.stem, "line": i + 1, "score": score, "snippet": snippet[:600]})
    hits.sort(key=lambda h: -h["score"])
    for h in hits:
        h["title"] = palace.sessions.get(h["session_id"], {}).get("title", "")
    return hits[:limit]
