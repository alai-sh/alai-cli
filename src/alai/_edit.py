# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai add`` and ``alai remove``: change the project's requirements in ``alai.toml``, then lock and sync, as
``uv add`` and ``uv remove`` do. The design is in ``design/manifest-editing.md``."""

from __future__ import annotations

import bisect
import collections.abc as _c
import contextlib
import os
import secrets
import time

from ._common import NAME_RE, ConfigError, home_path, read_toml_document
from ._config import Settings, no_project, raw_manifest
from ._lock import Lock, lock_path, read_lock, write_lock
from ._manifest import (
    GitSource,
    PathSource,
    Requirement,
    SourceCollection,
    UrlSource,
    collection_name,
    given_source,
    is_source,
    source_key,
)
from ._native import TomlDocument, TomlEditError
from ._output import error, shown_path, summary
from ._project import (
    Upgrade,
    given_requirement,
    lock_unchanged,
    read_given_source,
    report_changes,
    resolve_project,
    with_root,
)
from ._sync import DEFAULT_GROUPS, install_locked


def add(
    settings: Settings,
    requirements: _c.Sequence[str],
    *,
    group: str | None,
    sync: bool,
    frozen: bool,
    locked: bool = False,
    upgrade: Upgrade | None = None,
    reinstall: bool | set[str] = False,
    editable: _c.Sequence[str] = (),
) -> bool:
    """Add ``requirements`` to the project's collections, or ``group``'s. False when ``locked`` and ``alai.lock`` is
    missing or would change, having written nothing.

    A collection already there is left as it is when given by name alone, and its requirement replaced when given
    with one. A name alone is written with the lowest version it can have, ``>=`` the version locked for it, once
    resolving has found one; with ``frozen`` nothing is resolved, so it's written as given. A directory is the
    collection whose source it holds: its name is added alone, and ``[project.sources]`` takes it from there,
    replacing any entry it had, as ``uv add ../lib`` does; one in ``editable`` is taken as editable, as ``pip install
    -e`` takes each. A tarball, by path or URL, is the same, as ``uv add ../lib-1.0.tar.gz`` and ``uv add
    https://.../lib-1.0.tar.gz`` are, and so is a git repository, ``git+<url>[@<rev>][#subdirectory=<dir>]``, as
    ``uv add git+...`` writes one.
    """
    with _editing(settings, group, "add"):
        config, doc = _read(settings, group)
        path = _array_path(group)
        parsed: list[Requirement] = []
        sources: dict[str, PathSource | UrlSource | GitSource] = {}
        fetched: dict[str, SourceCollection] = {}
        for given, edit in [*((r, False) for r in requirements), *((d, True) for d in editable)]:
            # -e takes a directory, so a bare name there is one, as pip install -e lib2 installs the directory lib2.
            if not edit and not is_source(given):
                parsed.append(given_requirement(given))
                continue
            collection, source = read_given_source(settings, given, editable=edit, relative_to=settings.project.root)
            parsed.append(Requirement.parse(collection.name))
            sources[collection.name] = source
            if isinstance(source, (UrlSource, GitSource)):
                fetched[collection.name] = collection
        bare: list[str] = []
        for requirement in parsed:
            current = doc.values(path)
            if current is None:
                doc.create(path, [requirement.text])
            else:
                names = [_name_of(v) for v in current]
                if requirement.name in names:
                    if requirement.req.clauses:
                        doc.replace(path, names.index(requirement.name), requirement.text)
                    continue
                doc.insert(path, _position(names, requirement.name), requirement.text)
            # One from a directory stays bare, as uv writes a path dependency: there's only ever its one version.
            if not requirement.req.clauses and requirement.name not in sources:
                bare.append(requirement.name)
        for name, source in sources.items():
            table: dict[str, str | bool]
            if isinstance(source, GitSource):
                # uv's order: the repository, then the directory in it, then the revision; then alai's version.
                table = {"git": source.url}
                if source.subdirectory is not None:
                    table["subdirectory"] = source.subdirectory
                if source.rev is not None:
                    table["rev"] = source.rev
                if source.version is not None:
                    table["version"] = source.version.text
            elif isinstance(source, UrlSource):
                table = {"url": source.url}
            else:
                table = {"path": source.path}
                if source.editable:
                    table["editable"] = True
            doc.set_table(("project", "sources", name), table)
        if frozen:
            _write(config, str(doc))
            return True

        def bound(doc: TomlDocument, lock: Lock) -> None:
            # Each name added alone gets >= its locked version, as uv writes one.
            for name in bare:
                current = doc.values(path) or []
                names = [_name_of(v) for v in current]
                locked = lock.get(name)
                if locked is not None and name in names:
                    doc.replace(path, names.index(name), f"{name}>={locked.version}")

        return _lock_and_sync(
            settings,
            config,
            doc,
            bound,
            group=group,
            sync=sync,
            locked=locked,
            upgrade=upgrade,
            reinstall=reinstall,
            fetched=fetched,
        )


def remove(
    settings: Settings,
    names: _c.Sequence[str],
    *,
    group: str | None,
    sync: bool,
    frozen: bool,
    locked: bool = False,
    upgrade: Upgrade | None = None,
    reinstall: bool | set[str] = False,
) -> bool:
    """Remove the collections ``names`` from the project's collections, or ``group``'s. False when ``locked`` and
    ``alai.lock`` is missing or would change, having written nothing."""
    with _editing(settings, group, "remove"):
        config, doc = _read(settings, group)
        path = _array_path(group)
        for given in names:
            try:
                name = collection_name(given)
            except ValueError as e:
                raise ConfigError(_by_source(settings, doc, given) or str(e)) from None
            current = doc.values(path) or []
            found = [_name_of(v) for v in current]
            if name not in found:
                raise ConfigError(_not_there(doc, name, group))
            doc.remove(path, found.index(name))
            _drop_path_source(doc, name)
        if frozen:
            _write(config, str(doc))
            return True
        return _lock_and_sync(
            settings,
            config,
            doc,
            lambda _doc, _lock: None,
            group=None,
            sync=sync,
            locked=locked,
            upgrade=upgrade,
            reinstall=reinstall,
        )


@contextlib.contextmanager
def _editing(settings: Settings, group: str | None, verb: str) -> _c.Generator[None]:
    """An edit the editor won't make becomes an error saying so, and what to do instead."""
    try:
        yield
    except TomlEditError as e:
        where = "project.collections" if group is None else f"project.groups.{group}.collections"
        raise ConfigError(
            f"can't edit {settings.project.config_file} for you ({e}); {verb} the collections in {where} by hand"
        ) from None


def _lock_and_sync(
    settings: Settings,
    config: str,
    doc: TomlDocument,
    finish: _c.Callable[[TomlDocument, Lock], None],
    *,
    group: str | None,
    sync: bool,
    locked: bool,
    upgrade: Upgrade | None,
    reinstall: bool | set[str],
    fetched: _c.Mapping[str, SourceCollection] | None = None,
) -> bool:
    """Resolve ``doc`` as the new ``alai.toml``, let ``finish`` make its last edits from the lock, and write both;
    then sync, with ``group`` installed as well as the default groups, reinstalling ``reinstall``. Nothing is written
    if resolving fails. ``fetched`` are the URL sources already downloaded, by name, so they aren't again.

    With ``locked``, a missing ``alai.lock``, or one the edit would change, is an error that writes nothing (False),
    checked before resolving and after, as ``alai sync --locked`` does. Otherwise ``alai.toml`` is written, as uv
    writes ``pyproject.toml`` when the requirement is only spelt differently, and ``alai.lock`` kept as it is.
    """
    started = time.monotonic()
    raw, manifest = with_root(settings, raw_manifest(doc.data(), config))
    manifest.local.update(fetched or {})
    path = lock_path(settings.project.root)
    existing = read_lock(path)
    if locked and existing is None:
        error(f"there's no {shown_path(path)}, and --locked was given; run alai lock")
        return False
    lock, resolution = resolve_project(settings, manifest, raw, existing, upgrade=upgrade, download=sync)
    finish(doc, lock)
    # Parsed again for what finish changed, with the sources as they were just read rather than read again.
    raw, manifest = with_root(settings, raw_manifest(doc.data(), config), local=manifest.local)
    lock = Lock(raw, lock.collections, lock.root)
    summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)
    if locked and not lock_unchanged(existing, manifest, lock):
        report_changes(existing, manifest, lock, resolution, inputs=True)
        error(f"{shown_path(path)} would change, and --locked was given")
        return False
    _write(config, str(doc))
    if not locked:
        _ = write_lock(path, lock)
    if sync:
        groups = sorted({g for g in DEFAULT_GROUPS if g in manifest.groups} | ({group} if group else set()))
        install_locked(
            settings,
            lock.select(groups),
            exact=True,
            reinstall=reinstall,
            root=lock.root,
        )
    return True


def _by_source(settings: Settings, doc: TomlDocument, given: str) -> str | None:
    """What to say when ``alai remove`` is given a source, such as ``git+...``, rather than a collection name: the
    collections ``[project.sources]`` takes from it, to remove by name, as ``uv remove`` takes names alone. None when
    ``given`` isn't a source, for the error about collection names to stand."""
    key = given_source(given)
    if key is None:
        return None
    raw = raw_manifest(doc.data(), "alai.toml")
    names = sorted(n for n, table in raw.sources.items() if source_key(table, settings.project.root) == key)
    if not names:
        return f"alai remove takes collection names, such as ns.col, and [project.sources] takes nothing from {given}"
    return (
        f"alai remove takes collection names: [project.sources] takes {', '.join(names)} from {given}, so run "
        + f"alai remove {' '.join(names)}"
    )


def _drop_path_source(doc: TomlDocument, name: str) -> None:
    """Remove ``name``'s ``[project.sources]`` entry when it's a directory, a tarball or a git repository and
    nothing lists the collection any more, as ``uv remove`` drops its source. An index pin stays: it also applies to what other
    collections depend on."""
    raw = raw_manifest(doc.data(), "alai.toml")
    source = raw.sources.get(name)
    if source is None or not {"path", "url", "git"} & set(source):
        return
    listed = [*raw.collections, *(r for items in raw.groups.values() for r in items)]
    if name in (_name_of(r) for r in listed):
        return
    doc.remove_key(("project", "sources", name))


def _read(settings: Settings, group: str | None) -> tuple[str, TomlDocument]:
    """The project's ``alai.toml`` path and document, its newlines kept, so edits keep them too. In a
    collection project only a group can be edited, as the rest is in ``galaxy.yml``."""
    config = settings.project.config_file
    if settings.project.collection_file is not None and group is None:
        raise ConfigError(
            f"{home_path(settings.project.root)} is a collection project, whose collections are galaxy.yml's "
            + "dependencies, which alai can't edit yet: change them by hand, then run alai lock, or give --group "
            + "for collections only its tests need"
        )
    if config is None:
        raise no_project(settings.project, "it's where the project's collections are listed")
    return config, read_toml_document(config)


def _write(config: str, text: str) -> None:
    """Replace ``alai.toml`` with ``text``, written beside it and renamed over it, keeping its permissions."""
    temp = f"{config}.{secrets.token_hex(4)}.tmp"
    try:
        mode = os.stat(config).st_mode & 0o7777
        with open(temp, "x", encoding="utf-8", newline="") as f:
            _ = f.write(text)
        os.chmod(temp, mode)
        os.replace(temp, config)
    except OSError as e:
        if os.path.exists(temp):
            os.remove(temp)
        raise ConfigError(f"can't write {config}: {e.strerror or e}") from None


def _array_path(group: str | None) -> tuple[str, ...]:
    if group is None:
        return ("project", "collections")
    if not NAME_RE.fullmatch(group):
        raise ConfigError(
            f"--group: invalid group name '{group}', use lowercase letters, digits, '-' and '_', starting with a "
            + "letter or digit"
        )
    return ("project", "groups", group, "collections")


def _name_of(requirement: str) -> str:
    """The collection a requirement in ``alai.toml`` names, or the text itself if it can't be read, so it's never
    mistaken for another."""
    try:
        return Requirement.parse(requirement.strip()).name
    except ValueError:
        return requirement


def _position(names: list[str], name: str) -> int:
    """Where a new collection goes: in order if the list is already sorted, as uv keeps a sorted list sorted, else at
    the end."""
    folded = [n.lower() for n in names]
    if folded == sorted(folded):
        return bisect.bisect(folded, name.lower())
    return len(names)


def _not_there(doc: TomlDocument, name: str, group: str | None) -> str:
    where = "project.collections" if group is None else f"group '{group}'"
    message = f"{name} isn't in {where}"
    places: list[str] = []
    if group is not None and name in [_name_of(v) for v in doc.values(("project", "collections")) or []]:
        places.append(f"project.collections (try: alai remove {name})")
    for other in sorted(raw_manifest(doc.data(), "alai.toml").groups):
        if other == group:
            continue
        found = doc.values(("project", "groups", other, "collections")) or []
        if name in [_name_of(v) for v in found]:
            places.append(f"group '{other}' (try: alai remove {name} --group {other})")
    return message + (f"; it's in {', '.join(places)}" if places else "")
