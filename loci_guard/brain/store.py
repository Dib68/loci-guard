"""JSON state shared safely between processes (hooks, the MCP server, the CLI).

Every save takes a lock, re-reads what is on disk, replays only this process's changes on
top of it (a three-way merge against what it originally loaded), and replaces the file
atomically. Readers never see a half-written file, and concurrent writers don't erase each
other's additions. When two processes change the same record, the later save wins.
"""

from __future__ import annotations

import copy
import json
import os
import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

LOCK_TIMEOUT = 10.0       # give up waiting for the lock after this many seconds
STALE_LOCK = 30.0         # a lock older than this was left by a crashed process
REPLACE_RETRIES = 50      # Windows refuses to replace a file another process is reading
_MISSING = object()


class LockTimeout(OSError):
    pass


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        # PermissionError: on Windows a lock file that is being deleted can't be recreated yet.
        except (FileExistsError, PermissionError):
            try:
                if time.time() - lock.stat().st_mtime > STALE_LOCK:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise LockTimeout(f"timed out waiting for {lock}") from None
            time.sleep(random.uniform(0.002, 0.01))
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        _unlink(lock)


def _unlink(path: Path) -> None:
    for attempt in range(REPLACE_RETRIES):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:  # another process is looking at it right now
            time.sleep(0.005 * (attempt + 1))


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{random.randrange(1 << 30)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.01 * (attempt + 1))


def rebase(base: dict, ours: dict, theirs: dict) -> None:
    """Bring into `ours`, in place, what another process changed since `base`; keep our own changes."""
    for key in set(base) | set(theirs):
        b, t, o = base.get(key, _MISSING), theirs.get(key, _MISSING), ours.get(key, _MISSING)
        if t == b:
            continue                      # they didn't touch it: ours (changed or not) stands
        if o == b:                        # we didn't touch it: take theirs
            if t is _MISSING:
                ours.pop(key, None)
            else:
                ours[key] = t
        elif isinstance(o, dict) and isinstance(t, dict):
            rebase(b if isinstance(b, dict) else {}, o, t)
        elif isinstance(o, list) and isinstance(t, list) and _hashable(o) and _hashable(t):
            # Set-like lists (links, seen ids): keep both sides' additions, honour our removals.
            removed = set(b) - set(o) if isinstance(b, list) and _hashable(b) else set()
            o.extend(x for x in t if x not in o and x not in removed)
        elif isinstance(o, list) and isinstance(t, list) and _keyed(o) and _keyed(t):
            # Lists of records with an "id" (brain constraints, decisions): merge record by record.
            base_map = {r["id"]: r for r in b} if isinstance(b, list) and _keyed(b) else {}
            ours_map, theirs_map = {r["id"]: r for r in o}, {r["id"]: r for r in t}
            rebase(base_map, ours_map, theirs_map)
            order = [r["id"] for r in o] + [r["id"] for r in t if r["id"] not in {x["id"] for x in o}]
            o[:] = [ours_map[i] for i in order if i in ours_map]
        # otherwise both changed the same value: ours, the later save, wins


def _hashable(items: list) -> bool:
    return all(isinstance(x, (str, int, float, bool)) for x in items)


def _keyed(items: list) -> bool:
    return all(isinstance(x, dict) and "id" in x for x in items)


class JsonStore:
    """A JSON document loaded once and saved with lock + merge + atomic replace."""

    def __init__(self, path: Path, default: dict[str, Any]):
        self.path = path
        self.default = default
        self.data = self._read()
        self._base = copy.deepcopy(self.data)

    def _read(self) -> dict[str, Any]:
        for attempt in range(REPLACE_RETRIES):
            try:
                text = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return copy.deepcopy(self.default)
            except PermissionError:   # Windows, mid-replace
                if attempt == REPLACE_RETRIES - 1:
                    # Never fall back to empty here: a save would then erase what is on disk.
                    raise
                time.sleep(0.005 * (attempt + 1))
                continue
            try:
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    return {**copy.deepcopy(self.default), **loaded}
            except json.JSONDecodeError:
                pass
            # Corrupt: keep a copy instead of silently starting from empty and overwriting it.
            backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return copy.deepcopy(self.default)
        raise AssertionError("unreachable")

    def save(self, indent: int | None = 2) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path):
            theirs = self._read()
            rebase(self._base, self.data, theirs)
            atomic_write(self.path, json.dumps(self.data, indent=indent, ensure_ascii=False, default=str))
            self._base = copy.deepcopy(self.data)
