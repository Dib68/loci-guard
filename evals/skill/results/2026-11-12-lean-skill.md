## S1

```bash
codesentinel constrain payments_via_client "Call the payments API only through billing/client.py" --severity high --at billing/
```

The rule is high severity and hangs on `billing/`, where `client.py` lives, so it surfaces when someone works there.

## S2

```bash
codesentinel check --filename tools/run.py - <<'EOF'
def run(cmd):
    return subprocess.run(cmd, shell=True)
EOF
codesentinel check --filename tools/run.py - <<'EOF'
import subprocess

def run(argv):
    return subprocess.run(argv, check=True)
EOF
```

The first check should be blocked, so I would not save it; the second is the fixed version, which passes the command as an argument list with no shell, and the caller must still allowlist what `argv` may contain.

## S3

```bash
codesentinel intend "Update CHANGELOG.md for billing changes" --when billing/
```

This to-do fires whenever someone enters `billing/` until it is marked done.

## S4

```bash
codesentinel remember "Totals drifted because amounts were floats; use Decimal end to end" --kind lesson --at billing/
# run the project's test suite here
git add billing/totals.py
codesentinel gate --staged
codesentinel intend --list
codesentinel foresee open
git commit -m "Use Decimal for billing totals to stop float drift

Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>"
```

I would record the root cause, test, stage, and clear the gate and open to-dos and forecasts before reporting the fix and committing.

## S5

```bash
codesentinel recall --search "Postgres"
codesentinel recall --search "postgres database persistence"
```

I would search past decisions before answering, and treat any result marked `by association` as a lead rather than a confirmed reason.

## S6

```bash
OPEN references/wiki.md
```

I would read the wiki reference to see how the pending compiling should be handled, then carry on with the feature work.

## S7

```bash
OPEN references/memory.md
```

ASK: Which old memories should I remove? I can list the dormant and stale candidates first so you can pick them, since nothing gets deleted without your approval.
