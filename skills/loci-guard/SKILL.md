---
name: loci-guard
description: Project memory, security guard and foresight swarm for coding work. Use at the start of work in a repo that has a .loci/ folder, before working in an area of the code, when the user states a rule, decision or something to do later or corrects you, when the user refers to past conversations, before a risky or wide change or when asked what could go wrong, before writing code that touches credentials, SQL, shell commands, HTML, logging or CORS, after finding a bug's root cause, and before saying work is done or committing.
license: MIT
compatibility: Requires Python 3.10+ with the loci-guard package (CLI or MCP server). Hooks are for Claude Code; other agents use MCP or AGENTS.md.
---

# Loci Guard

Loci Guard keeps the project's **memory** (rules, decisions, lessons and to-dos that survive sessions, compaction and agents), a **shield** (checks for secrets, injection, PII leaks and debug leftovers) and a **foresight swarm** (personas that forecast a change and are scored on being right).

- If `loci-guard` is blocked by the system, run `python -m loci_guard` with the same arguments.
- If the `loci-guard` MCP server is connected, its tools (`project_context`, `recall`, `search`, `remember`, `constrain`, `intend`, `check_code`, `foresee`...) do the same as the commands below. Other agents may share this memory, so what you record is what they will read.
- With the Claude Code hooks installed, insecure writes are blocked and each folder's memories reach you when you open a file there, in a block titled `Loci Guard, <folder>`. You don't need to fetch them by hand.

## When an item arrives shortened

To save context, long memories arrive as a gist ending in `...` followed by `[details]`, and memories saved together arrive as one item ending in `(+N from the same episode: #id #id)`. Before changing code such an item is about, read it whole: `loci-guard recall --id ID --id ID` (the item's own id is the last `#id`). Otherwise the gist is enough.

## At the start of a task

Run `loci-guard context` (skip it if the session already started with a "Project context (Loci Guard)" block) and follow it. Under **Rules**, CRITICAL and HIGH are hard rules; WARNING and INFO are the project's normal style. Under **Intentions** are things the user asked to be done when this place was reached: do them, then `loci-guard intend --done ID`.

No `.loci/` folder and the user wants it: `loci-guard setup`.

## Before writing risky code

When new code handles secrets, database queries, subprocesses, HTML, logs or HTTP headers, check it before saving:

```bash
loci-guard check --filename path/to/file.py - <<'EOF'
<the code you are about to write>
EOF
```

Exit code 1 means a blocking finding. Fix the code rather than suppressing it. Add `# loci-guard: ignore[RULE_ID]` only for a genuine false positive (for example a fake key in a test fixture), and tell the user you did.

## When the user states a rule, a decision or a to-do, or corrects you

Record it so the next session, and any other agent, knows without being told again. A correction ("no, don't do that") is a rule: first undo what it contradicts, then record it. Hang it in the folder it is about; leave it project-wide only when it truly applies everywhere.

```bash
loci-guard constrain payments_via_client "Call the payments API only through billing/client.py" --severity high --at billing/
loci-guard decide "Use PostgreSQL for persistence" --reason "Relational data, team expertise"
loci-guard remember "Stripe webhooks retry 3 times" --at billing/webhooks.py
loci-guard intend "Update CHANGELOG.md for billing changes" --when billing/   # fires on entering billing/ until done
```

Constraint names are short snake_case; reusing a name updates the rule. If a command reports `the new wording dropped: ...` and those details still hold, save again with them included.

Record only what a future session would get wrong without being told and can't read from the code or git history. Routine edits, renames, passing tests and your own plans not yet carried out are not memories: every memory is re-read in later sessions, so noise costs context forever. One sentence, with the reason when there is one.

## After finding a bug's root cause

Record the cause, not the symptom, where it lives:

```bash
loci-guard remember "Totals drifted because amounts were floats; use Decimal end to end" --kind lesson --at billing/
```

## When the user refers to the past

"As we decided", "last time", or you need the reason behind existing code: `loci-guard recall --search "<words>"` before guessing. Results marked `by association` are leads, not facts.

## Before saying the work is done

Run the tests. Every commit goes through `loci-guard gate --staged` first; fix anything BLOCKED. Don't commit on your own initiative: only when the user asked or the task includes it. Check `loci-guard intend --list` for to-dos on the folders you touched, and resolve any forecast whose outcome you now know (`loci-guard foresee open`).

## When to do the bigger jobs, and where the how-to is

- **Wiki pages need compiling** (the context says so): don't stop the current task for it. When the work is done, offer to compile them; if the user agrees, follow [references/wiki.md](references/wiki.md).
- **A risky or wide change**, or the user asks what could go wrong: offer a foresight forecast before writing the change, since it costs one subagent per persona. If the user agrees, follow [references/foresight.md](references/foresight.md).
- **Anything else about memory**: looking things up by hand, why something was or wasn't shown, merged or reworded memories, dormant and stale ones, past chats, candidate rules. See [references/memory.md](references/memory.md).

Ask the user before removing or relocating any memory, constraint or page: list what you would change and wait for a yes.

Rule ids (SEC = credentials and injection, PRV = privacy, QUA = quality) come with their fix in every finding; the full list is in [references/rules.md](references/rules.md).
