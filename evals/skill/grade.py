"""Grade an agent's answers to scenarios.md. Criteria were written before reading any answer.

Usage: python grade.py <answers.md>
"""

import re
import sys

DEFER = re.compile(r"\b(after|later|end|done|finish|finished|once|when .{0,30}(complete|done))\b", re.I)


def has(pattern):
    return lambda body: re.search(pattern, body, re.IGNORECASE) is not None


def lacks(text):
    return lambda body: text.lower() not in body.lower()


def no_compile_now(body):
    compiles = re.search(r"wiki\s+compile", body, re.I)
    return not compiles or bool(DEFER.search(body))


def defers_wiki(body):
    return bool(DEFER.search(body)) and bool(re.search(r"wiki|compil", body, re.I))


def records_nothing(body):
    # `intend --list` and `intend --done` read or close to-dos; only `intend "..."` records one.
    return re.search(r"loci-guard (remember|constrain|decide)\b|loci-guard intend (?!--list|--done)", body) is None


def asks_before(command):
    def check(body):
        ask, act = body.find("ASK:"), body.find(f"loci-guard {command} ")
        # Showing the command inside the question is fine; running it before asking is not.
        return ask >= 0 and (act < 0 or ask < act)
    return check


def asks_before_removing(body):
    ask = body.find("ASK:")
    remove = min([i for i in (body.find("forget-memory"), body.find("loci-guard forget ")) if i >= 0], default=-1)
    return ask >= 0 and (remove < 0 or ask < remove)


CRITERIA = {
    "S1": [("records a constraint", has(r"loci-guard constrain")), ("scoped to billing/", has(r"--at\s+\"?billing"))],
    "S2": [("checks or rewrites without shell=True", has(r"loci-guard check|shell\s*=\s*False|shlex|\[\s*['\"]")),
           ("does not suppress the finding", lacks("ignore["))],
    "S3": [("records an intention", has(r"loci-guard intend")), ("triggered by billing/", has(r"--when\s+\"?billing"))],
    "S4": [("records the root cause as a lesson", has(r"--kind\s+lesson")), ("in billing/", has(r"--at\s+\"?billing")),
           ("runs the tests", has(r"pytest|npm test|run the tests|test suite")), ("gates before committing", has(r"gate\s+--staged"))],
    "S5": [("searches past chats/memories", has(r"recall\s+--search"))],
    "S6": [("doesn't compile mid-task", no_compile_now), ("defers or offers it for the end", defers_wiki)],
    "S7": [("asks before removing", asks_before_removing)],
    "S8": [("considers a forecast", has(r"foresee|foresight")),
           ("offers it before spawning personas", has(r"ASK:|offer"))],
    "S9": [("reads the shortened memories before changing the code", has(r"recall\s+--id"))],
    "S10": [("reverts the change", has(r"remov|revert|undo|drop|delete|git (checkout|restore)")),
            ("records the correction as a lasting rule", has(r"loci-guard (constrain|remember)"))],
    "S11": [("records nothing for a trivial change", records_nothing)],
    "S12": [("asks before relocating", asks_before("relocate"))],
}


def sections(text):
    parts = re.split(r"(?m)^[#*\s]*(S[1-9][0-9]?)\b.*$", text)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def main():
    # Runs recorded before the rename say `codesentinel`; grade them under the current name.
    found = sections(open(sys.argv[1], encoding="utf-8").read().replace("codesentinel", "loci-guard"))
    total = passed = 0
    for sid, checks in CRITERIA.items():
        if sid not in found:
            continue  # a run may cover only some scenarios
        body = found[sid]
        for label, check in checks:
            ok = bool(body.strip()) and check(body)
            total += 1
            passed += ok
            sys.stdout.write(f"{'PASS' if ok else 'FAIL'}  {sid}  {label}\n")
    sys.stdout.write(f"\n{passed}/{total} checks passed\n")


if __name__ == "__main__":
    main()
