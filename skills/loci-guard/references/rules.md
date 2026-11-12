# Shield rule ids

Read this when you need to know what a rule id covers, or to suppress a genuine false positive with `# loci-guard: ignore[RULE_ID]` (tell the user when you do).

| Prefix | Area | Examples |
|---|---|---|
| SEC001-007 | Credentials | API keys, passwords, AWS keys, private keys, JWTs, DB URLs with passwords |
| SEC010-014 | Injection | SQL built with f-strings, shell commands with user input, `innerHTML`, `eval`, `shell=True` |
| PRV001-004 | Privacy | Hardcoded emails or IPs, PII in logs, `Access-Control-Allow-Origin: *` |
| QUA001-003 | Quality | TODO/FIXME, leftover debug prints, bare `except:` |

Credential rules also search strings and comments; rules about code (`eval`, SQL, shell, debug prints, bare `except`) ignore matches inside strings and comments of `.py` files. `# loci-guard: ignore-file` works only for files already on disk, never for code passed to `check` or the write hook.
