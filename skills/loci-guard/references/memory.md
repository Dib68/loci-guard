# The memory palace, in depth

Read this when you need to look up memories by hand, understand why something was or wasn't shown to you, deal with merged or reworded memories, or search past chats.

## Places

Memories hang in places: the **atrium** holds project-wide ones, every folder is a **room**, and a memory can be linked to others (**corridors**). With the Claude Code hooks installed, reading or editing a file shows you the memories of its room and of the rooms that contain it, once per session. Without hooks, look before you work in an area:

```bash
loci-guard recall billing/          # what hangs in this room and above it
loci-guard map                      # which rooms hold memories
loci-guard link ID1 ID2             # two memories that explain each other
```

## The astrocyte: how much you are told

A background model doses what the palace tells you. Rooms you work in heat up and share a few memories with linked or nearby hot rooms. Memories that keep being useful get stronger; ones nobody recalls fade to dormant (still searchable, never deleted). Constraints, decisions and intentions never fade. When many memories arrive in a short time, it holds some back and says so; then run `loci-guard recall <room>` if you need them.

- `loci-guard astro` shows which areas are active and how many memories are dormant or stale.
- `loci-guard astro --stale` lists memories whose files were deleted or moved. Offer to re-anchor them or remove them, and ask before removing.
- After a folder or file is renamed, its memories stop surfacing. `loci-guard relocate` lists rooms that no longer exist and guesses where each went; with the user's OK, `loci-guard relocate OLD/ NEW/` moves every memory under it. It never deletes anything.
- `loci-guard recall <room> --all` includes dormant memories; `loci-guard astro --wake ID` restores one.

## The workspace: why you saw what you saw

Everything Loci Guard puts in your context arrives as one block chosen by a single arbiter: brain rules, palace memories, wiki bullets, forecasts, nearby memories and housekeeping notes compete on salience within the astrocyte's budget. Constraints and intentions always get through. Wiki bullets whose sources you already received are dropped. Groups appear in a fixed order: Intentions, Rules, Pitfalls, Forecasts, Decisions, Notes, Linked, Nearby, Project, Housekeeping. Rules are binding; Intentions are to-dos; Forecasts are open predictions about the area, weighted by how reliable their persona has been.

If the user asks why you did or didn't know something, run `loci-guard workspace <file>` (or with no path, for session start). It shows every proposal, whether it was broadcast, and why the others lost (`over capacity`, `below ignition`, `duplicate`, `restates a broadcast item`, `already seen this session`, `covered by a broadcast memory`, `in the wiki page`, `dormant`). It changes nothing.

## Merging, rewording and history

Saving something close to an existing memory in the same room merges into it or links to it; `remember` says which. Memories with the same words but different numbers, names or paths are never merged, only linked. If it merged two things that should stay distinct, reword the new one to make the difference explicit.

When a command reports `the new wording dropped: ...`, check whether those details are no longer true. If they still hold, save again with them included. Nothing is lost either way: `loci-guard history ID` (or a brain constraint's name) shows every wording and what each change dropped and added.

`loci-guard forget NAME` removes a project-wide constraint, `loci-guard forget-memory ID` a palace memory. Ask the user before removing either.

## Past chats

When the user says "as we decided", "last time", "like before", or you need the reason behind existing code, search before guessing. Search is associative: results marked `by association` didn't contain your words but sit in the same room, corridor or concept cluster as ones that did. Weigh them as leads, not facts.

```bash
loci-guard recall --search "refund rounding"   # memories + matching passages of past chats
loci-guard recall --list                       # archived chats
loci-guard recall --session 1a2b3c4d           # one full chat (secrets redacted)
```

Chats are archived automatically before compaction and at session end; `loci-guard ingest` archives all past chats of the project. Past chats are not repeated in every room you open; search them when you need them.

## Candidate rules

The archive collects sentences where the user stated a preference ("always...", "never...", "sempre", "mai", "d'ora in poi"). Pasted documents and text the harness injects (usage notes, compaction summaries) never count. Candidates are not applied until confirmed: when `loci-guard recall --candidates` lists some, show them to the user and run `loci-guard promote ID` only for the ones the user approves.
