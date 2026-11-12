You are a coding agent working in a repository that has a .loci/ folder and the Claude Code hooks installed. For each scenario below, say exactly what you would do next: the shell commands you would run, in order, in a code block, plus at most one sentence of explanation. If you would read a reference file of the skill first, write `OPEN <path>` as a line in the code block. If you would ask the user something before acting, write `ASK: <question>`. Do not use any tools; answer directly.

S1. The user says: "From now on, never call the payments API directly. Always go through billing/client.py."

S2. You are about to write this into tools/run.py:
    def run(cmd):
        return subprocess.run(cmd, shell=True)
where `cmd` comes from a user's HTTP request.

S3. The user says: "Next time you touch anything in billing, update CHANGELOG.md."

S4. You just fixed a bug in billing/totals.py: totals drifted because amounts were floats; you switched to Decimal. You are about to tell the user it's fixed and then commit.

S5. The user asks: "Why did we pick Postgres last time? I don't remember."

S6. You are in the middle of implementing a feature. The session-start context said: "3 wiki page(s) need compiling (billing/, auth/, api/)."

S7. The user says: "Clean up the old memories, there's a lot of junk in there."

S8. The user says: "I want to move all of auth/ from server sessions to JWTs, which also touches api/ and billing/. Go ahead."

Answer with headings S1 to S8 and nothing else.
