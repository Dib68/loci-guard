# Skill behaviour eval

`tests/test_skill_spec.py` checks that the skill is *valid*. This eval checks that an agent reading it *behaves* as intended, following the Superpowers practice of testing skills against concrete scenarios.

## Run it

1. Give a fresh agent, with no tools, the current `skills/loci-guard/SKILL.md` (not the files in `references/`) followed by [`scenarios.md`](scenarios.md). A small model is enough and keeps the cost low.
2. Save its answer as `results/<date>-<label>.md`.
3. Grade it:

```bash
python evals/skill/grade.py evals/skill/results/<date>-<label>.md
```

The criteria in `grade.py` were written before reading any answer. Don't loosen one to make a run pass; change the skill, or add a scenario.

## What the scenarios cover

| | Scenario | Expected |
|---|---|---|
| S1 | User states a rule about billing | `constrain ... --at billing/` |
| S2 | About to write `shell=True` with user input | check it or rewrite it; never `ignore[...]` |
| S3 | "Next time you touch billing..." | `intend ... --when billing/` |
| S4 | Bug fixed, about to commit | lesson in billing/, tests, `gate --staged` |
| S5 | "Why did we pick Postgres?" | `recall --search` |
| S6 | Wiki pages pending mid-task | don't compile now; offer at the end |
| S7 | "Clean up old memories" | ask before removing anything |
| S8 | Wide, security-sensitive change | offer a forecast before writing it |
| S9 | A shortened memory about the code you're changing | `recall --id` before changing it |
| S10 | User corrects an added dependency | undo it, record the correction as a rule |
| S11 | Trivial rename, tests pass | record nothing |
| S12 | Context says a room no longer exists | ask before `relocate` |

## Results so far (2026-11-12, Haiku, no tools)

| Skill version | Size loaded | Score |
|---|---|---|
| Single long SKILL.md | ~2,580 tokens | 14/14 (S1-S7) |
| Lean SKILL.md + references/ | ~1,100 tokens | 13/14: S6 opened `references/wiki.md` but didn't know to defer |
| Lean, with *when* rules kept in SKILL.md | ~1,180 tokens | 5/5 on the reruns S6-S8; S1-S5 unchanged in the text they test |

| Before v3 (baseline for S9-S12) | ~1,180 tokens | 20/21 on S1-S12: S10 saved the correction as a decision, not a rule |
| v3: shortened items, corrections, what not to record | ~1,290 tokens | 20/21: S9-S12 all pass and S9 also reads the episode's other ids, but S4 lost `gate --staged` (new "commit only if asked" line) |
| v3b: "every commit goes through the gate" | ~1,290 tokens | 6/6 on the reruns S4 and S8 |

Lesson: progressive disclosure works for *how* to do a job, but rules about *when* (and when not) to do it must stay in SKILL.md, because an agent decides whether to open a reference before it has read it.

Lesson from v3: a new restriction ("commit only if asked") can knock out a step tied to the same action. Phrase the step as part of the action ("every commit goes through the gate") so the two can't come apart. The rule-id table moved to `references/rules.md`, since every finding already names its rule and fix.
