# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What ``alai sync`` installs and how: choosing groups as uv does, downloading only what isn't cached, then removing
and installing. The design is in ``design/install.md``."""

from __future__ import annotations

import argparse
import collections.abc as _c
import os
import time

from . import _output
from ._common import ConfigError
from ._config import Index, Settings
from ._install import Linker, install, link_editable, plan, remove, unlink_namespace
from ._lock import LockedCollection, LockedRoot
from ._manifest import Manifest
from ._output import change, error, shown_path, summary
from ._progress import Live, Task

DEFAULT_GROUPS = ("dev",)
"""Groups installed unless asked otherwise, as uv installs its ``dev`` dependency group."""


def selected_groups(args: argparse.Namespace, manifest: Manifest) -> tuple[bool, list[str]]:
    """Whether the project's own collections are installed, and which groups, as uv chooses them: the default groups
    that exist, plus ``--group``, or every group with ``--all-groups``, or only ``--only-group``'s, less ``--no-group``.
    """
    known = manifest.groups

    def check(flag: str, names: _c.Iterable[str], *, defaults_ok: bool = False) -> None:
        # --no-group dev and --no-dev are fine without a dev group, so a CI job can always pass them.
        for name in sorted(set(names)):
            if name not in known and not (defaults_ok and name in DEFAULT_GROUPS):
                have = ", ".join(f"'{g}'" for g in sorted(known))
                raise ConfigError(f"{flag}: alai.toml has no group '{name}'" + (f" (it has {have})" if have else ""))

    check("--group", args.group)
    check("--only-group", args.only_group)
    check("--only-dev", ["dev"] if args.only_dev else [])
    check("--no-group", args.no_group, defaults_ok=True)
    excluded = {*args.no_group, *(["dev"] if args.no_dev else [])}
    only = {*args.only_group, *(["dev"] if args.only_dev else [])}
    if only:
        if args.group or args.all_groups:
            raise ConfigError("--only-group and --only-dev can't be used with --group or --all-groups")
        return False, sorted(only - excluded)
    if args.all_groups:
        chosen = set(known)
    else:
        chosen: set[str] = set() if args.no_default_groups else {g for g in DEFAULT_GROUPS if g in known}
        chosen |= set(args.group)
    return True, sorted(chosen - excluded)


def sync_project(
    args: argparse.Namespace,
    settings: Settings,
    why: str,
    *,
    exact: bool,
    reinstall: bool | set[str] = False,
    brief: bool = False,
) -> tuple[Manifest, list[str]] | None:
    """Lock the project if needed and install what the options select, as ``alai sync`` does; ``why`` says, in the
    error, why the command needs an ``alai.toml``. ``reinstall`` is as for ``install_locked``. With ``brief``, as ``uv run`` syncs, only the phases that changed
    something are reported, and each collection's line only with ``-v``.

    The manifest and the names of the collections installed, the project's own included, or None when ``--locked``
    or ``--frozen`` failed, which has been reported.
    """
    from ._lock import lock_path, read_lock, stale, write_lock
    from ._project import load_project, lock_unchanged, report_changes, resolve_project, upgrade_from_args

    started = time.monotonic()
    raw, manifest = load_project(settings, why)
    project, groups = selected_groups(args, manifest)
    upgrade = upgrade_from_args(args)
    if upgrade and args.frozen:
        raise ConfigError("--upgrade, --upgrade-collection and --upgrade-group resolve, which --frozen doesn't")
    path = lock_path(settings.project.root)
    existing = read_lock(path)

    if (args.locked or args.frozen) and existing is None:
        flag = "--locked" if args.locked else "--frozen"
        error(f"there's no {shown_path(path)}, and {flag} was given; run alai lock")
        return None
    if args.frozen and existing is not None:
        lock = existing
    elif upgrade or args.locked or existing is None or stale(existing, manifest, {i.url for i in settings.indexes}):
        lock, resolution = resolve_project(settings, manifest, raw, existing, upgrade=upgrade)
        if not brief:
            summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)
        if args.locked:
            if not lock_unchanged(existing, manifest, lock):
                report_changes(existing, manifest, lock, resolution, inputs=True)
                error(f"{shown_path(path)} is out of date, and --locked was given; run alai lock")
                return None
        else:
            _ = write_lock(path, lock)
    else:
        lock = existing
        if not brief:
            summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)

    wanted = lock.select(groups, project=project)
    root = lock.root if project else None
    if root is not None and manifest.root is None:
        raise ConfigError(f"{shown_path(path)} locks {root.name} as the project's own, but there's no galaxy.yml")
    install_locked(
        settings,
        wanted,
        exact=exact,
        reinstall=reinstall,
        brief=brief,
        editable=root,
        editable_path=settings.project.root,
    )
    return manifest, [c.name for c in wanted] + ([root.name] if root is not None else [])


def install_locked(
    settings: Settings,
    wanted: list[LockedCollection],
    *,
    exact: bool,
    keep_unversioned: bool = False,
    reinstall: bool | set[str] = False,
    brief: bool = False,
    editable: LockedRoot | None = None,
    editable_path: str = "",
) -> None:
    """Make the collections directory hold ``wanted``: download what isn't cached, then remove and install. With
    ``keep_unversioned``, a collection whose version can't be read is left as it is. ``reinstall``, all of ``wanted``
    or the names given, replaces those already at their version from the cache, as uv reinstalls from its own. With
    ``brief``, nothing is said when nothing changed, and each collection's line only with ``-v``. ``editable``, the
    project's own collection, is linked to its checkout at ``editable_path`` rather than installed."""

    started = time.monotonic()
    cdir = settings.collections_dir
    todo = plan(
        cdir,
        {c.name: c.version for c in wanted},
        exact=exact,
        keep_unversioned=keep_unversioned,
        reinstall=reinstall,
        keep=[editable.name] if editable is not None else [],
    )
    by_name = {c.name: c for c in wanted}
    # Linked first, so a namespace link it replaces is already gone when the plan's own unlinking runs.
    linked = editable is not None and link_editable(cdir, editable.name, editable_path)
    if linked and editable is not None:
        summary("Linked", 1, time.monotonic() - started)
        if not brief or _output.verbosity >= 1:
            change("+", editable.name, f"=={editable.version.text} (from {editable_path})")
    if not (todo.install or todo.remove or todo.unlink):
        if not brief and not linked:
            summary("Checked", len(wanted) + (editable is not None), time.monotonic() - started, dim=True)
        return

    started = time.monotonic()
    sources = _fetch([by_name[n] for n in todo.install], settings)
    if sources.downloaded:
        summary("Prepared", sources.downloaded, time.monotonic() - started)

    started = time.monotonic()
    # Where each namespace link pointed, read before it's removed, to say so below.
    links = {path: _link_target(path) for path in todo.unlink}
    for path in todo.unlink:
        unlink_namespace(path)
    for collection in todo.remove:
        remove(cdir, collection)
    # An unlinked namespace isn't a collection uninstalled: its line below says what happened to it.
    gone = len(todo.remove) + len(todo.replace)
    if gone:
        summary("Uninstalled", gone, time.monotonic() - started)

    started = time.monotonic()
    linker = Linker(settings.link_mode)
    with Live(Task("Installing collections...", total=len(todo.install), bar=True)) as task:
        for name in todo.install:
            install(cdir, name, sources.paths[name], linker)
            task.advance(f"{name}=={by_name[name].version}")
    if todo.install:
        summary("Installed", len(todo.install), time.monotonic() - started)

    if brief and _output.verbosity < 1:
        return
    lines: list[tuple[str, str, str]] = []
    for path, target in links.items():
        lines.append(("-", os.path.basename(path), f" (namespace link to {target})"))
    # One replaced by the same version is a single ~ line, as uv shows a reinstall.
    same = {c.name for c in todo.replace if c.version == by_name[c.name].version}
    for collection in [*todo.remove, *todo.replace]:
        if collection.name in same:
            continue
        version = f"=={collection.version}" if collection.version is not None else " (unknown version)"
        lines.append(("-", collection.name, version))
    lines.extend(("~" if name in same else "+", name, f"=={by_name[name].version}") for name in todo.install)
    # By name, a removal before its replacement, as uv lists them.
    for sign, name, rest in sorted(lines, key=lambda line: (line[1], line[0] != "-")):
        change(sign, name, rest)


def _link_target(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:  # gone since the plan was made
        return "?"


class _Sources:
    def __init__(self, paths: dict[str, str], downloaded: int) -> None:
        self.paths = paths
        """Each collection's extracted directory in the cache, by name."""
        self.downloaded = downloaded


def _fetch(collections: list[LockedCollection], settings: Settings) -> _Sources:
    """The cached copy of each collection, downloading those that aren't cached.

    The fetcher, and so the HTTP library, is only loaded when something has to be downloaded. Each download comes
    from the index its lock entry names: by its configured name when one has that URL, else from the URL alone, as
    ``--frozen`` may use a lock whose index isn't configured any more.
    """
    from ._artifacts import Artifact, ArtifactCache

    cache = ArtifactCache(settings.cache_dir)
    paths: dict[str, str] = {}
    missing: list[LockedCollection] = []
    for collection in collections:
        path = cache.cached(collection.cksum)
        if path is not None:
            paths[collection.name] = path
        else:
            missing.append(collection)
    if not missing:
        return _Sources(paths, 0)

    from . import __version__
    from ._fetch import Fetcher
    from ._index import IndexClient, artifact_url

    configured = {i.url: i for i in settings.indexes}
    with Fetcher(
        user_agent=f"alai/{__version__}", offline=settings.offline, system_certs=settings.system_certs
    ) as fetcher:
        clients: dict[str, IndexClient] = {}
        artifacts: list[Artifact] = []
        for collection in missing:
            url = collection.source.url
            if url not in clients:
                index = configured.get(url) or Index(url, url, source=f"{settings.project.root}/alai.lock")
                clients[url] = IndexClient(index, fetcher, settings.cache_dir)
            dl = artifact_url(clients[url].config(), collection.name, collection.version, collection.cksum)
            artifacts.append(
                Artifact(collection.name, collection.version, url=dl, cksum=collection.cksum, size=collection.size)
            )
        with Live(Task("Preparing collections...", total=len(artifacts))) as task:

            def report(artifact: Artifact, state: str | None) -> None:
                if state is None:
                    task.end(artifact.name)
                elif state == "waiting":
                    task.pause(artifact.name)
                else:
                    task.start(artifact.name, state)

            paths.update(ArtifactCache(settings.cache_dir, fetcher).get_all(artifacts, report))
    return _Sources(paths, len(missing))
