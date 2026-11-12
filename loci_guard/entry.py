"""Console entry point: `loci-guard hook <name>` skips the CLI imports for speed."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "hook":
        from loci_guard import hooks

        sys.exit(hooks.run(sys.argv[2]))
    from loci_guard.cli import main as cli_main

    cli_main()
