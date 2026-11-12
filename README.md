# Loci Guard

**Project memory, security shield and foresight for AI coding agents.**

Loci Guard learns your codebase and protects it. It remembers your rules, decisions and past mistakes where they belong in the repo, enforces them through Claude Code hooks, catches security problems in AI-written code before it is saved, and shares all of it with any other agent (Codex, Cursor, Copilot, Hermes) through MCP and `AGENTS.md`.

## The Problem

AI coding assistants in 2026 have three critical failures:

1. **They forget.** Context decay causes AI to lose your project conventions, architectural decisions, and constraints after ~16 turns. Compliance drops from 73% to 33%.
2. **They write insecure code.** Developers using AI write "significantly less secure code" with a false sense of security. Hardcoded credentials, SQL injection, XSS — the basics get missed.
3. **They don't know your project.** AI performs significantly worse on existing codebases than greenfield projects. It adds duplicate code, ignores your patterns, and introduces dependencies you already have.

Loci Guard solves all three.

## How It Works

```
  MEMORY                                  SHIELD
  brain      rules learned from a scan    security, privacy, quality rules
  palace     memories hung in folders     pre-write check, pre-commit gate
  astrocyte  what to recall, how much
  wiki       compiled page per folder     FORESIGHT
  workspace  one arbiter for context      personas forecast a change,
                                          scored on being right
                 |                                   |
  CLAUDE CODE HOOKS   block insecure writes, recall the folder you enter,
                      restore memory after compaction, archive chats
  OTHER AGENTS        MCP server + AGENTS.md, same .loci/ state
```

## Quick Start

```bash
# Not on PyPI yet: install from a clone of this repo
pip install -e .

# One step: config, scan, archive past chats, Claude Code hooks, skill
cd your-project
loci-guard setup

# Check the installation anytime: state files, hooks, their executable and speed, the skill
loci-guard doctor
```

`setup` writes the hooks as `"<full path to python>" -m loci_guard hook ...`, using the Python that ran it, so they work inside a virtualenv and don't depend on the generated `loci-guard.exe` launcher, which Windows application-control policies can block. That path is machine-specific, so it goes to `.claude/settings.local.json` and `setup` adds that file to `.gitignore`. The hooks take effect in Claude Code right away. If the `loci-guard` command is blocked on your machine, `python -m loci_guard` works the same.

Other agents and extras:

```bash
loci-guard integrate --agents   # Loci Guard block in AGENTS.md (Codex, Cursor, Copilot, Hermes...)
loci-guard mcp --print-config   # MCP server entry to paste into any MCP client
loci-guard install-hook         # git pre-commit hook
```

`AGENTS.md` is rewritten only between `<!-- loci-guard:start -->` and `<!-- loci-guard:end -->`, so anything you wrote yourself stays.

## Features

### Brain: Learns Your Project

- **Auto-scans** your codebase to detect languages, architecture, naming conventions, dependencies
- **Learns rules** automatically — if 60%+ of your functions have docstrings, it creates a "require docstrings" constraint
- **Records decisions** — `loci-guard decide "Use PostgreSQL" --reason "Team expertise"`
- **Persists across sessions** — stored in `.loci/` so it travels with your repo
- **Generates context** for any AI tool so it knows your project from the first prompt

### Memory Palace: Remembers Where Things Belong

Loading every past chat into context makes agents forget faster, not slower: the window fills, compaction fires sooner, and rule compliance drops. Loci Guard borrows the structure of the method of loci instead. Every memory has a place, and only the place you are in gets recalled.

```
castle     = the repository
atrium     = project-wide memories (always loaded, keep it small)
rooms      = folders and files: billing/, billing/invoice.py
objects    = constraints, decisions, lessons, notes, past chats hung in a room
corridors  = links between memories in different rooms
```

- **Entering a room recalls it.** When Claude reads or edits `billing/invoice.py`, a hook shows it the memories of `billing/invoice.py`, `billing/` and any linked memories, once per session. After compaction or `/clear` the rooms are shown again.
- **Mistakes become lessons where they happened.** Every write the Shield blocks is hung in that room. Blocked three times, it becomes a constraint for the room.
- **Past chats are objects too.** Before compaction and at session end the transcript is archived, with secrets redacted, and hung in the rooms whose files it touched. `recall --search` finds the passage, `recall --session` brings back the whole chat.
- **It learns your rules, with your permission.** Sentences where you state a preference ("always…", "never…", "sempre", "mai", "d'ora in poi") become candidate rules. They apply only after `loci-guard promote ID`.

```bash
loci-guard remember "Stripe webhooks retry 3 times" --at billing/webhooks.py
loci-guard constrain money_is_decimal "Use Decimal for amounts, never float" --severity high --at billing/
loci-guard recall billing/invoice.py      # what this room remembers
loci-guard recall --search "refund rounding"
loci-guard map                            # the castle, room by room
loci-guard ingest                         # archive all past Claude Code chats of this project
```

`.loci/palace.json` (the map and its memories) can be committed and shared with the team. `.loci/archive/` (full chats), `visits.json` and `astrocyte.json` are git-ignored by default.

### Astrocyte: Doses the Memory

Half the brain is glia, and astrocytes do memory work neurons don't: they integrate activity slowly across many synapses, spread it as calcium waves, strengthen or weaken connections, and clear excess signal. Loci Guard's astrocyte is a functional model of those four jobs applied to context. It does not simulate biology and does not change how the model computes. What it changes is what reaches the model, and how much.

| Astrocyte job | In Loci Guard |
|---|---|
| Slow calcium integration | Each room has a calcium level raised by reads (1), writes (2) and blocked writes (3), with a 30-minute half-life. It spreads upward at half strength. |
| Calcium waves | Activity travels along corridors to linked rooms (30%). The hottest room off your current path shares its 2 strongest memories. |
| Gliotransmission | Each memory has its own stability; well-spaced recalls raise it, massed recalls barely do (see Memory Dynamics). Below 25% retrievability a memory goes dormant: hidden from rooms, still searchable. **Constraints and decisions never fade.** |
| Coupling | Memories from different rooms that activate together within 10 minutes, 3 times, get a corridor automatically. |
| Clearance | Each room entry gets about 1,500 characters, more in hot rooms and less under load (whatever was injected in the last 5 minutes), within a 20,000-character session budget. Constraints always get through. |

```bash
loci-guard astro            # activity heat map, active / dormant / stale counts, grown corridors
loci-guard astro --stale    # memories whose files were deleted or moved
loci-guard astro --wake ID  # bring a dormant memory back
loci-guard recall billing/ --all
```

The numbers are starting estimates, all constants at the top of `loci_guard/brain/astrocyte.py`. They need tuning against real use.

### Memory Dynamics: How Memories Are Encoded, Kept and Recalled

Four mechanisms from memory research, each reduced to a small, testable rule. They are functional analogies; nothing here simulates neurons.

| Mechanism | Rule in Loci Guard |
|---|---|
| **Pattern completion** (hippocampal indexing, after HippoRAG) | Memories, rooms and the concepts their texts mention form one graph. `recall --search` seeds the concepts and rooms in the query and runs Personalized PageRank, so a partial cue also reaches memories that share a room, a corridor or a concept with the direct matches. Each result says `matched: ...` or `by association`. Words are prefix-stemmed, so `refund`/`refunds` and `rimborso`/`rimborsi` meet. |
| **Pattern separation and integration** | When a memory is saved, it is compared with memories of the same kind in the same rooms. At 80% word overlap or more, and with the same numbers, names and paths, it **integrates** (one memory, newer wording, count goes up). From 50%, or when the words match but the details differ ("timeout 30 s" / "timeout 5 s"), it is **kept apart but linked**. Below that it is new. Texts under three content words are never merged. |
| **Spacing effect** (forgetting curve with stability) | Every memory has its own stability (half-life, starting at 30 days). Retrievability halves every stability period. A recall multiplies stability by `1 + 2 x (1 - retrievability)`: recalling something that had half faded doubles its life, recalling it again a minute later teaches nothing. Capped at one year. Below 25% retrievability a memory goes dormant. Constraints and decisions are exempt. |
| **Synaptic tagging and capture** | A blocked write is a strong event. Weak memories touched in that room in the previous hour are captured: their stability doubles. A note written just before a mistake in the same area is likely to matter. |

#### Reconsolidation without losing pieces

Human memories are rewritten each time they are recalled: the gist survives, details drift. Loci Guard separates the memory from its rewordings:

- **Recall never changes content.** It only changes stability.
- **Every rewording is versioned.** When a memory is integrated with a near-duplicate or updated by id, the old wording goes into its history (up to 20 versions). Hand-written brain constraints keep history too; auto-learned ones, regenerated by every scan, don't.
- **Dropped details are named.** `remember`, `decide` and `constrain` report what the new wording lost (`the new wording dropped: 90`). `loci-guard history ID` shows every version with what each change dropped and added.
- **Summaries are checked.** `wiki done` and `wiki lint` list the precise details of each cited source (numbers, names, paths, code) that appear nowhere on the page.

```bash
loci-guard recall --search "rimborso"   # associative recall + matching passages of past chats
loci-guard history 9bc3e2ddb9           # every wording of a memory (or a brain constraint by name)
loci-guard remember "..." --at billing/ # reports whether it was new, linked to a similar memory, or merged
loci-guard astro                        # mean stability and how many memories strong events captured
```

### Prospective Memory: Intentions

Remembering to *do* something when the right moment comes is its own kind of memory. People manage it best with implementation intentions: "when X happens, I'll do Y". Loci Guard keeps those too:

```bash
loci-guard intend "Update CHANGELOG.md for billing changes" --when billing/
loci-guard intend --list
loci-guard intend --done ID
```

An intention fires every time its place is entered, in every session and every connected agent, until it is marked done. In the workspace it is mandatory and comes first. It never fades and never becomes wiki material, because it is a to-do, not knowledge.

### Foresight Swarm: Personas That Earn Their Weight

Inspired by MiroFish, which seeds a knowledge graph and lets AI personas with their own memories and biases play out a scenario. Loci Guard applies the idea to a code change and adds the part that makes forecasts trustworthy: **measuring them**.

1. `loci-guard foresee new "Add partial refunds" --at billing/refunds.py` builds a maintainer persona for each touched room, plus security reviewer, test author, end user, on-call operator and skeptic. Each gets its own memories from the palace: the maintainer gets its room's rules and lessons, the security reviewer gets what the shield has blocked before.
2. The agent plays each persona as an independent forecaster (ideally one parallel subagent each) and records lines `PREDICT <persona> | <probability> | <what will happen> | <how we'll know>`.
3. `foresee record` shows the consensus with every probability weighted by its persona's track record.
4. When the outcome is known, `foresee resolve ID yes|no` scores the persona with the Brier score. After 3 resolved predictions a persona's weight moves between 0.2 (worse than a coin flip) and 2.0 (always right). Predictions that came true become lessons in their room, and open ones show up as Forecasts when the agent enters that room.

Loci Guard never calls a model: the agent you already use plays the personas.

```bash
loci-guard foresee open      # predictions waiting for an outcome, with evidence recorded since
loci-guard foresee scores    # each persona's Brier score and weight
```

### Shared Memory Across Agents (MCP)

Skills written for one agent increasingly run on many: the agentskills.io `SKILL.md` format is read by Claude Code, Hermes Agent and others, and Superpowers ships one repo to about 17 harnesses. Loci Guard goes one step further and shares the **memory**:

```bash
loci-guard mcp --print-config   # the mcpServers entry to paste into any MCP client
loci-guard mcp                  # the server itself (stdio)
loci-guard integrate --agents   # Loci Guard block in AGENTS.md
```

Every agent connected to the same project reads and writes the same `.loci/`: a rule Claude records, Hermes recalls; a lesson Cursor triggers, Codex is warned about. Tools: `project_context`, `recall`, `search`, `remember`, `constrain`, `intend`, `intend_done`, `history`, `check_code`, `foresee`, `foresee_record`, `foresee_resolve`.

**Safe with several agents at once.** Every save takes a lock, re-reads the file, replays only its own changes on top (a three-way merge), and replaces the file atomically, so readers never see a half-written file and writers don't erase each other's additions. When two agents change the same memory at the same moment, the later save wins. A file that is genuinely corrupt is set aside as `*.corrupt-<time>` instead of being overwritten. Measured on Windows with up to 8 processes saving into one project at once: 0 memories lost in 10 runs (before locking: 91-94% lost, and occasional wipes of the whole store).

### Wiki: Compiled Knowledge (Karpathy's LLM Wiki)

Retrieval rebuilds understanding from scattered fragments every time. Following Andrej Karpathy's LLM Wiki pattern, Loci Guard lets the agent **compile** what a room knows into one page, kept in `.loci/wiki/` and meant to be committed.

```
raw sources (immutable)  ->  palace memories, lessons, archived chats
compiled wiki            ->  .loci/wiki/<room>.md + index.md
schema                   ->  .loci/wiki/SCHEMA.md (yours to edit, never overwritten)
```

- **The agent writes, Loci Guard checks.** `wiki compile` hands the agent a work order; the agent writes the page; `wiki done` rejects secrets, citations to memories that don't exist and broken links, then stamps the page's sources and rebuilds the index. No API key: the compiler is the agent you already use.
- **Recompile only on surprise.** A page predicts its room. Only material it didn't account for asks for a rewrite: new memories, memories changed since compilation, and removed sources. A lesson blocked *again* after the page was written weighs the most, because the page failed to prevent it. Quiet rooms cost nothing.
- **Every claim cites its source** (`[#id]`), so the raw memory behind any sentence is one lookup away.
- **Pages replace fragments in context.** Entering a room with a page loads it, by section in priority order (Rules, Pitfalls, Decisions, Notes) with citations stripped. Anything newer than the page and every constraint still come through.
- **Lint**: dangling citations, broken links, files that no longer exist, orphan and stale pages.

```bash
loci-guard wiki status            # which pages are out of date, most surprising first
loci-guard wiki compile           # work orders for the agent
loci-guard wiki done billing/     # check + stamp after the agent writes the page
loci-guard wiki lint
loci-guard wiki show billing/
```

### Global Workspace: One Arbiter for the Context

In Global Workspace Theory, specialised modules compete to put content on a small central stage, and what wins is broadcast to the whole system. Loci Guard works the same way: the brain, the palace, the wiki and the astrocyte don't write into the agent's context directly. They **propose** items with a salience, and one arbiter decides.

- **Mandatory first.** Constraints always get through, even past capacity (up to a floor).
- **Then by salience**, within the capacity the astrocyte sets for this moment. Salience combines kind (rule > pitfall > decision > note), memory strength, room heat, distance from the file, and surprise (memories newer than the wiki page get a boost).
- **Ignition threshold.** Items too weak to matter never reach the stage.
- **Deduplication by provenance.** A wiki bullet whose cited sources the agent has already received, in this broadcast or earlier in the session, is dropped. Identical text from two modules is broadcast once, and so is a restatement: most of the same words in the same group, with no number, path or identifier the first one lacks.
- **Gist first, verbatim on demand** (fuzzy-trace theory). A long memory arrives as its opening clause plus, in brackets, every precise detail of the part cut away; `loci-guard recall --id ID` gives the full text. Rules, intentions and curated wiki bullets are never cut.
- **Chunking.** Three or more memories of one kind saved in the same place within minutes (one episode) arrive as one item: the strongest in full, the others by id.
- **Feedback.** What wins is reinforced and co-activated, marked as seen, and charged to the session budget.

```bash
loci-guard workspace billing/invoice.py   # what entering this file would broadcast, and why each item won or lost
loci-guard workspace                      # the same for session start
loci-guard context                        # just the session-start broadcast
```

The workspace command changes nothing, so you can use it to debug what the agent sees.

Measured on this repository (one simulated session opening all 49 source files, same memories): the version before these changes injected about 964 tokens and had to cut the `shield/` room's rules for lack of budget; the current one injects about 968 tokens and delivers twice as many items (16 vs 8), including all of those rules.

### Shield: Protects Your Code

Detects 20+ vulnerability patterns out of the box:

| Category | What it catches |
|---|---|
| **Credentials** | API keys, AWS keys, JWTs, private keys, connection strings, Firebase configs |
| **Injection** | SQL injection, command injection, XSS, eval(), shell=True |
| **Privacy** | Hardcoded emails, PII logging, CORS wildcards, IP addresses |
| **Quality** | Debug statements, broad exceptions, TODO/FIXME markers |

### Gate: Blocks Before It Ships

- **Pre-commit hook** — blocks commits with critical security issues
- **AI output scanner** — `loci-guard check` validates code from stdin before it touches your filesystem
- **PR gate** — `loci-guard gate --changed-since origin/main` checks only what the PR changed
- **Configurable severity** — `gate.block_severity` in the config: block on critical only, or be strict and block on medium+

### Claude Code Hooks: Enforcement, Not Suggestions

Context files are suggestions an agent can forget. Hooks run every time:

| Hook | When it fires | What it does |
|---|---|---|
| `PreToolUse` on `Write\|Edit\|MultiEdit` | Before Claude writes a file | Scans only the text being added. On a blocking finding it cancels the write, tells Claude which line to fix and how, and hangs a lesson in that room. |
| `PostToolUse` on `Read\|Write\|Edit\|MultiEdit` | After Claude enters a file | Shows the memories of that room that this session hasn't seen yet. |
| `SessionStart` | Startup, resume, `/clear`, **and after compaction** | Re-injects the atrium and the palace map, and re-opens the rooms so they are recalled again. |
| `PreCompact`, `SessionEnd` | Before compaction, at exit | Archives the transcript into the palace; before compaction it also tells the summarizer what to keep. |

`loci-guard setup` (or `loci-guard integrate --claude-hooks` alone) adds all of them to `.claude/settings.local.json`, keeping your existing settings. With `--command loci-guard` they go to the shared `.claude/settings.json` instead, for teams that have `loci-guard` on everyone's PATH. The hooks do nothing in projects without a `.loci/` folder.

### Agent Skill

[`skills/loci-guard/`](skills/loci-guard/SKILL.md) follows the [agentskills.io](https://agentskills.io/specification) format, read by Claude Code, Hermes Agent and others. `SKILL.md` (about 1,100 tokens, loaded whenever the skill activates) covers what every session needs: load context, check risky code, record rules, decisions and to-dos, verify before saying done. The detail lives in `references/` (memory, wiki, foresight) and is read only when a task needs it. `loci-guard setup` installs the folder into `.claude/skills/`; for a personal install, copy it into `~/.claude/skills/`. A test keeps the skill valid under the spec and fails if it mentions a command the CLI doesn't have, and [`evals/skill/`](evals/skill/README.md) checks how an agent reading it actually behaves in eight scenarios.

### False Positives

Silence a finding on one line with a comment:

```python
TEST_KEY = "AKIAIOSFODNN7EXAMPLE"  # loci-guard: ignore[SEC003]
# loci-guard: ignore-next-line
password = "fixture-password-for-tests"
```

`ignore` without a rule list silences every rule on that line.

### Integrations

| Tool | How it connects | What it gets |
|---|---|---|
| **Claude Code** | Hooks in `.claude/settings.local.json` + the skill in `.claude/skills/` | Insecure writes blocked; folder memories on entry; memory restored after compaction; chats archived |
| **Any MCP client** (Hermes, Codex, Cursor, Claude...) | `loci-guard mcp` | The same palace, rules, checks and foresight as tools |
| **Agents that read `AGENTS.md`** (Codex, Cursor, Copilot...) | `loci-guard integrate --agents` | Project context, security rules, how to call Loci Guard |
| **CI** | `loci-guard gate --changed-since origin/main` in any pipeline | Exit 1 when a pull request adds a blocking finding |

## Configuration

`.loci/config.yaml` (created by `setup`; every key is optional):

```yaml
brain:
  auto_learn: true          # derive rules from the scan
gate:
  block_severity: critical  # lowest severity that blocks: critical | high | medium
ignore_paths:               # extra paths scans skip, on top of .gitignore
  - node_modules
  - .venv
```

## CLI Commands

| Command | Description |
|---|---|
| `loci-guard setup [--no-ingest]` | Set up everything in one step |
| `loci-guard doctor` | Check the installation and say how to fix what fails |
| `loci-guard scan` | Full scan: learn patterns + detect issues |
| `loci-guard gate --staged` | Validate git-staged files |
| `loci-guard gate --changed-since REF` | Validate files changed since a git ref (CI) |
| `loci-guard gate --file path` | Validate specific files |
| `loci-guard check [FILE\|-] --filename x.py` | Check code before writing it (stdin with `-`) |
| `loci-guard context` | Print the session-start broadcast |
| `loci-guard workspace [PATH]` | Show the competition for the agent's context (dry run) |
| `loci-guard decide "desc" --reason "why" [--at PATH]` | Record a decision (in a room with `--at`) |
| `loci-guard constrain name rule [--at PATH]` | Add or update a constraint (in a room with `--at`) |
| `loci-guard forget name` | Remove an atrium constraint |
| `loci-guard remember "text" --at PATH [--kind] [--link ID]` | Hang a memory in a room |
| `loci-guard recall [PATH]` | Memories visible from a room |
| `loci-guard recall --search "words"` | Search memories and past chats |
| `loci-guard recall --id ID` | Full text of a memory that arrived shortened |
| `loci-guard recall --list \| --session ID \| --candidates` | Archived chats, one chat, candidate rules |
| `loci-guard map` | Rooms and how many memories of each kind they hold |
| `loci-guard astro [--dormant \| --stale \| --wake ID]` | Activity heat map and memory strength |
| `loci-guard wiki status \| compile [ROOM] \| done ROOM \| lint \| show ROOM` | Compiled wiki pages per room |
| `loci-guard link ID1 ID2` | Connect two memories |
| `loci-guard relocate [OLD NEW]` | After a rename: list rooms that no longer exist, or move their memories |
| `loci-guard promote ID` | Confirm a candidate rule |
| `loci-guard forget-memory ID` | Remove a palace memory |
| `loci-guard history ID\|NAME` | Every wording of a memory or brain constraint, with what each change dropped |
| `loci-guard intend "text" --when PATH \| --list \| --done ID` | Intentions that fire when a place is entered |
| `loci-guard foresee new \| record \| open \| resolve \| scores` | Foresight swarm with Brier-calibrated personas |
| `loci-guard mcp [--print-config]` | Share memory and checks with any MCP agent |
| `loci-guard integrate [--claude-hooks] [--agents]` | Claude Code hooks and the AGENTS.md block (no flags = both) |
| `loci-guard ingest [TRANSCRIPT]` | Archive past Claude Code chats |
| `loci-guard hook pretool \| enter-room \| session-start \| archive` | Entry points called by Claude Code hooks |
| `loci-guard install-hook` | Install git pre-commit hook |

## Why Not Just Use Snyk / Semgrep / ESLint?

Those tools scan for vulnerabilities. Loci Guard does that **plus**:

- **Learns your project** — auto-derives rules from your actual patterns
- **Remembers across sessions** — your AI never starts from zero
- **Works across agents** — one memory shared over MCP and `AGENTS.md`, enforced in Claude Code by hooks
- **Catches AI-specific problems** — duplicate code, ignored conventions, unnecessary dependencies
- **Pre-filters AI output** — catches issues before code hits your filesystem

## Contributing

PRs welcome. Run `pytest` before opening one; new detection rules need a test that shows a true positive and a near-miss that must not match. CI (`.github/workflows/tests.yml`) runs the tests on Linux, macOS and Windows with Python 3.10 and 3.12, and fails if Loci Guard finds issues in its own code.

## License

MIT
