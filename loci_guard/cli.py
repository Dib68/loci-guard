"""Loci Guard CLI: brain + shield for your codebase."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from loci_guard import hooks
from loci_guard.brain import archive
from loci_guard.brain.associative import AssociativeIndex
from loci_guard.brain.astrocyte import Astrocyte
from loci_guard.brain.memory import ProjectMemory
from loci_guard.brain.palace import ATRIUM, KINDS, MemoryPalace, format_memories
from loci_guard.brain.text import lost_details
from loci_guard.brain.wiki import Wiki
from loci_guard.brain.workspace import BROADCAST, room_broadcast, session_broadcast
from loci_guard.brain.rules import RuleEngine
from loci_guard.brain.scanner import ProjectScanner
from loci_guard.config import Config
from loci_guard.gate.validator import GateValidator
from loci_guard.integrations import claude_hooks
from loci_guard.shield.analyzer import ShieldAnalyzer

console = Console()
err_console = Console(stderr=True)

SEVERITY_STYLES = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
    "info": "dim",
}


def _rel(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return path


def _print_findings(findings: list[dict], root: Path, limit: int = 25) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Severity", width=10)
    table.add_column("Rule", width=8)
    table.add_column("Location", width=36)
    table.add_column("Message")
    for f in findings[:limit]:
        style = SEVERITY_STYLES.get(f["severity"], "")
        table.add_row(
            f"[{style}]{f['severity'].upper()}[/]",
            f["rule_id"],
            f"{_rel(f['file'], root)}:{f['line']}",
            f["message"],
        )
    console.print(table)
    if len(findings) > limit:
        console.print(f"  ... and {len(findings) - limit} more")


def _self_executable() -> str:
    """The Python running this Loci Guard; hooks call it as `python -m loci_guard`.

    Works outside this shell's PATH and inside virtualenvs, and avoids the generated
    launcher, which Windows application-control policies can block.
    """
    return sys.executable


def _read_stdin() -> str:
    # utf-8-sig drops the BOM that PowerShell pipes add; plain utf-8 would make json.loads fail.
    return sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")


@click.group()
@click.version_option(package_name="loci-guard")
@click.option("--root", default=".", help="Project root directory")
@click.pass_context
def main(ctx, root):
    """Loci Guard: AI-powered project brain + security shield."""
    ctx.ensure_object(dict)
    ctx.obj["root"] = Path(root).resolve()


@main.command()
@click.option("--json-output", is_flag=True, help="Output as JSON")
@click.pass_context
def scan(ctx, json_output):
    """Scan the project: learn patterns + detect issues."""
    root = ctx.obj["root"]
    config = Config(root)

    with console.status("[bold blue]Scanning project...", spinner="line"):
        scan_result = ProjectScanner(root).scan()
        memory = ProjectMemory(root)
        memory.store_scan(scan_result)
        learned = RuleEngine(memory).auto_learn(scan_result) if config.get("brain.auto_learn", True) else []
        shield_report = ShieldAnalyzer(root, extra_ignore=config.get("ignore_paths", [])).full_scan()

    if json_output:
        click.echo(json.dumps(
            {"scan": scan_result, "learned_rules": learned, "shield": shield_report},
            indent=2, default=str,
        ))
        return

    console.print("\n[bold cyan]PROJECT BRAIN[/]")
    stats = scan_result.get("stats", {})
    console.print(
        f"  Files: {stats.get('total_files', 0)}  |  Lines: {stats.get('total_lines', 0)}"
        f"  |  Size: {stats.get('total_size_kb', 0)} KB"
    )
    langs = scan_result.get("languages", {})
    if langs:
        console.print(f"  Languages: {', '.join(f'{k} ({v})' for k, v in langs.items())}")
    if learned:
        console.print(f"\n  [bold]Learned {len(learned)} rules:[/]")
        for r in learned:
            console.print(f"    [{r['severity'].upper()}] {r['name']}: {r['rule']}")

    console.print("\n[bold red]SECURITY SHIELD[/]")
    console.print(f"  {shield_report['summary']}")
    if shield_report["findings"]:
        _print_findings(shield_report["findings"], root)


@main.command()
@click.option("--staged", is_flag=True, help="Check git-staged files")
@click.option("--changed-since", "ref", help="Check files changed since a git ref (e.g. origin/main)")
@click.option("--file", "files", multiple=True, help="Specific files to check")
@click.pass_context
def gate(ctx, staged, ref, files):
    """Validate code against brain rules + shield checks. Exits 1 when blocked."""
    validator = GateValidator(ctx.obj["root"])
    if staged:
        result = validator.validate_staged()
    elif ref:
        result = validator.validate_changed_since(ref)
    elif files:
        result = validator.validate_files(list(files))
    else:
        raise click.UsageError("Specify --staged, --changed-since REF, or --file PATH")

    if result["passed"]:
        console.print("[bold green]PASSED[/] - no blocking issues.")
        if result["warnings"]:
            console.print(f"  {len(result['warnings'])} non-blocking warnings:")
            _print_findings(result["warnings"], validator.root, limit=10)
        return

    console.print("[bold red]BLOCKED[/] - fix these before committing:")
    for f in result["blocked_by"]:
        console.print(f"  [{f['severity'].upper()} {f['rule_id']}] {_rel(f['file'], validator.root)}:{f['line']} - {f['message']}")
        if f.get("fix_suggestion"):
            console.print(f"    Fix: {f['fix_suggestion']}")
    console.print("  False positive? Add `loci-guard: ignore[RULE_ID]` as a comment on that line.")
    sys.exit(1)


@main.command()
@click.argument("source", default="-")
@click.option("--filename", help="Name used to pick language rules when reading stdin (e.g. app.py)")
@click.option("--json-output", is_flag=True, help="Output as JSON")
@click.pass_context
def check(ctx, source, filename, json_output):
    """Check code before it's written. SOURCE is a file or '-' for stdin. Exits 1 when blocked."""
    if source == "-":
        content, name = _read_stdin(), filename or "<stdin>"
    else:
        content, name = Path(source).read_text(errors="ignore"), filename or source
    result = GateValidator(ctx.obj["root"]).validate_content(content, name)

    if json_output:
        click.echo(json.dumps(result, indent=2))
    elif result["findings"] or result["constraint_violations"]:
        if result["findings"]:
            _print_findings(result["findings"], ctx.obj["root"])
        for v in result["constraint_violations"]:
            console.print(f"  [{v['severity'].upper()}] {v['message']}")
    else:
        console.print("[bold green]Clean.[/]")
    if not result["passed"]:
        sys.exit(1)


@main.group()
def hook():
    """Entry points called by AI tool hooks (reads hook JSON from stdin)."""


@hook.command("pretool")
def hook_pretool():
    """PreToolUse: block Write/Edit calls that add insecure code."""
    sys.exit(hooks.run("pretool"))


@hook.command("session-start")
def hook_session_start():
    """SessionStart: re-inject project memory and the palace map (also after compaction)."""
    sys.exit(hooks.run("session-start"))


@hook.command("enter-room")
def hook_enter_room():
    """PostToolUse on file tools: broadcast what the workspace picks for the room entered."""
    sys.exit(hooks.run("enter-room"))


@hook.command("archive")
def hook_archive():
    """PreCompact / SessionEnd: archive the transcript into the palace."""
    sys.exit(hooks.run("archive"))


@main.command()
@click.pass_context
def context(ctx):
    """Print the project context a new session would receive."""
    click.echo(session_broadcast(ctx.obj["root"], None, None, dry_run=True).text or "No project context yet.")


@main.command()
@click.argument("path", required=False)
@click.option("--session", "session_id", default="preview", help="Session whose already-seen items to account for")
@click.pass_context
def workspace(ctx, path, session_id):
    """Show the competition for the agent's context, without changing anything.

    With PATH: what entering that file would broadcast. Without: the session-start broadcast.
    """
    root = ctx.obj["root"]
    if path:
        palace = MemoryPalace(root)
        result = room_broadcast(palace, Astrocyte(palace), session_id, path, dry_run=True)
    else:
        result = session_broadcast(root, None, None, dry_run=True)
    if not result.outcomes:
        console.print("Nothing proposed.")
        return
    table = Table(show_header=True, header_style="bold", title=f"capacity {result.capacity} chars")
    table.add_column("Result")
    table.add_column("Source")
    table.add_column("Group")
    table.add_column("Salience", justify="right")
    table.add_column("Item")
    order = {BROADCAST: 0}
    for o in sorted(result.outcomes, key=lambda o: (order.get(o.status, 1), not o.proposal.mandatory, -o.proposal.salience)):
        p = o.proposal
        style = "green" if o.status == BROADCAST else "dim"
        label = ("MANDATORY " if p.mandatory and o.status == BROADCAST else "") + o.status
        table.add_row(f"[{style}]{label}[/]", p.source, p.group, f"{p.salience:.1f}", p.text[:70])
    console.print(table)
    if result.text:
        console.print("\n[bold]Broadcast:[/]")
        click.echo(result.text)


def _anchors(palace: MemoryPalace, places: tuple[str, ...]) -> list[str]:
    anchors = []
    for place in places:
        anchor = palace.anchor_for(place)
        if anchor is None:
            raise click.BadParameter(f"{place} is outside the project", param_hint="--at")
        anchors.append(anchor)
    return anchors


@main.command()
@click.argument("description")
@click.option("--reason", required=True, help="Why this decision was made")
@click.option("--at", "places", multiple=True, help="Hang it in a room (file or folder) instead of the atrium")
@click.pass_context
def decide(ctx, description, reason, places):
    """Record an architectural decision."""
    root = ctx.obj["root"]
    if places:
        palace = MemoryPalace(root)
        m = palace.remember(f"{description} (reason: {reason})", _anchors(palace, places), kind="decision")
        console.print(f"[green]Decision hung in {', '.join(m['anchors'])}[/] #{m['id']}")
        _report_encoding(palace, m["id"])
        return
    ProjectMemory(root).add_decision(description, reason)
    console.print(f"[green]Decision recorded:[/] {description}")


@main.command()
@click.argument("name")
@click.argument("rule")
@click.option("--severity", default="warning", type=click.Choice(["critical", "high", "medium", "warning", "info"]))
@click.option("--at", "places", multiple=True, help="Hang it in a room (file or folder) instead of the atrium")
@click.pass_context
def constrain(ctx, name, rule, severity, places):
    """Add or update a project constraint."""
    root = ctx.obj["root"]
    if places:
        palace = MemoryPalace(root)
        m = palace.remember(
            f"[{severity.upper()}] {name}: {rule}", _anchors(palace, places), kind="constraint",
            memory_id=f"c-{name}",
        )
        console.print(f"[green]Constraint hung in {', '.join(m['anchors'])}[/] #{m['id']}")
        _report_encoding(palace, m["id"])
        return
    memory = ProjectMemory(root)
    memory.add_constraint(name, rule, severity)
    console.print(f"[green]Constraint saved:[/] [{severity.upper()}] {name}: {rule}")
    _report_lost(memory.last_lost, name)


@main.command()
@click.argument("text")
@click.option("--at", "places", multiple=True, help="Room(s) to hang it in; default is the atrium")
@click.option("--kind", default="note", type=click.Choice([k for k in KINDS if k not in ("candidate", "intention")]))
@click.option("--link", "links", multiple=True, help="Id of a memory to connect it to")
@click.pass_context
def remember(ctx, text, places, kind, links):
    """Hang a memory in the palace."""
    palace = MemoryPalace(ctx.obj["root"])
    m = palace.remember(text, _anchors(palace, places) if places else [ATRIUM], kind=kind)
    for other in links:
        if not palace.link(m["id"], other):
            console.print(f"[yellow]No memory #{other} to link[/]")
    console.print(f"[green]Remembered[/] #{m['id']} in {', '.join(a or '(atrium)' for a in m['anchors'])}")
    _report_encoding(palace, m["id"])


def _report_encoding(palace: MemoryPalace, memory_id: str) -> None:
    encoding, other, score = palace.last_encoding
    if encoding == "integrated":
        console.print(f"  merged into an existing memory ({score:.0%} similar); the newer wording was kept")
    elif encoding == "related":
        console.print(f"  kept separate from #{other} ({score:.0%} similar) and linked to it")
    _report_lost(palace.last_lost, memory_id)


def _report_lost(lost: list[str], ref: str) -> None:
    if lost:
        console.print(
            f"  [yellow]the new wording dropped:[/] {', '.join(lost)}. "
            f"The old wording is kept: `loci-guard history {ref}`"
        )


@main.command()
@click.argument("ref")
@click.pass_context
def history(ctx, ref):
    """Every wording a memory (id) or a brain constraint (name) has had, and what each change dropped."""
    root = ctx.obj["root"]
    versions = MemoryPalace(root).history(ref)
    if versions is None:
        constraint = next((c for c in ProjectMemory(root).data.get("constraints", []) if c["name"] == ref), None)
        if constraint is None:
            raise click.ClickException(f"No memory or constraint '{ref}'")
        versions = [{"text": v["rule"], "until": v.get("until")} for v in constraint.get("versions", [])]
        versions.append({"text": constraint["rule"], "until": None})
    for i, version in enumerate(versions):
        label = "current" if version["until"] is None else f"until {time.strftime('%Y-%m-%d %H:%M', time.localtime(version['until']))}"
        click.echo(f"v{i + 1} ({label}): {version['text']}")
        if i:
            previous = versions[i - 1]["text"]
            dropped, added = lost_details(previous, version["text"]), lost_details(version["text"], previous)
            if dropped:
                click.echo(f"    dropped: {', '.join(dropped)}")
            if added:
                click.echo(f"    added:   {', '.join(added)}")
    if len(versions) == 1:
        click.echo("(never reworded)")


@main.command()
@click.argument("place", required=False)
@click.option("--search", "query", help="Search memories and past chats for these words")
@click.option("--session", "session", help="Print an archived chat (id or unique prefix)")
@click.option("--list", "list_sessions", is_flag=True, help="List archived chats")
@click.option("--candidates", is_flag=True, help="Rules stated in past chats, awaiting confirmation")
@click.option("--all", "show_all", is_flag=True, help="Include dormant (faded) memories")
@click.pass_context
def recall(ctx, place, query, session, list_sessions, candidates, show_all):
    """Walk the palace: a room's memories, a search, or past chats."""
    palace = MemoryPalace(ctx.obj["root"])
    if session:
        text = archive.read_session(palace, session)
        if text is None:
            raise click.ClickException(f"No single archived chat matches '{session}'. Try `recall --list`.")
        click.echo(text)
    elif list_sessions:
        rows = sorted(palace.sessions.values(), key=lambda s: s.get("started_at") or "", reverse=True)
        for s in rows:
            rooms = ", ".join(r or "(atrium)" for r in s["rooms"])
            click.echo(f"{s['id'][:8]}  {(s.get('started_at') or '')[:10]}  {s['title'][:60]}  @{rooms}")
        if not rows:
            click.echo("No archived chats yet. Run `loci-guard ingest`.")
    elif candidates:
        waiting = palace.candidates()
        click.echo(format_memories(waiting, max_chars=10_000) or "No candidate rules.")
        if waiting:
            click.echo("\nConfirm one with `loci-guard promote ID`, drop it with `loci-guard forget-memory ID`.")
    elif query:
        found = AssociativeIndex(palace).recall(query)
        if found:
            click.echo("Memories (associative recall):")
            for m in found:
                where = ", ".join(a or "(atrium)" for a in m["anchors"])
                click.echo(f"- [{m['kind'].upper()}] {m['text']}  @{where} #{m['id']}  ({m['match']})")
        passages = archive.search_archive(palace, query)
        if passages:
            click.echo("\nPast chats:")
            for h in passages:
                click.echo(f"--- {h['session_id'][:8]} {h['title'][:60]} (line {h['line']})\n{h['snippet']}")
        if not found and not passages:
            click.echo("Nothing found.")
    else:
        astrocyte = Astrocyte(palace)
        memories = palace.recall(place)
        dormant = [m for m in memories if astrocyte.is_dormant(m)]
        shown = memories if show_all else [m for m in memories if m not in dormant]
        click.echo(format_memories(shown, max_chars=10_000) or f"No memories visible from {place or 'the atrium'}.")
        if dormant and not show_all:
            target = f"{place} " if place else ""
            click.echo(f"\n{len(dormant)} dormant memories here: `loci-guard recall {target}--all`")
        if shown:
            # Recalling on purpose is reconsolidation: it strengthens what was looked at.
            astrocyte.reinforce([m["id"] for m in shown])
            astrocyte.save()
            if place:
                astrocyte.stimulate(place, "recall")


@main.command()
@click.argument("text", required=False)
@click.option("--when", "places", multiple=True, help="Fire when a file or folder here is entered (default: next session start)")
@click.option("--list", "list_open", is_flag=True, help="List open intentions")
@click.option("--done", "done_id", help="Mark an intention as done")
@click.pass_context
def intend(ctx, text, places, list_open, done_id):
    """Prospective memory: "when I'm in X, do Y". It fires on entering X until marked done."""
    palace = MemoryPalace(ctx.obj["root"])
    if done_id:
        if palace.complete(done_id) is None:
            raise click.ClickException(f"No intention #{done_id}")
        console.print(f"[green]Done:[/] #{done_id}")
    elif list_open:
        rows = palace.intentions()
        for m in rows:
            click.echo(f"#{m['id']}  when in {', '.join(a or '(atrium)' for a in m['anchors'])}: {m['text']}")
        if not rows:
            click.echo("No open intentions.")
    elif text:
        m = palace.intend(text, _anchors(palace, places) if places else [ATRIUM])
        where = ", ".join(a or "next session start" for a in m["anchors"])
        console.print(f"[green]Intention #{m['id']}[/] will fire on: {where}")
    else:
        raise click.UsageError("Give the intention text, or --list, or --done ID")


@main.group()
def foresee():
    """Foresight swarm: personas from the palace forecast a change; outcomes calibrate them."""


@foresee.command("new")
@click.argument("description")
@click.option("--at", "places", multiple=True, help="Files or folders the change touches")
@click.pass_context
def foresee_new(ctx, description, places):
    """Print a work order: one persona per lens, each with its own memories, to forecast DESCRIPTION."""
    from loci_guard.brain.foresight import Foresight

    palace = MemoryPalace(ctx.obj["root"])
    _anchors(palace, places)  # rejects paths outside the project
    foresight = Foresight(palace)
    click.echo(foresight.work_order(foresight.begin(description, list(places))))


@foresee.command("record")
@click.argument("session_id")
@click.argument("source", default="-")
@click.pass_context
def foresee_record(ctx, session_id, source):
    """Record PREDICT lines (from SOURCE file or stdin) and show the calibrated consensus."""
    from loci_guard.brain.foresight import Foresight

    foresight = Foresight(MemoryPalace(ctx.obj["root"]))
    text = _read_stdin() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        recorded = foresight.record(session_id, text)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    if not recorded:
        raise click.ClickException("No lines like `PREDICT <persona> | <0-1> | <what> | <how we'll know>` found.")
    console.print(f"[green]Recorded {len(recorded)} prediction(s).[/] Calibrated consensus:")
    for p in foresight.consensus(session_id):
        console.print(f"  {p['weighted']:.0%}  #{p['id']}  {p['persona']} (said {p['probability']:.0%}, weight {p['weight']}): {p['claim']}")


@foresee.command("open")
@click.pass_context
def foresee_open(ctx):
    """Predictions still waiting for an outcome, with lessons recorded since that hint at one."""
    from loci_guard.brain.foresight import Foresight

    foresight = Foresight(MemoryPalace(ctx.obj["root"]))
    rows = foresight.open_predictions()
    for p in rows:
        click.echo(f"#{p['id']}  {foresight.weighted(p):.0%}  {p['persona']}: {p['claim']}  (check: {p['check']})")
        for m in foresight.evidence(p):
            click.echo(f"      evidence since: [{m['kind']}] {m['text']}")
    if not rows:
        click.echo("No open predictions.")


@foresee.command("resolve")
@click.argument("prediction_id")
@click.argument("outcome")
@click.pass_context
def foresee_resolve(ctx, prediction_id, outcome):
    """Say whether a prediction came true (yes/no, scores its persona) or can't be checked any more (void)."""
    from loci_guard.brain.foresight import Foresight, parse_outcome

    foresight = Foresight(MemoryPalace(ctx.obj["root"]))
    try:
        p = foresight.resolve(prediction_id, parse_outcome(outcome))
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    score = foresight.score(foresight._score_key(p["persona"]))
    state = f"weight {score['weight']}" if score["calibrated"] else f"{score['n']} resolved, needs 3 to calibrate"
    console.print(f"[green]Resolved #{prediction_id}[/] ({ {1.0: 'happened', 0.0: 'did not happen'}.get(p['outcome'], 'void, not scored') }); {p['persona']}: {state}")


@foresee.command("scores")
@click.pass_context
def foresee_scores(ctx):
    """Each persona's track record: Brier score (lower is better) and the weight it earns."""
    from loci_guard.brain.foresight import Foresight

    table = Table(show_header=True, header_style="bold")
    for column in ("Persona", "Resolved", "Brier", "Weight"):
        table.add_column(column)
    for key, s in Foresight(MemoryPalace(ctx.obj["root"])).scores().items():
        table.add_row(key, str(s["n"]), "-" if s["brier"] is None else f"{s['brier']:.3f}",
                      f"{s['weight']}" + ("" if s["calibrated"] else " (uncalibrated)"))
    console.print(table)


@main.command(name="map")
@click.pass_context
def map_cmd(ctx):
    """Draw the palace: which rooms hold memories, and of what kind."""
    rooms = MemoryPalace(ctx.obj["root"]).map()
    if not rooms:
        console.print("The palace is empty. Use `remember`, `decide --at`, or `ingest`.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Room", no_wrap=True)
    table.add_column("Total", justify="right")
    table.add_column("Kinds")
    for anchor, kinds in rooms.items():
        depth = anchor.rstrip("/").count("/") if anchor else 0
        label = "(atrium)" if anchor == ATRIUM else "  " * depth + anchor
        detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: KINDS.index(kv[0])))
        table.add_row(label, str(sum(kinds.values())), detail)
    console.print(table)


@main.command()
@click.option("--dormant", is_flag=True, help="List faded memories")
@click.option("--stale", is_flag=True, help="List memories whose files or folders no longer exist")
@click.option("--wake", "wake_id", help="Restore a dormant memory to full strength")
@click.pass_context
def astro(ctx, dormant, stale, wake_id):
    """The astrocyte: room activity, memory strength, and corridors it grew by itself."""
    palace = MemoryPalace(ctx.obj["root"])
    astrocyte = Astrocyte(palace)
    if wake_id:
        if not astrocyte.wake(wake_id):
            raise click.ClickException(f"No memory #{wake_id}")
        console.print(f"[green]#{wake_id} is awake again.[/]")
        return
    report = astrocyte.report()
    if dormant or stale:
        chosen = report["dormant"] if dormant else report["stale"]
        click.echo(format_memories(chosen, max_chars=10_000) or "None.")
        if stale and chosen:
            click.echo("\nThese hang in places that no longer exist. Move them with `remember --at`, or `forget-memory ID`.")
        return

    console.print("[bold]Room activity[/] (calcium, half-life 30 min)")
    if report["heat"]:
        top = report["heat"][0][1]
        for anchor, level in report["heat"][:15]:
            bar = "#" * max(1, round(20 * level / top))
            console.print(f"  {anchor or '(atrium)':<36} {bar} {level:.2f}")
    else:
        console.print("  quiet: no recent activity")
    console.print(
        f"\n[bold]Memories[/]: {len(report['active'])} active, {len(report['dormant'])} dormant, "
        f"{len(report['stale'])} stale; mean stability {report['mean_stability_days']:.0f} days; "
        f"{report['captures']} captured by strong events"
    )
    if report["auto_links"]:
        console.print(f"[bold]Corridors grown by co-activation[/]: {len(report['auto_links'])}")
        for key in report["auto_links"][-10:]:
            console.print(f"  #{key.replace('|', ' <-> #')}")


@main.group()
def wiki():
    """Compiled wiki: one page per room, written by the agent from the palace."""


def _room_arg(palace: MemoryPalace, value: str) -> str:
    if value.strip() in ("", ".", "(atrium)", "atrium"):
        return ATRIUM
    room = palace.anchor_for(value.rstrip("/\\") + "/")
    if room is None:
        raise click.BadParameter(f"{value} is outside the project")
    return room


@wiki.command("status")
@click.pass_context
def wiki_status(ctx):
    """Which pages need compiling, most surprising first."""
    rows = Wiki(MemoryPalace(ctx.obj["root"])).status()
    if not rows:
        console.print("No rooms with memories yet.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Room", no_wrap=True)
    table.add_column("Page")
    table.add_column("New", justify="right")
    table.add_column("Changed", justify="right")
    table.add_column("Removed", justify="right")
    table.add_column("Surprise", justify="right")
    for d in rows:
        table.add_row(
            d["room"] or "(atrium)", "yes" if d["page"] else "-", str(len(d["new"])),
            str(len(d["changed"])), str(len(d["removed"])), f"{d['surprise']:.0f}",
        )
    console.print(table)


@wiki.command("compile")
@click.argument("room", required=False)
@click.pass_context
def wiki_compile(ctx, room):
    """Print the work order for the agent: what each out-of-date page must absorb."""
    palace = MemoryPalace(ctx.obj["root"])
    w = Wiki(palace)
    rooms = [_room_arg(palace, room)] if room else [d["room"] for d in w.pending()]
    if not rooms:
        click.echo("The wiki is up to date: no room has material its page doesn't account for.")
        return
    click.echo("\n\n".join(w.work_order(r) for r in rooms))


@wiki.command("done")
@click.argument("room")
@click.pass_context
def wiki_done(ctx, room):
    """Check a page the agent wrote, stamp its sources, refresh the index."""
    palace = MemoryPalace(ctx.obj["root"])
    result = Wiki(palace).finish(_room_arg(palace, room))
    for warning in result["warnings"]:
        console.print(f"[yellow]warning:[/] {warning}")
    if not result["ok"]:
        for error in result["errors"]:
            console.print(f"[red]error:[/] {error}")
        sys.exit(1)
    console.print("[green]Page compiled and stamped.[/]")


@wiki.command("lint")
@click.pass_context
def wiki_lint(ctx):
    """Find dangling citations, broken links, missing files, orphan and stale pages."""
    issues = Wiki(MemoryPalace(ctx.obj["root"])).lint()
    if not issues:
        console.print("[green]Wiki is clean.[/]")
        return
    for issue in issues:
        style = "red" if issue["level"] == "error" else "yellow"
        console.print(f"[{style}]{issue['level']}[/] {issue['room']}: {issue['message']}")
    if any(i["level"] == "error" for i in issues):
        sys.exit(1)


@wiki.command("show")
@click.argument("room")
@click.pass_context
def wiki_show(ctx, room):
    """Print a room's compiled page."""
    palace = MemoryPalace(ctx.obj["root"])
    page = Wiki(palace).read_page(_room_arg(palace, room))
    if page is None:
        raise click.ClickException(f"No page for {room}. Run `loci-guard wiki compile {room}`.")
    click.echo(page["body"])


@main.command()
@click.argument("first")
@click.argument("second")
@click.pass_context
def link(ctx, first, second):
    """Open a corridor between two memories so recalling one brings the other."""
    if MemoryPalace(ctx.obj["root"]).link(first, second):
        console.print(f"[green]Linked[/] #{first} <-> #{second}")
    else:
        raise click.ClickException("Both ids must exist and differ.")


@main.command()
@click.argument("memory_id")
@click.option("--kind", default="constraint", type=click.Choice(["constraint", "decision", "note"]))
@click.pass_context
def promote(ctx, memory_id, kind):
    """Confirm a candidate rule (or change any memory's kind)."""
    m = MemoryPalace(ctx.obj["root"]).promote(memory_id, kind)
    if m is None:
        raise click.ClickException(f"No memory #{memory_id}")
    console.print(f"[green]#{memory_id} is now a {kind}:[/] {m['text']}")


@main.command(name="forget-memory")
@click.argument("memory_id")
@click.pass_context
def forget_memory(ctx, memory_id):
    """Remove a memory from the palace."""
    if not MemoryPalace(ctx.obj["root"]).forget(memory_id):
        raise click.ClickException(f"No memory #{memory_id}")
    console.print(f"[green]Forgot[/] #{memory_id}")


@main.command()
@click.argument("transcript", required=False)
@click.pass_context
def ingest(ctx, transcript):
    """Archive Claude Code chats for this project into the palace (all of them by default)."""
    palace = MemoryPalace(ctx.obj["root"])
    sessions = [archive.ingest(palace, transcript)] if transcript else archive.ingest_all(palace)
    sessions = [s for s in sessions if s]
    if not sessions:
        console.print(f"No chats found in {archive.transcripts_dir(palace.root)}")
        return
    console.print(f"[green]Archived {len(sessions)} chat(s)[/] (secrets redacted, stored in .loci/archive/, git-ignored)")
    waiting = len(palace.candidates())
    if waiting:
        console.print(f"{waiting} candidate rule(s) found: `loci-guard recall --candidates`")


@main.command()
@click.argument("name")
@click.pass_context
def forget(ctx, name):
    """Remove a constraint by name."""
    if ProjectMemory(ctx.obj["root"]).remove_constraint(name):
        console.print(f"[green]Removed constraint:[/] {name}")
    else:
        console.print(f"[yellow]No constraint named[/] {name}")


@main.command()
@click.option("--claude-hooks", "hooks", is_flag=True, help="Install the Claude Code hooks")
@click.option("--agents", is_flag=True, help="Write the Loci Guard block in AGENTS.md (Codex, Cursor, Copilot, Hermes...)")
@click.option("--command", "executable", help="Executable the hooks call (default: this loci-guard, by full path)")
@click.pass_context
def integrate(ctx, hooks, agents, executable):
    """Connect AI tools: Claude Code hooks and an AGENTS.md block. No flags = both."""
    from loci_guard.integrations import agents_md

    root = ctx.obj["root"]
    both = not (hooks or agents)
    if hooks or both:
        console.print(f"[green]Updated:[/] {_rel(str(claude_hooks.install(root, executable or _self_executable())), root)}")
    if agents or both:
        console.print(f"[green]Updated:[/] {_rel(str(agents_md.write(root)), root)}")


@main.command()
@click.option("--no-ingest", is_flag=True, help="Don't archive past Claude Code chats")
@click.option("--command", "executable", help="Executable the hooks call (default: this loci-guard, by full path)")
@click.pass_context
def setup(ctx, no_ingest, executable):
    """Set up everything in one step: config, scan, chat archive, Claude Code hooks, skill."""
    from loci_guard.integrations import setup as installer

    root = ctx.obj["root"]
    config = Config(root)
    fresh = not config.config_path.exists()
    if fresh:
        config.save()
    console.print(f"[green]OK[/]  config {'created' if fresh else 'kept'}: {_rel(str(config.config_path), root)}")

    with console.status("Scanning the project...", spinner="line"):
        scan_result = ProjectScanner(root).scan()
        memory = ProjectMemory(root)
        memory.store_scan(scan_result)
        learned = RuleEngine(memory).auto_learn(scan_result) if config.get("brain.auto_learn", True) else []
    console.print(f"[green]OK[/]  scanned {scan_result['stats']['total_files']} files, learned {len(learned)} rules")

    if not no_ingest:
        with console.status("Archiving past chats...", spinner="line"):
            sessions = archive.ingest_all(MemoryPalace(root))
        console.print(f"[green]OK[/]  archived {len(sessions)} past chat(s), secrets redacted")

    settings = claude_hooks.install(root, executable or _self_executable())
    console.print(f"[green]OK[/]  Claude Code hooks in {_rel(str(settings), root)}")
    if settings.name == "settings.local.json" and installer.ensure_gitignored(root, installer.LOCAL_SETTINGS):
        console.print(f"[green]OK[/]  added {installer.LOCAL_SETTINGS} to .gitignore (it holds a machine-specific path)")

    skill = installer.install_skill(root)
    if skill:
        console.print(f"[green]OK[/]  skill installed: {_rel(str(skill), root)}")
    else:
        console.print("[yellow]--[/]  SKILL.md not found in this installation; copy skills/loci-guard/ by hand")

    failed = [c for c in installer.doctor(root) if not c.ok]
    if failed:
        console.print(f"\n[yellow]{len(failed)} check(s) need attention:[/] run `loci-guard doctor`")
    else:
        console.print("\n[bold green]Ready.[/] The hooks take effect in Claude Code right away; `loci-guard doctor` re-checks anytime.")


@main.command()
@click.option("--print-config", is_flag=True, help="Print the mcpServers entry to paste into an MCP client")
@click.pass_context
def mcp(ctx, print_config):
    """Serve this project's memory and checks over MCP (stdio), for any MCP-capable agent."""
    from loci_guard.mcp_server import Server, client_config

    root = ctx.obj["root"]
    if print_config:
        click.echo(json.dumps(client_config(_self_executable(), root), indent=2))
        return
    Server(root).serve()


@main.command()
@click.pass_context
def doctor(ctx):
    """Check the installation: state files, hooks, their executable and speed, the skill."""
    from loci_guard.integrations import setup as installer

    checks = installer.doctor(ctx.obj["root"])
    for check in checks:
        mark = "[green]OK  [/]" if check.ok else "[red]FAIL[/]"
        console.print(f"{mark} {check.name}: {check.detail}")
        if not check.ok and check.fix:
            console.print(f"     fix: {check.fix}")
    failed = sum(not c.ok for c in checks)
    console.print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    if failed:
        sys.exit(1)


@main.command(name="install-hook")
@click.pass_context
def install_hook(ctx):
    """Install Loci Guard as a git pre-commit hook."""
    if GateValidator(ctx.obj["root"]).install_git_hook():
        console.print("[green]Git pre-commit hook installed.[/]")
    else:
        console.print("[red].git/hooks not found. Is this a git repo?[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
