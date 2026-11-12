"""The skill must stay valid under the agentskills.io specification and in sync with the CLI."""

import re
from pathlib import Path

import click
import yaml

from loci_guard.cli import main
from loci_guard.integrations.setup import SKILL_DIR, doctor, install_skill, skill_dir, skill_files

SKILL = Path(__file__).resolve().parents[1] / "skills" / "loci-guard"


def _split(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter between --- lines"
    return yaml.safe_load(match.group(1)), match.group(2)


def test_frontmatter_follows_the_spec():
    meta, _ = _split(SKILL / "SKILL.md")
    assert set(meta) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    assert meta["name"] == SKILL.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
    assert 1 <= len(meta["description"]) <= 1024
    assert len(meta.get("compatibility", "")) <= 500
    assert meta.get("license") == "MIT"


def test_body_stays_small_enough_to_load_every_time():
    _, body = _split(SKILL / "SKILL.md")
    assert len(body.splitlines()) < 500
    assert len(body.split()) * 1.4 < 2500, "keep SKILL.md lean; move detail to references/"


def test_references_exist_and_are_one_level_deep():
    _, body = _split(SKILL / "SKILL.md")
    links = re.findall(r"\]\(([^)#\s]+\.md)\)", body)
    assert links, "SKILL.md should point to its reference files"
    for link in links:
        assert (SKILL / link).is_file(), f"missing {link}"
        assert link.count("/") == 1 and link.startswith("references/")
    for ref in (SKILL / "references").glob("*.md"):
        assert not re.search(r"\]\(([^)]+\.md)\)", ref.read_text(encoding="utf-8")), f"{ref.name} links deeper"
        assert f"references/{ref.name}" in links, f"{ref.name} is never referenced from SKILL.md"


def _commands() -> dict[str, set[str]]:
    out = {}
    for name, cmd in main.commands.items():
        out[name] = set(cmd.commands) if isinstance(cmd, click.Group) else set()
    return out


def test_every_command_the_skill_and_readme_mention_exists():
    known = _commands()
    texts = [(SKILL / rel).read_text(encoding="utf-8") for rel in skill_files(SKILL)]
    texts.append((SKILL.parents[1] / "README.md").read_text(encoding="utf-8"))
    # Commands appear at the start of code-block lines or right after a backtick; prose doesn't count.
    mentions = re.findall(r"(?:^|`)loci-guard ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", "\n".join(texts), re.MULTILINE)
    assert mentions
    for command, sub in mentions:
        assert command in known, f"the skill mentions `loci-guard {command}`, which doesn't exist"
        if known[command] and sub:
            assert sub in known[command], f"`loci-guard {command} {sub}` doesn't exist"


def test_setup_installs_the_whole_skill_and_doctor_notices_missing_files(tmp_path):
    assert skill_dir() == SKILL
    install_skill(tmp_path)
    installed = {p.relative_to(tmp_path / SKILL_DIR).as_posix() for p in (tmp_path / SKILL_DIR).rglob("*.md")}
    assert installed == {rel.as_posix() for rel in skill_files(SKILL)}
    assert {c.name: c.ok for c in doctor(tmp_path)}["skill up to date"]
    (tmp_path / SKILL_DIR / "references" / "wiki.md").unlink()
    stale = {c.name: c for c in doctor(tmp_path)}["skill up to date"]
    assert not stale.ok and "references/wiki.md" in stale.detail
