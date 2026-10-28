# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What ``alai sync`` installs and how: choosing groups as uv does, downloading only what isn't cached, then removing
and installing. The design is in ``design/install.md``."""

from __future__ import annotations

import collections.abc as _c
import contextlib
import os
import pathlib
import time
import typing as _t

from . import _output
from ._common import ConfigError, home_path, sha256_file
from ._config import Index, RawManifest, Settings
from ._install import (
    Linker,
    Plan,
    current_umask,
    install,
    link_editable,
    linked,
    namespace_dir,
    place,
    plan,
    remove,
    unlink_namespace,
)
from ._lock import (
    ArchiveSource,
    ArchiveUrlSource,
    DirectorySource,
    EditableSource,
    GitCommitSource,
    IndexSource,
    LockedCollection,
    LockedRoot,
)
from ._manifest import Manifest
from ._output import change, error, shown_path, summary
from ._progress import Live, Task

if _t.TYPE_CHECKING:
    from alai_core.semver import Version

    from ._project import Upgrade

DEFAULT_GROUPS = ("dev",)
"""Groups installed unless asked otherwise, as uv installs its ``dev`` dependency group."""


def sync_project(
    settings: Settings,
    raw: RawManifest,
    manifest: Manifest,
    *,
    project: bool,
    groups: _c.Sequence[str],
    upgrade: Upgrade,
    locked: bool,
    frozen: bool,
    exact: bool,
    reinstall: bool | set[str] = False,
    brief: bool = False,
) -> list[str] | None:
    """Lock the project if needed and install the project's collections, unless not ``project``, and ``groups``, as
    ``alai sync`` does: ``raw`` and ``manifest`` are the project as ``load_project`` gives them. ``upgrade`` is what
    to lock again, and ``locked`` and ``frozen`` the flags of those names; ``reinstall`` is as for
    ``install_locked``. With ``brief``, as ``uv run`` syncs, only the phases that changed something are reported, and
    each collection's line only with ``-v``.

    The names of the collections installed, the project's own included, or None when ``locked`` or ``frozen`` failed,
    which has been reported.
    """
    from ._lock import lock_path, read_lock, stale, write_lock
    from ._project import lock_unchanged, report_changes, resolve_project

    started = time.monotonic()
    path = lock_path(settings.project.root)
    existing = read_lock(path)

    if (locked or frozen) and existing is None:
        flag = "--locked" if locked else "--frozen"
        error(f"there's no {shown_path(path)}, and {flag} was given; run alai lock")
        return None
    if frozen and existing is not None:
        lock = existing
    elif upgrade or locked or existing is None or stale(existing, manifest, {i.url for i in settings.indexes}):
        lock, resolution = resolve_project(settings, manifest, raw, existing, upgrade=upgrade, download=True)
        if not brief:
            summary("Resolved", len(lock.collections), time.monotonic() - started, dim=True)
        if locked:
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
    install_locked(settings, wanted, exact=exact, reinstall=reinstall, brief=brief, root=root)
    return [c.name for c in wanted] + ([root.name] if root is not None else [])


def install_locked(
    settings: Settings,
    wanted: list[LockedCollection],
    *,
    exact: bool,
    keep_unversioned: bool = False,
    reinstall: bool | set[str] = False,
    brief: bool = False,
    root: LockedRoot | None = None,
) -> None:
    """Make the collections directory hold ``wanted``: download what isn't cached, copy what comes from a directory,
    link what's editable, then remove and install. With ``keep_unversioned``, a collection whose version can't be
    read is left as it is. ``reinstall``, all of ``wanted`` or the names given, replaces those already at their
    version from the cache, or copies them again from their directory, as uv reinstalls from its own. With ``brief``,
    nothing is said when nothing changed, and each collection's line only with ``-v``. ``root``, the project's own
    collection, is linked to the project's root."""
    from ._project import source_dir

    started = time.monotonic()
    cdir = settings.collections_dir
    links, directories, records, todo = _plan(
        settings, wanted, root, exact=exact, keep_unversioned=keep_unversioned, reinstall=reinstall
    )
    by_name = {c.name: c for c in wanted}
    # Linked first, so a namespace link one replaces is already gone when the plan's own unlinking runs.
    relinked = [(name, version, path) for name, version, path in links if link_editable(cdir, name, path)]
    if relinked:
        summary("Linked", len(relinked), time.monotonic() - started)
        if not brief or _output.verbosity >= 1:
            for name, version, path in relinked:
                change("+", name, f"=={version.text} (from {home_path(path)})")
    if not (todo.install or todo.remove or todo.unlink):
        if not brief and not relinked:
            summary("Checked", len(wanted) + (root is not None), time.monotonic() - started, dim=True)
        return

    started = time.monotonic()
    fetched = _fetch_commits([by_name[n] for n in todo.install if by_name[n].is_git()], settings)
    directories.update(fetched.paths)
    sources = _fetch([by_name[n] for n in todo.install if n not in directories], settings)
    if sources.downloaded + fetched.downloaded:
        summary("Prepared", sources.downloaded + fetched.downloaded, time.monotonic() - started)

    started = time.monotonic()
    # Where each namespace link pointed, read before it's removed, to say so below.
    namespace_targets = {path: _link_target(path) for path in todo.unlink}
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
    umask = current_umask()
    with Live(Task("Installing collections...", total=len(todo.install), bar=True)) as task:
        cached = [name for name in todo.install if name not in directories]
        for name in todo.install:
            if name in directories:
                _copy_directory(cdir, by_name[name], directories[name], records[name], umask)
                task.advance(f"{name}=={by_name[name].version}")
        _install_all(cdir, cached, sources.paths, linker, records, lambda n: task.advance(f"{n}=={by_name[n].version}"))
    if todo.install:
        summary("Installed", len(todo.install), time.monotonic() - started)

    if brief and _output.verbosity < 1:
        return
    lines: list[tuple[str, str, str]] = []
    for path, target in namespace_targets.items():
        lines.append(("-", os.path.basename(path), f" (namespace link to {target})"))
    # One replaced by the same version is a single ~ line, as uv shows a reinstall.
    same = {c.name for c in todo.replace if c.version == by_name[c.name].version}
    for collection in [*todo.remove, *todo.replace]:
        if collection.name in same:
            continue
        version = f"=={collection.version}" if collection.version is not None else " (unknown version)"
        lines.append(("-", collection.name, version))
    for name in todo.install:
        # One from a directory or a tarball says which, as uv shows a path or URL dependency's source.
        source = by_name[name].source
        if isinstance(source, DirectorySource):
            where = f" (from {home_path(directories[name])})"
        elif isinstance(source, ArchiveSource):
            where = f" (from {home_path(source_dir(settings, source.path))})"
        elif isinstance(source, (ArchiveUrlSource, GitCommitSource)):
            where = f" (from {source})"
        else:
            where = ""
        lines.append(("~" if name in same else "+", name, f"=={by_name[name].version}{where}"))
    # By name, a removal before its replacement, as uv lists them.
    for sign, name, rest in sorted(lines, key=lambda line: (line[1], line[0] != "-")):
        change(sign, name, rest)


def _install_all(
    cdir: str,
    names: list[str],
    paths: _c.Mapping[str, str],
    linker: Linker,
    records: _c.Mapping[str, dict[str, object]],
    done: _c.Callable[[str], None],
) -> None:
    """Install each cached collection in ``names`` from ``paths``, several at once: each in its own directory, and
    the extension's linker releases the GIL. Every one is attempted; if any fails, the first by name is raised once
    the rest have finished, as downloads are. ``done`` is called with each name as it's installed."""
    import concurrent.futures

    from ._common import worker_threads

    # Made before the threads start, so no two replace the same namespace link.
    for namespace in sorted({name.split(".")[0] for name in names}):
        _ = namespace_dir(cdir, namespace)
    with concurrent.futures.ThreadPoolExecutor(worker_threads(), thread_name_prefix="alai-install") as pool:
        futures = {pool.submit(install, cdir, name, paths[name], linker, records.get(name)): name for name in names}
        for future in concurrent.futures.as_completed(futures):
            if future.exception() is None:
                done(futures[future])
    for future, _name in sorted(futures.items(), key=lambda item: item[1]):
        future.result()


def not_installed(settings: Settings, wanted: list[LockedCollection], root: LockedRoot | None) -> int:
    """How many of ``wanted``, and ``root``, an inexact ``install_locked`` would install or link."""
    links, _, _, todo = _plan(settings, wanted, root, exact=False)
    return len(todo.install) + sum(not linked(settings.collections_dir, name, path) for name, _, path in links)


def _plan(
    settings: Settings,
    wanted: list[LockedCollection],
    root: LockedRoot | None,
    *,
    exact: bool,
    keep_unversioned: bool = False,
    reinstall: bool | set[str] = False,
) -> tuple[list[tuple[str, Version, str]], dict[str, str], dict[str, dict[str, object]], Plan]:
    """What ``install_locked`` does: the editable installs to link, by name, version and checkout, the project's own
    collection first; each collection to copy from a directory, by name, to which those from a commit are added once
    it's fetched; the record each collection from a directory, a tarball or a commit is installed with; and the plan
    for the rest."""
    links, directories, records = expected(settings, wanted, root)
    todo = plan(
        settings.collections_dir,
        {c.name: c.version for c in wanted if not isinstance(c.source, EditableSource)},
        exact=exact,
        keep_unversioned=keep_unversioned,
        reinstall=reinstall,
        keep=[name for name, _, _ in links],
        records=records,
    )
    return links, directories, records, todo


def expected(
    settings: Settings, wanted: _c.Iterable[LockedCollection], root: LockedRoot | None
) -> tuple[list[tuple[str, Version, str]], dict[str, str], dict[str, dict[str, object]]]:
    """How ``wanted`` and ``root`` are installed when they're as locked: the editable installs, by name, version and
    the checkout they link to, the project's own collection first; the directory each collection copied from one comes
    from, by name; and the record each collection from a directory, a tarball or a commit has. One from an index has
    no record."""
    from ._project import source_dir

    wanted = list(wanted)
    links = [
        (c.name, c.version, source_dir(settings, c.source.path)) for c in wanted if isinstance(c.source, EditableSource)
    ]
    if root is not None:
        links.insert(0, (root.name, root.version, settings.project.root))
    directories = {c.name: source_dir(settings, c.source.path) for c in wanted if isinstance(c.source, DirectorySource)}
    records = {name: directory_record(path) for name, path in directories.items()}
    for c in wanted:
        if isinstance(c.source, ArchiveSource):
            records[c.name] = {"source": {"path": source_dir(settings, c.source.path)}, "cksum": c.cksum}
        elif isinstance(c.source, ArchiveUrlSource):
            records[c.name] = {"source": {"url": c.source.url}, "cksum": c.cksum}
        elif isinstance(c.source, GitCommitSource):
            records[c.name] = git_record(c.source)
    return links, directories, records


def directory_record(path: str) -> dict[str, object]:
    """The install record of a collection copied from the directory ``path``: the directory, and the sha256 of its
    metadata as cache keys, so a change to any of it copies it again: ``galaxy.yml`` for a source, ``MANIFEST.json``
    for a built collection, whose ``FILES.json`` checksum it holds, and ``meta/runtime.yml``. uv keys a directory
    on ``pyproject.toml``'s modification time; a hash is as cheap for two small files, and neither misses an edit
    that keeps the time nor copies again for one that only touched it. Editing any other file doesn't copy it
    again, as in uv: that's what an editable source, or ``--reinstall-collection``, is for."""
    keys = {
        name: _file_sha256(os.path.join(path, name)) for name in ("galaxy.yml", "MANIFEST.json", "meta/runtime.yml")
    }
    return {"source": {"directory": path}, "cache_keys": keys}


def git_record(source: GitCommitSource) -> dict[str, object]:
    """The install record of a collection built from a commit: the repository, the commit and the directory in it, so
    another commit is installed in its place even at the same version, as uv's ``direct_url.json`` holds a git
    dependency's commit."""
    found: dict[str, object] = {"git": source.url, "commit": source.commit}
    if source.subdirectory is not None:
        found["subdirectory"] = source.subdirectory
    return {"source": found}


def _file_sha256(path: str) -> str | None:
    """A small file's sha256, or None when it can't be read, such as a ``meta/runtime.yml`` the collection doesn't
    have."""
    try:
        return sha256_file(path)
    except OSError:
        return None


def _copy_directory(cdir: str, collection: LockedCollection, path: str, record: dict[str, object], umask: int) -> None:
    from ._build import install_dir

    place(
        cdir,
        collection.name,
        lambda temp: install_dir(
            path, temp, collection.name, collection.version, umask=umask, source_version=collection.source_version
        ),
        path,
        record,
    )


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
    ``--frozen`` may use a lock whose index isn't configured any more. A tarball comes from its URL, or its file,
    checked against the lock as an index's is.
    """
    from ._artifacts import Artifact, ArtifactCache

    cache = ArtifactCache(settings.cache_dir)
    paths: dict[str, str] = {}
    missing: list[LockedCollection] = []
    for collection in collections:
        path = cache.cached(_artifact(collection)[0])
        if path is not None:
            paths[collection.name] = path
        else:
            missing.append(collection)
    if not missing:
        return _Sources(paths, 0)

    from ._fetch import open_fetcher
    from ._index import IndexClient, artifact_url
    from ._project import source_dir

    configured = {i.url: i for i in settings.indexes}
    with open_fetcher(settings) as fetcher:
        clients: dict[str, IndexClient] = {}
        artifacts: list[Artifact] = []
        for collection in missing:
            cksum, size = _artifact(collection)
            source = collection.source
            if isinstance(source, ArchiveUrlSource):
                dl = source.url
            elif isinstance(source, ArchiveSource):
                dl = pathlib.Path(source_dir(settings, source.path)).as_uri()
            elif isinstance(source, IndexSource):
                url = source.url
                if url not in clients:
                    index = configured.get(url) or Index(url, url, source=f"{settings.project.root}/alai.lock")
                    # Only config.json, for the download URLs: not collections.json, which installing doesn't use.
                    clients[url] = IndexClient(index, fetcher, settings.cache_dir, prefetch=None)
                dl = artifact_url(clients[url].config(), collection.name, collection.version, cksum)
            else:
                raise ValueError(f"{collection.name} doesn't come from an artifact")
            artifacts.append(Artifact(collection.name, collection.version, url=dl, cksum=cksum, size=size))
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


def _fetch_commits(collections: list[LockedCollection], settings: Settings) -> _Sources:
    """The directory each collection from a commit is built from, fetching the commits that aren't cached, in
    parallel."""
    from ._git import cached, update_all

    commits = {c.name: c.source for c in collections if isinstance(c.source, GitCommitSource)}
    if not commits:
        return _Sources({}, 0)
    missing = {(s.url, s.commit) for s in commits.values() if cached(settings.cache_dir, s.url, s.commit) is None}
    with Live(Task("Fetching git repositories...", total=len(missing))) if missing else contextlib.nullcontext():
        fetched = update_all(
            settings.cache_dir,
            [(s.url, s.rev, s.commit, name) for name, s in commits.items()],
            offline=settings.offline,
        )
    paths: dict[str, str] = {}
    for name, source in commits.items():
        _, root = fetched[name]
        paths[name] = os.path.join(root, *source.subdirectory.split("/")) if source.subdirectory is not None else root
    return _Sources(paths, len(missing))


def _artifact(collection: LockedCollection) -> tuple[str, int]:
    """An artifact's sha256 and size, which reading a lock requires of every index's or tarball's collection."""
    if collection.cksum is None or collection.size is None:
        raise ValueError(f"{collection.name} doesn't come from an artifact")
    return collection.cksum, collection.size
