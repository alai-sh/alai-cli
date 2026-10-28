# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection list``: the collections in the collections directory, whatever installed them, as ``uv pip list``
lists an environment's packages, each compared with ``alai.lock`` when the project has one. The design is in
``design/list.md``."""

from __future__ import annotations

import json
import os
import sys

from alai_core.semver import Version

from . import _output
from ._common import ConfigError, Record, home_path
from ._config import Settings
from ._install import Installed, installed, namespace_links
from ._lock import IndexSource, Lock, LockedCollection, LockedRoot, lock_path, read_lock
from ._manifest import parse_manifest
from ._output import BLUE, BOLD, CYAN, DIM, GREEN, RED, YELLOW, status, styled, terminal_width

LOCKED = "locked"
"""Installed as ``alai.lock`` has it."""
DIFFERENT = "different"
"""Installed, but at another version, from another source, or changed at its source since."""
UNLOCKED = "unlocked"
"""Installed, but ``alai.lock`` doesn't list it."""
MISSING = "missing"
"""Locked, and installed by a plain ``alai sync``, but not installed."""

_MARKERS = {LOCKED: ("✓", GREEN), UNLOCKED: ("?", YELLOW), DIFFERENT: ("≠", YELLOW), MISSING: ("✗", RED)}
_LEGEND = (
    (LOCKED, "as locked"),
    (UNLOCKED, "not in alai.lock"),
    (DIFFERENT, "not as locked"),
    (MISSING, "locked, not installed"),
)


class Row(Record):
    """A collection installed, or locked and missing, or a namespace that's a link, as one line of the list."""

    __slots__ = ("editable", "lock_source", "lock_version", "name", "note", "path", "source", "status", "version")

    def __init__(
        self,
        name: str,
        version: Version | None,
        path: str | None,
        *,
        source: dict[str, object] | None = None,
        editable: bool = False,
        status: str | None = None,
        note: str = "",
        lock_version: Version | None = None,
        lock_source: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        """``ns.name``, or ``ns.*`` for a namespace that's a link."""
        self.version = version
        self.path = path
        self.source = source
        """Where it was installed from, as its install record has it, or ``{"directory": ...}`` for a link; None for
        one from an index, which has no record."""
        self.editable = editable
        self.status = status
        """``LOCKED``, ``DIFFERENT``, ``UNLOCKED`` or ``MISSING``; None when there's no ``alai.lock``."""
        self.note = note
        """How it differs from ``alai.lock``, or an empty string."""
        self.lock_version = lock_version
        self.lock_source = lock_source


def list_collections(settings: Settings, *, output_format: str = "text") -> None:
    from ._collection import target, using

    cdir = target(settings)
    path = lock_path(settings.project.root) if settings.project.config_file is not None else None
    lock = read_lock(path) if path is not None else None
    if output_format == "text":
        # Always, unlike collection install's: what's listed is only what's in this directory, so a listing of the
        # wrong one looks like an empty or partial one.
        _ = using(cdir, always=True)
        if lock is None:
            status(styled("No alai.lock to compare with", DIM))
    rows = collect(settings, cdir, lock)
    if output_format == "json":
        _print_json(rows, cdir, path if lock is not None else None)
        return
    if not rows:
        status(styled("No collections installed", DIM))
        return
    _Renderer(compared=lock is not None).listing(rows)
    if any(r.status not in (None, LOCKED) for r in rows):
        status(
            styled("hint: ", DIM)
            + styled("alai sync", BOLD)
            + styled(" installs what alai.lock lists and removes the rest", DIM)
        )


def collect(settings: Settings, cdir: str, lock: Lock | None) -> list[Row]:
    """Every collection in ``cdir`` and every namespace link there, each compared with ``lock`` if given, and the
    locked collections a plain ``alai sync`` would install that aren't there, by name."""
    from ._sync import DEFAULT_GROUPS, expected

    present = installed(cdir)
    rows = [_installed_row(c) for c in present.values()]
    rows.extend(_link_row(namespace, link) for namespace, link in namespace_links(cdir).items())
    if lock is None:
        return sorted(rows, key=lambda r: r.name)

    entries: dict[str, LockedCollection | LockedRoot] = {c.name: c for c in lock.collections}
    if lock.root is not None:
        entries[lock.root.name] = lock.root
    links, _, records = expected(settings, lock.collections, lock.root)
    link_to = {name: path for name, _, path in links}
    for row in rows:
        entry = entries.get(row.name)
        if entry is None:
            row.status = UNLOCKED
            row.note = "not in alai.lock"
            continue
        link = row.name in link_to
        want: dict[str, object] | None = (
            {"directory": os.path.abspath(link_to[row.name])} if link else _record_source(records.get(row.name))
        )
        row.lock_version = entry.version
        row.lock_source = _lock_table(entry)
        same_source = row.editable == link and row.source == want
        # A link's target is read every time, so only a copy can have changed at its source since it was made.
        same = same_source and (link or present[row.name].record == records.get(row.name))
        if same and row.version == entry.version:
            row.status = LOCKED
        else:
            row.status = DIFFERENT
            row.note = _difference(settings, row, entry, want, link=link, same_source=same_source)

    groups = [g for g in DEFAULT_GROUPS if g in parse_manifest(lock.manifest).groups]
    synced = [*lock.select(groups), *([lock.root] if lock.root is not None else [])]
    rows.extend(
        Row(
            entry.name,
            None,
            None,
            status=MISSING,
            note=f"alai.lock has {entry.version}, not installed",
            lock_version=entry.version,
            lock_source=_lock_table(entry),
        )
        for entry in synced
        if entry.name not in present
    )
    return sorted(rows, key=lambda r: r.name)


def _installed_row(collection: Installed) -> Row:
    if os.path.islink(collection.path):
        target = os.readlink(collection.path)
        version = collection.version if collection.version is not None else _source_version(collection.path)
        return Row(collection.name, version, collection.path, source={"directory": target}, editable=True)
    return Row(collection.name, collection.version, collection.path, source=_record_source(collection.record))


def _link_row(namespace: str, path: str) -> Row:
    return Row(f"{namespace}.*", None, path, source={"directory": os.readlink(path)})


def _source_version(path: str) -> Version | None:
    """The version of the collection an editable link points at, from its ``galaxy.yml`` or ``MANIFEST.json``, or None
    when that can't be read, such as for a broken link."""
    from ._galaxy import read_collection_dir

    try:
        return read_collection_dir(path).version
    except (ConfigError, OSError):
        return None


def _record_source(record: dict[str, object] | None) -> dict[str, object] | None:
    source = record.get("source") if record is not None else None
    return source if isinstance(source, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _lock_table(entry: LockedCollection | LockedRoot) -> dict[str, str]:
    return {"editable": entry.path} if isinstance(entry, LockedRoot) else entry.source.table()


def _difference(
    settings: Settings,
    row: Row,
    entry: LockedCollection | LockedRoot,
    want: dict[str, object] | None,
    *,
    link: bool,
    same_source: bool,
) -> str:
    """What ``alai.lock`` has that's different from what's installed: its version, its source, or both; or, when
    they're the same, that the source changed after it was copied."""
    version = str(entry.version) if row.version != entry.version else ""
    if same_source:
        return f"alai.lock has {version}" if version else "its source has changed since it was installed"
    if want is not None and row.source is not None and _same_repository(want, row.source) and not link:
        where = f"at {_short(str(want['commit']))}"
    elif link:
        where = f"from {home_path(str(want['directory']))}, editable" if want is not None else ""
    elif want is not None:
        where = f"from {_source_text(want)}"
    else:
        where = f"from {_index_text(settings, entry)}"
    return f"alai.lock has {version} {where}" if version else f"alai.lock has it {where}"


def _same_repository(want: dict[str, object], have: dict[str, object]) -> bool:
    """Whether both are git sources from the same repository and directory in it, so only the commit differs."""
    return "git" in want and all(want.get(k) == have.get(k) for k in ("git", "subdirectory"))


def _index_text(settings: Settings, entry: LockedCollection | LockedRoot) -> str:
    source = entry.source if isinstance(entry, LockedCollection) else None
    if not isinstance(source, IndexSource):
        return str(source)
    name = next((i.name for i in settings.indexes if i.url == source.url), None)
    return f"index '{name}'" if name is not None else source.url


def _source_text(source: dict[str, object]) -> str:
    """A source as the list shows it: a directory or tarball's path, a tarball's URL, or ``git+<url>@<commit>``
    with the commit shortened, as git shortens one."""
    if "git" in source:
        subdirectory = f"#subdirectory={source['subdirectory']}" if source.get("subdirectory") else ""
        return f"git+{source['git']}@{_short(str(source.get('commit', '')))}{subdirectory}"
    if "url" in source:
        return str(source["url"])
    return home_path(str(source.get("directory") or source.get("path") or ""))


def _short(commit: str) -> str:
    return commit[:7]


def _print_json(rows: list[Row], cdir: str, path: str | None) -> None:
    """The rows as facts: each collection's version, path and source, and, when there's a lock, how it compares; the
    namespaces that are links separately, as they aren't collections."""

    def collection(row: Row) -> dict[str, object]:
        lock: dict[str, object] | None = None
        if row.status is not None:
            lock = {
                "status": row.status,
                "version": str(row.lock_version) if row.lock_version is not None else None,
                "source": row.lock_source,
            }
        return {
            "name": row.name,
            "version": str(row.version) if row.version is not None else None,
            "path": os.path.abspath(row.path) if row.path is not None else None,
            "source": row.source,
            "editable": row.editable,
            "lock": lock,
        }

    result = {
        "v": 1,
        "collections_dir": os.path.abspath(cdir),
        "lock": os.path.abspath(path) if path is not None else None,
        "collections": [collection(r) for r in rows if not r.name.endswith(".*")],
        "namespace_links": [
            {"namespace": r.name[:-2], "path": os.path.abspath(str(r.path)), "target": (r.source or {})["directory"]}
            for r in rows
            if r.name.endswith(".*")
        ],
    }
    json.dump(result, sys.stdout, indent=2)
    print()


Piece = tuple[str, tuple[str, ...]]
"""Some text and the styles it's shown in."""


class _Renderer:
    """The text output: a row per collection, as ``alai find`` draws its rows, coloured when stdout is a terminal (or
    ``--color`` says so). On a terminal, a row too wide for it puts its source on a line of its own, and anything still
    too wide is cut; piped, every row is one line, whole."""

    def __init__(self, *, compared: bool) -> None:
        self.compared = compared
        self.terminal = sys.stdout.isatty()
        self.width = terminal_width()

    def s(self, text: str, *codes: str) -> str:
        return _output.styled(text, *codes, stdout=True)

    def listing(self, rows: list[Row]) -> None:
        name_width = max(len(r.name) for r in rows)
        version_width = max(len(_version_text(r)) for r in rows)
        for row in rows:
            self._row(row, name_width, version_width)
        if self.terminal and self.compared and _output.verbosity >= 0:
            shown = {r.status for r in rows}
            if shown != {LOCKED}:
                parts = [
                    f"{self.s(_MARKERS[s][0], _MARKERS[s][1])} {self.s(text, DIM)}" for s, text in _LEGEND if s in shown
                ]
                print()
                print("  ".join(parts))

    def _row(self, row: Row, name_width: int, version_width: int) -> None:
        head = ""
        indent = 0
        colour = DIM
        if self.compared:
            marker, colour = _MARKERS[row.status or UNLOCKED]
            head = f" {self.s(marker, colour)} "
            indent = 3
        namespace, _, short = row.name.partition(".")
        head += self.s(f"{namespace}.", BLUE) + self.s(short, BOLD) + " " * (name_width - len(row.name) + 2)
        version = _version_text(row)
        head += self.s(version, DIM) if row.version is None else version
        head += " " * (version_width - len(version) + 2)
        indent += name_width + 2 + version_width + 2

        note: list[Piece] = [(row.note, (colour,))] if row.note else []
        source: list[Piece] = []
        if row.source is not None:
            prefix = "link to " if row.name.endswith(".*") else ""
            source.append((prefix + _source_text(row.source), (DIM,)))
            if row.editable:
                source.append((" editable", (CYAN,)))
        line = note + ([("; ", (DIM,))] if note and source else []) + source

        room = self.width - indent - 1 if self.terminal else None
        if room is None or room < 10 or _length(line) <= room:
            print(f"{head}{self._pieces(line, None)}".rstrip())
        elif note and source:
            print(f"{head}{self._pieces(note, room)}".rstrip())
            print(" " * indent + self._pieces(source, room))
        else:
            print(f"{head}{self._pieces(line, room)}".rstrip())

    def _pieces(self, pieces: list[Piece], room: int | None) -> str:
        """``pieces`` styled, cut to ``room`` characters with an ellipsis if given and they're longer."""
        budget = room - 1 if room is not None and _length(pieces) > room else None
        out: list[str] = []
        used = 0
        for text, codes in pieces:
            if budget is not None:
                if used >= budget:
                    break
                text = text[: budget - used]
            used += len(text)
            out.append(self.s(text, *codes))
        if budget is not None:
            out.append("…")
        return "".join(out)


def _length(pieces: list[Piece]) -> int:
    return sum(len(text) for text, _ in pieces)


def _version_text(row: Row) -> str:
    if row.version is not None:
        return str(row.version)
    return "" if row.status == MISSING or row.name.endswith(".*") else "unknown"
