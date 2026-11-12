# Changelog

## 0.2.0 (2026-11-12)

### Shield
- **Blocks high-severity findings by default**, not only critical ones. A fresh install used to let SQL built with f-strings, `shell=True`, `eval`, `innerHTML`, JWTs, database URLs with passwords and PII in logs through without a word. Projects whose `.loci/config.yaml` already sets `block_severity` keep their setting.
- Findings under the block threshold now reach the agent right after the write, through the `PostToolUse` hook, instead of being dropped.

### Memory and context
- Long memories arrive as a gist plus every number, path and identifier the cut part held; `loci-guard recall --id ID` prints the full text. Rules, intentions and wiki bullets are never cut.
- Memories saved together in one place (an episode) arrive as one item.
- A restatement of an item already on stage is dropped.
- Past-chat notes no longer repeat in every room, and foresight lessons no longer carry how they were checked.
- Session-start housekeeping is one short line per chore.
- On this repository, a simulated session gets twice as many items (16 vs 8) for the same ~965 tokens.
- `loci-guard relocate`: after a rename, lists rooms whose folder is gone, guesses where they went and moves their memories (nothing is deleted).
- Text that Claude Code injects into transcripts (usage notes, compaction summaries) no longer becomes candidate rules.
- `SCREAMING_SNAKE_CASE` constants count as details that rewordings and gists must keep.

### Skill
- New "when" rules, each tested with a behaviour scenario: read shortened items before changing the code they are about, treat a correction as a rule, don't record noise, ask before relocating memories, gate every commit.
- Rule ids moved to `references/rules.md`.

### MCP
- The stdio server reads and writes UTF-8 whatever the platform default, so accented text from a client is no longer garbled on Windows, and a leading BOM no longer breaks the first message.

### Hooks
- `loci-guard --root PATH hook ...` uses `PATH` when the hook payload carries no `cwd`.
- Hooks written by earlier versions (named CodeSentinel) are updated in place, not duplicated.

## 0.1.0 (2026-11-12)

First public version.
