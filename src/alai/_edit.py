# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai add`` and ``alai remove``: change the project's requirements in ``alai.toml``, then lock and sync, as
``uv add`` and ``uv remove`` do. The design is in ``design/manifest-editing.md``."""

from __future__ import annotations

import bisect
import collections.abc as _c
import contextlib
import os
import re
import secrets
import sys
import time

from . import _toml_edit
from ._common import NAME_RE, ConfigError
from ._config import Settings, raw_manifest
from ._lock import Lock, lock_path, read_lock, write_lock
from ._manifest import Manifest, Requirement, collection_name, parse_manifest
from ._output import home_path, summary
from ._project import resolve_project
from ._sync import DEFAULT_GROUPS, install_locked

if sys.version_info >= (3, 11):
    import tomllib  # pyright: ignore[reportUnreachable]  # checked against the 3.10 floor
else:
    import tomli as tomllib


def add(
    settings: Settings,
    requirements: _c.Sequence[str],
    *,
    group: str | None,
    sync: bool,
    frozen: bool,
    upgrade: bool | set[str] = False,
    reinstall: bool | set[str] = False,
) -> None:
    """Add ``requirements`` to the project's collections, or ``group``'s; see ``_add``."""
    _no_upgrade_frozen(upgrade, frozen=frozen)
    with _editing(settings, group, "add"):
        _add(settings, requirements, group=group, sync=sync, frozen=frozen, upgrade=upgrade, reinstall=reinstall)


def remove(
    settings: Settings,
    names: _c.Sequence[str],
    *,
    group: str | None,
    sync: bool,
    frozen: bool,
    upgrade: bool | set[str] = False,
    reinstall: bool | set[str] = False,
) -> None:
    """Remove the collections ``names`` from the project's collections, or ``group``'s."""
    _no_upgrade_frozen(upgrade, frozen=frozen)
    with _editing(settings, group, "remove"):
        _remove(settings, names, group=group, sync=sync, frozen=frozen, upgrade=upgrade, reinstall=reinstall)


def _no_upgrade_frozen(upgrade: bool | set[str], *, frozen: bool) -> None:
    if upgrade and frozen:
        raise ConfigError("--upgrade and --upgrade-collection resolve, which --frozen doesn't")


@contextlib.contextmanager
def _editing(settings: Settings, group: str | None, verb: str) -> _c.Generator[None]:
    """An edit the editor won't make becomes an error saying so, and what to do instead."""
    try:
        yield
    except _toml_edit.TomlEditError as e:
        where = "project.collections" if group is None else f"project.groups.{group}.collections"
        raise ConfigError(
            f"can't edit {settings.project.config_file} for you ({e}); {verb} the collections in {where} by hand"
        ) from None


def _add(
    settings: Settings,
    requirements: _c.Sequence[str],
    *,
    group: str | None,
    sync: bool,
    frozen: bool,
    upgrade: bool | set[str] = False,
    reinstall: bool | set[str] = False,
) -> None:
    """Add ``requirements`` to the project's collections, or ``group``'s.

    A collection already there is left as it is when given by name alone, and its requirement replaced when given
    with one. A name alone is written with the lowest version it can have, ``>=`` the version locked for it, once
    resolving has found one; with ``frozen`` nothing is resolved, so it's written as given.
    """
    config, text = _read(settings)
    path = _array_path(group)
    parsed = [_requirement(r) for r in requirements]
    bare: list[str] = []
    for requirement in parsed:
        current = _toml_edit.values(text, path)
        if current is None:
            text = _toml_edit.create(text, path, [requirement.text])
        else:
            names = [_name_of(v) for v in current]
            if requirement.name in names:
                if requirement.req.clauses:
                    text = _toml_edit.replace(text, path, names.index(requirement.name), requirement.text)
                continue
            text = _toml_edit.insert(text, path, _position(names, requirement.name), requirement.text)
        if not requirement.req.clauses:
            bare.append(requirement.name)
    if frozen:
        _write(config, text)
        return

    def bound(text: str, lock: Lock) -> str:
        # Each name added alone gets >= its locked version, as uv writes one.
        for name in bare:
            current = _toml_edit.values(text, path) or []
            names = [_name_of(v) for v in current]
            locked = lock.get(name)
            if locked is not None and name in names:
                text = _toml_edit.replace(text, path, names.index(name), f"{name}>={locked.version}")
        return text

    _lock_and_sync(settings, config, text, bound, group=group, sync=sync, upgrade=upgrade, reinstall=reinstall)


def _remove(
    settings: Settings,
    names: _c.Sequence[str],
    *,
    group: str | None,
    sync: bool,
    frozen: bool,
    upgrade: bool | set[str] = False,
    reinstall: bool | set[str] = False,
) -> None:
    config, text = _read(settings)
    path = _array_path(group)
    for given in names:
        try:
            name = collection_name(given)
        except ValueError as e:
            raise ConfigError(str(e)) from None
        current = _toml_edit.values(text, path) or []
        found = [_name_of(v) for v in current]
        if name not in found:
            raise ConfigError(_not_there(text, name, group))
        text = _toml_edit.remove(text, path, found.index(name))
    if frozen:
        _write(config, text)
        return
    _lock_and_sync(
        settings,
        config,
        text,
        lambda text, _: text,
        group=None,
        sync=sync,
        upgrade=upgrade,
        reinstall=reinstall,
    )


def _lock_and_sync(
    settings: Settings,
    config: str,
    text: str,
    finish: _c.Callable[[str, Lock], str],
    *,
    group: str | None,
    sync: bool,
    upgrade: bool | set[str],
    reinstall: bool | set[str],
) -> None:
    """Resolve ``text`` as the new ``alai.toml``, let ``finish`` give its final text from the lock, and write both;
    then sync, with ``group`` installed as well as the default groups, reinstalling ``reinstall``. Nothing is written
    if resolving fails."""
    started = time.monotonic()
    manifest = _manifest(text, config)
    existing = read_lock(lock_path(settings.project.root))
    lock, _ = resolve_project(settings, manifest, raw_manifest(tomllib.loads(text), config), existing, upgrade=upgrade)
    final = finish(text, lock)
    raw = raw_manifest(tomllib.loads(final), config)
    manifest = parse_manifest(raw)
    lock = Lock(raw, lock.collections)
    summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)
    _write(config, final)
    _ = write_lock(lock_path(settings.project.root), lock)
    if sync:
        groups = sorted({g for g in DEFAULT_GROUPS if g in manifest.groups} | ({group} if group else set()))
        install_locked(settings, lock.select(groups), exact=True, reinstall=reinstall)


def _read(settings: Settings) -> tuple[str, str]:
    """The project's ``alai.toml`` path and text, as written: its newlines kept, so edits keep them too."""
    config = settings.project.config_file
    if config is None:
        raise ConfigError(
            f"there's no alai.toml in {home_path(settings.project.root)}: create one with a [project] table first"
        )
    try:
        with open(config, encoding="utf-8", newline="") as f:
            return config, f.read()
    except OSError as e:
        raise ConfigError(f"can't read {config}: {e.strerror or e}") from None


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


def _manifest(text: str, config: str) -> Manifest:
    return parse_manifest(raw_manifest(tomllib.loads(text), config))


def _array_path(group: str | None) -> tuple[str, ...]:
    if group is None:
        return ("project", "collections")
    if not NAME_RE.fullmatch(group):
        raise ConfigError(
            f"--group: invalid group name '{group}', use lowercase letters, digits, '-' and '_', starting with a "
            + "letter or digit"
        )
    return ("project", "groups", group, "collections")


def _requirement(text: str) -> Requirement:
    # Written as uv writes one: without the spaces a requirement may be typed with.
    compact = re.sub(r"\s+", "", text)
    try:
        return Requirement.parse(compact)
    except ValueError as e:
        raise ConfigError(str(e)) from None


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


def _not_there(text: str, name: str, group: str | None) -> str:
    where = "project.collections" if group is None else f"group '{group}'"
    message = f"{name} isn't in {where}"
    places: list[str] = []
    if group is not None and name in [_name_of(v) for v in _toml_edit.values(text, ("project", "collections")) or []]:
        places.append(f"project.collections (try: alai remove {name})")
    for other in sorted(raw_manifest(tomllib.loads(text), "alai.toml").groups):
        if other == group:
            continue
        found = _toml_edit.values(text, ("project", "groups", other, "collections")) or []
        if name in [_name_of(v) for v in found]:
            places.append(f"group '{other}' (try: alai remove {name} --group {other})")
    return message + (f"; it's in {', '.join(places)}" if places else "")
