# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What ``alai sync`` installs and how: choosing groups as uv does, downloading only what isn't cached, then removing
and installing. The design is in ``design/install.md``."""

from __future__ import annotations

import argparse
import collections.abc as _c
import os
import time

from ._common import ConfigError
from ._config import Index, Settings
from ._install import Linker, install, plan, remove, unlink_namespace
from ._lock import LockedCollection
from ._manifest import Manifest
from ._output import change, summary
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


def install_locked(
    settings: Settings, wanted: list[LockedCollection], *, exact: bool, keep_unversioned: bool = False
) -> None:
    """Make the collections directory hold ``wanted``: download what isn't cached, then remove and install. With
    ``keep_unversioned``, a collection whose version can't be read is left as it is."""

    started = time.monotonic()
    cdir = settings.collections_dir
    todo = plan(cdir, {c.name: c.version for c in wanted}, exact=exact, keep_unversioned=keep_unversioned)
    by_name = {c.name: c for c in wanted}
    if not (todo.install or todo.remove or todo.unlink):
        summary("Checked", len(wanted), time.monotonic() - started, dim=True)
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

    lines: list[tuple[str, str, str]] = []
    for path, target in links.items():
        lines.append(("-", os.path.basename(path), f" (namespace link to {target})"))
    for collection in [*todo.remove, *todo.replace]:
        version = f"=={collection.version}" if collection.version is not None else " (unknown version)"
        lines.append(("-", collection.name, version))
    lines.extend(("+", name, f"=={by_name[name].version}") for name in todo.install)
    # By name, a removal before its replacement, as uv lists them.
    for sign, name, rest in sorted(lines, key=lambda line: (line[1], line[0] == "+")):
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
