### S6

```
# No CodeSentinel command now: keep implementing the feature.
# Before saying it is done: run the tests, then `codesentinel gate --staged`, `codesentinel intend --list` and `codesentinel foresee open`.
```

Wiki compiling waits until the feature work is finished, and then I would offer it to you rather than start it.

### S7

```
OPEN references/memory.md
codesentinel context
```

ASK: Should I list the memories I would remove as junk and remove only the ones you confirm?

I would not remove anything until you approve a specific list.

### S8

```
codesentinel decide "Move auth/ from server sessions to JWTs" --reason "User request; also touches api/ and billing/"
```

ASK: Before I write the change, may I run a foresight forecast on it, which costs one subagent per persona?

I would record your decision now, then offer the forecast before writing any auth code, since this is a risky, wide change that touches credentials, and only open `references/foresight.md` if you agree.
