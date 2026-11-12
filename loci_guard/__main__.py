"""`python -m loci_guard`: the same entry point as the `loci-guard` command.

Hooks and MCP configs use this form: it needs no generated launcher, which Windows
application-control policies may refuse to run because it is unsigned.
"""

from loci_guard.entry import main

main()
