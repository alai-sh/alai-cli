# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Loading the project's manifest and resolving it, for the commands that lock: ``lock`` and ``sync``."""

from __future__ import annotations

import collections.abc as _c
import concurrent.futures
import os
import re
import typing as _t

from ._common import ConfigError, Record, home_path, inside, no_group, read_toml, selects, sha256_file
from ._config import RawManifest, Settings, no_project, raw_manifest
from ._lock import (
    ArchiveSource,
    ArchiveUrlSource,
    GitCommitSource,
    Lock,
    LockedRoot,
    changes,
    input_changes,
    local_changes,
)
from ._manifest import (
    GitSource,
    Manifest,
    PathSource,
    Requirement,
    SourceCollection,
    UrlSource,
    file_url_path,
    is_git,
    is_url,
    parse_git,
    parse_manifest,
)
from ._output import detail, lock_change, status, warn
from ._progress import Live, Task

if _t.TYPE_CHECKING:
    from alai_core.semver import Version

    from ._fetch import Fetcher
    from ._resolve import Resolution


class Upgrade(Record):
    """What to upgrade while resolving: everything (``-U``), the collections named (``-P``), and every collection the
    groups named list in ``alai.toml`` (``--upgrade-group``), as uv's flags of the same names do."""

    __slots__ = ("collections", "everything", "groups")

    def __init__(
        self, *, everything: bool = False, collections: _c.Iterable[str] = (), groups: _c.Iterable[str] = ()
    ) -> None:
        self.everything = everything
        self.collections = frozenset(collections)
        self.groups = tuple(dict.fromkeys(groups))

    def __bool__(self) -> bool:
        return self.everything or bool(self.collections) or bool(self.groups)

    def names(self, manifest: Manifest | None) -> bool | set[str]:
        """What to upgrade, for the resolver: everything, the collections named, or nothing (False). A group is the
        collections it lists directly in ``manifest``, not what they depend on, as uv's ``--upgrade-group`` is; one
        ``manifest`` doesn't have is an error, as uv's is."""
        known = manifest.groups if manifest is not None else {}
        for group in self.groups:
            if group not in known:
                raise no_group("--upgrade-group", group, known)
        if self.everything:
            return True
        return set(self.collections) | {r.name for group in self.groups for r in known[group]} or False


def load_project(settings: Settings, why: str) -> tuple[RawManifest, Manifest]:
    """The project's ``alai.toml`` as written and as parsed; ``why`` says, in the error, why a command needs one.

    In a collection project, it's resolved with ``galaxy.yml``: see ``with_root``.
    """
    project = settings.project
    if project.config_file is None:
        raise no_project(project, why)
    raw = settings.manifest
    if raw is None:
        raw = raw_manifest(read_toml(project.config_file), project.config_file)
    return with_root(settings, raw)


def with_root(
    settings: Settings, raw: RawManifest, *, local: _c.Mapping[str, SourceCollection] | None = None
) -> tuple[RawManifest, Manifest]:
    """``raw``, the project's ``alai.toml``, as resolved: as it is, or in a collection project, with
    the requirements and ``requires_ansible`` its ``galaxy.yml`` and ``meta/runtime.yml`` give, which ``alai.toml``
    mustn't repeat. See ``design/collection-development.md``. ``local`` is another manifest's ``local``, read from
    the same sources moments ago, to use rather than read them again."""
    galaxy_file = settings.project.collection_file
    if galaxy_file is None:
        manifest = parse_manifest(raw)
        _fill_local(settings, manifest, local)
        return raw, manifest

    from ._galaxy import read_source_collection

    root = read_source_collection(galaxy_file)
    for key, value, instead in (
        ("collections", raw.collections, "galaxy.yml's dependencies"),
        ("requires_ansible", raw.requires_ansible, "meta/runtime.yml's requires_ansible"),
    ):
        if value:
            raise ConfigError(
                f"{raw.path}: 'project.{key}' can't be set beside a collection's galaxy.yml, "
                + f"{instead} say that; a [project.groups.<name>] holds collections only its tests need"
            )
    raw = RawManifest(
        raw.path,
        collections=list(root.dependencies),
        requires_ansible=root.requires_ansible,
        groups=raw.groups,
        overrides=raw.overrides,
        ignore_requires_ansible=raw.ignore_requires_ansible,
        sources=raw.sources,
    )
    manifest = parse_manifest(raw)
    manifest.root = root
    # Refused whatever the source, before any is read or downloaded: uv ignores a source for the project itself.
    if (source := manifest.sources.get(root.name)) is not None:
        raise ConfigError(
            f"[project.sources] takes {root.name} from {source}, but it's the project's own collection, which is "
            + "always the checkout"
        )
    _fill_local(settings, manifest, local)
    return raw, manifest


def _fill_local(settings: Settings, manifest: Manifest, local: _c.Mapping[str, SourceCollection] | None) -> None:
    if local is None:
        read_local(settings, manifest)
    else:
        manifest.local.update(local)


def source_dir(settings: Settings, path: str) -> str:
    """Where a path source is: its path, relative to the project root unless absolute, with ``~`` expanded."""
    return os.path.normpath(os.path.join(settings.project.root, os.path.expanduser(path)))


def read_local(settings: Settings, manifest: Manifest) -> None:
    """Fill ``manifest.local`` from each path source's ``galaxy.yml``, or ``MANIFEST.json``, and ``meta/runtime.yml``,
    read now, as uv reads a
    path dependency's ``pyproject.toml`` on every lock and sync. The collection there must be the one its
    ``[project.sources]`` entry names, and the directory mustn't overlap the collections directory, where a link to it,
    or an exact sync, would replace or remove it. A path to a tarball is hashed and read through the cache; a URL or
    a git repository needs the network, so ``resolve_manifest`` reads those."""
    for name, source in sorted(manifest.sources.items()):
        if isinstance(source, PathSource):
            manifest.local[name] = read_path_source(settings, name, source)


def read_path_source(settings: Settings, name: str | None, source: PathSource) -> SourceCollection:
    """The collection in a path source's directory, from its ``galaxy.yml``, or a built collection's
    ``MANIFEST.json``, checked to be ``name`` when given, and not to overlap the collections directory; or in the
    tarball it names, from its ``MANIFEST.json``."""
    from ._galaxy import read_collection_dir

    where = source_dir(settings, source.path)
    if os.path.isfile(where):
        if source.editable:
            raise ConfigError(
                f"{home_path(where)} is a tarball, which can't be editable{_for(name)}: give a directory to link to"
            )
        if source.version is not None:
            raise ConfigError(
                f"{home_path(where)} is a tarball, whose MANIFEST.json gives its version, so its source can't give "
                + f"another{_for(name)}: remove 'version'"
            )
        return _read_archive(settings, name, str(source), where)
    if not os.path.isdir(where):
        raise ConfigError(f"{home_path(where)} doesn't exist or isn't a directory" + _for(name))
    if not any(os.path.isfile(os.path.join(where, f)) for f in ("galaxy.yml", "MANIFEST.json")):
        raise ConfigError(
            f"{home_path(where)} has no galaxy.yml or MANIFEST.json, so it isn't a collection{_for(name)}"
        )
    collection = read_collection_dir(where, source.version, source=_entry(name))
    _check_named(name, source, collection)
    tree = os.path.realpath(settings.collections_dir)
    real = os.path.realpath(where)
    for inner, outer, what in ((real, tree, "is inside"), (tree, real, "holds")):
        if inside(inner, outer):
            raise ConfigError(
                f"{collection.name}'s source {home_path(where)} {what} the collections directory "
                + f"{home_path(settings.collections_dir)}; a source is installed from outside it"
            )
    return collection


def _entry(name: str | None) -> str:
    """Where a source was given, in messages: the ``[project.sources]`` entry for ``name``, or the command line."""
    return f"[project.sources] for {name}" if name is not None else "the command line"


def _for(name: str | None) -> str:
    """``_entry`` as a suffix, ``(from [project.sources] for ns.col)``, or nothing for the command line."""
    return f" (from {_entry(name)})" if name is not None else ""


def read_given_source(
    settings: Settings, given: str, *, editable: bool, relative_to: str | None
) -> tuple[SourceCollection, PathSource | UrlSource | GitSource]:
    """The collection in the directory, tarball or git repository ``given`` on the command line, to ``alai add`` or
    ``alai collection install``, and the source taking it from there. An ``http`` or ``https`` URL is downloaded,
    and a repository fetched, to read its name; a ``file`` URL is its absolute path, as uv writes one. A path is
    written relative to ``relative_to``, the project root for ``alai add``, as uv writes one given from a
    subdirectory, unless it was given as an absolute path; with None it's written absolute."""
    if is_git(given):
        if editable:
            raise ConfigError(f"{given} is a git repository, which can't be editable: give a checkout to link to")
        git = parse_git(given)
        with Live(Task(f"Fetching {git}...")):
            return read_git_source(settings, None, git), git
    if is_url(given) and not given.lower().startswith("file:"):
        if editable:
            raise ConfigError(f"{given} is a tarball, which can't be editable: give a directory to link to")
        from ._fetch import open_fetcher

        url = UrlSource(given)
        with open_fetcher(settings) as fetcher:
            return read_url_source(settings, None, url, fetcher), url
    if is_url(given):
        given = file_url_path(given)
    expanded = os.path.expanduser(given)
    where = os.path.abspath(expanded)
    written = where if relative_to is None or os.path.isabs(expanded) else os.path.relpath(where, relative_to)
    source = PathSource(written, editable=editable)
    return read_path_source(settings, None, source), source


def given_requirement(text: str) -> Requirement:
    """A requirement given on the command line, written as uv writes one: without the spaces it may be typed with.
    ConfigError if it isn't one."""
    try:
        return Requirement.parse(re.sub(r"\s+", "", text))
    except ValueError as e:
        raise ConfigError(str(e)) from e


def read_git_source(
    settings: Settings, name: str | None, source: GitSource, commit: str | None = None
) -> SourceCollection:
    """The collection at a git source's ``subdirectory`` of ``commit``, or of the commit its ``rev`` is at now when
    None, fetched into the cache if it isn't there, checked to be ``name`` when given."""
    from ._git import update

    commit, files = update(
        settings.cache_dir,
        source.url,
        source.rev,
        commit,
        offline=settings.offline,
        names=[name] if name is not None else (),
    )
    return _git_collection(name, source, commit, files)


def _git_collection(name: str | None, source: GitSource, commit: str, files: str) -> SourceCollection:
    """The collection at ``source``'s ``subdirectory`` of ``commit``, whose files are at ``files``, checked to be
    ``name`` when given."""
    from ._galaxy import read_collection_dir

    where = os.path.join(files, *source.subdirectory.split("/")) if source.subdirectory is not None else files
    at = f"{source.url}@{commit[:7]}"
    if not any(os.path.isfile(os.path.join(where, f)) for f in ("galaxy.yml", "MANIFEST.json")):
        if not os.path.isdir(where):
            message = f"{at} has no directory {source.subdirectory}"
        else:
            shown = f"{at} ({source.subdirectory})" if source.subdirectory is not None else at
            message = f"{shown} has no galaxy.yml or MANIFEST.json, so it isn't a collection"
        # As ansible-galaxy looks for collections one level down when a repository's root isn't one.
        found = sorted(
            d
            for d in os.listdir(files)
            if any(os.path.isfile(os.path.join(files, d, f)) for f in ("galaxy.yml", "MANIFEST.json"))
        )
        if found:
            message += f"; collections are in {', '.join(found)}: give one as its subdirectory"
        raise ConfigError(message + _for(name))
    collection = read_collection_dir(
        where,
        source.version,
        source=_entry(name) if name is not None else str(source),
        shown=f"{at}/{source.subdirectory}" if source.subdirectory is not None else at,
    )
    _check_named(name, source, collection)
    collection.commit = commit
    return collection


def _check_named(name: str | None, source: PathSource | GitSource, collection: SourceCollection) -> None:
    """Refuse ``collection``, read from ``source``, unless it's ``name`` or ``name`` is None."""
    if name is not None and collection.name != name:
        raise ConfigError(
            f"[project.sources] takes {name} from {source}, but its {collection.metadata} is for {collection.name}"
        )


def read_git_sources(
    settings: Settings, manifest: Manifest, locked: Lock | None, upgrade: bool | _c.Collection[str]
) -> None:
    """Fill ``manifest.local`` with each git source's collection, in parallel.

    One already in ``manifest.local``, as ``alai add`` fetches one to read its name, isn't read again. One locked from
    the same ``[project.sources]`` entry is taken from the lock, with no network, unless ``upgrade`` names it, as uv
    keeps a git dependency's locked commit until it's upgraded; one locked from the same repository and ``rev`` keeps
    its commit, read from the cache. Anything else resolves ``rev`` to its commit now, the repositories fetched in
    parallel as ``sync`` fetches them, and each collection then checked and kept here, in order by name, so the first
    to fail by name is the one reported.
    """
    from ._git import update_all

    before = parse_manifest(locked.manifest).sources if locked is not None else {}
    wanted: dict[str, tuple[GitSource, str | None]] = {}
    for name, source in sorted(manifest.sources.items()):
        if not isinstance(source, GitSource) or name in manifest.local:
            continue
        was = locked.get(name) if locked is not None else None
        if was is None or not isinstance(was.source, GitCommitSource) or selects(upgrade, name):
            wanted[name] = (source, None)
        elif before.get(name) == source:
            manifest.local[name] = was.as_source()
        else:
            wanted[name] = (source, was.source.commit if was.source.same_source(source) else None)
    if not wanted:
        return
    fetched = update_all(
        settings.cache_dir,
        [(source.url, source.rev, commit, name) for name, (source, commit) in wanted.items()],
        offline=settings.offline,
    )
    for name, (source, _) in wanted.items():
        commit, files = fetched[name]
        manifest.local[name] = _git_collection(name, source, commit, files)


def _read_archive(settings: Settings, name: str | None, shown: str, path: str) -> SourceCollection:
    """The collection in the tarball at ``path``, extracted into the artifact cache, by its checksum, if it isn't
    there yet: hashing a tarball costs little, and a tarball already extracted isn't read again."""
    from ._artifacts import GZIP_MAGIC, ArtifactCache

    try:
        with open(path, "rb") as f:
            magic = f.read(len(GZIP_MAGIC))
        if magic != GZIP_MAGIC:
            raise ConfigError(f"{home_path(path)} isn't a gzip file, so it isn't a collection tarball{_for(name)}")
        cksum = sha256_file(path)
        size = os.path.getsize(path)
    except OSError as e:
        raise ConfigError(f"can't read {home_path(path)}: {e.strerror or e}") from e
    return _archive_collection(name, shown, ArtifactCache(settings.cache_dir).add(path, cksum), cksum, size)


def _archive_collection(name: str | None, shown: str, extracted: str, cksum: str, size: int) -> SourceCollection:
    """The collection a tarball named ``shown`` holds, from the copy extracted at ``extracted``: its ``MANIFEST.json``
    and ``meta/runtime.yml``, checked to be ``name`` when given."""
    from ._galaxy import read_built_collection

    collection = read_built_collection(os.path.join(extracted, "MANIFEST.json"))
    if name is not None and collection.name != name:
        raise ConfigError(f"[project.sources] takes {name} from {shown}, but it holds {collection.name}")
    collection.cksum, collection.size = cksum, size
    return collection


def read_url_source(settings: Settings, name: str | None, source: UrlSource, fetcher: Fetcher) -> SourceCollection:
    """The collection in the tarball at a URL source, downloaded into the artifact cache, checked to be ``name`` when
    given."""
    from ._artifacts import ArtifactCache

    cksum, size, extracted = ArtifactCache(settings.cache_dir, fetcher).download(source.url)
    return _archive_collection(name, str(source), extracted, cksum, size)


def read_archives(
    settings: Settings,
    manifest: Manifest,
    fetcher: Fetcher,
    locked: Lock | None,
    upgrade: bool | _c.Collection[str],
) -> None:
    """Fill ``manifest.local`` with each URL source's collection, and check each tarball's against ``locked``.

    One already in ``manifest.local``, as ``alai add`` downloads one to read its name, isn't downloaded again. A URL
    locked from the same URL is taken from the lock, with no download, unless ``upgrade`` names it: what's
    behind a URL is assumed not to change, as uv assumes, and the checksum catches it at install if it does. A tarball
    on disk that no longer matches its locked checksum is an error unless ``upgrade`` names it, as uv makes it, since
    a lock is meant to pin what's installed: a new tarball at the same path takes ``-P``.
    """

    from ._artifacts import ArtifactCache
    from ._fetch import MAX_CONNECTIONS

    wanted: dict[str, UrlSource] = {}
    for name, source in sorted(manifest.sources.items()):
        was = locked.get(name) if locked is not None else None
        if isinstance(source, UrlSource) and (name not in manifest.local or not manifest.local[name].is_archive()):
            if (
                was is not None
                and was.source == ArchiveUrlSource(source.url)
                and was.cksum is not None
                and not selects(upgrade, name)
            ):
                manifest.local[name] = was.as_source()
            else:
                wanted[name] = source
        elif (
            isinstance(source, PathSource)
            and (now := manifest.local.get(name)) is not None
            and now.is_archive()
            and was is not None
            and was.source == ArchiveSource(source.path)
            and was.cksum != now.cksum
            and not selects(upgrade, name)
        ):
            raise ConfigError(
                f"{home_path(source_dir(settings, source.path))} has changed since {name} was locked from it: its "
                + f"SHA-256 is {now.cksum}, where alai.lock has {was.cksum}; run alai lock -P {name} to lock it "
                + "as it is now"
            )
    if not wanted:
        return
    # Downloaded in parallel, as sync downloads index artifacts, through one cache, which limits how many extract at
    # once. Each is checked, and kept, here, in order by name, so the first to fail by name is the one reported.
    cache = ArtifactCache(settings.cache_dir, fetcher)
    with concurrent.futures.ThreadPoolExecutor(MAX_CONNECTIONS, thread_name_prefix="alai-archive") as pool:
        futures = {name: pool.submit(cache.download, source.url) for name, source in wanted.items()}
    for name, future in futures.items():
        cksum, size, extracted = future.result()
        manifest.local[name] = _archive_collection(name, str(wanted[name]), extracted, cksum, size)


def resolve_project(
    settings: Settings,
    manifest: Manifest,
    raw: RawManifest,
    existing: Lock | None,
    *,
    upgrade: Upgrade | None = None,
    download: bool = False,
) -> tuple[Lock, Resolution]:
    """Resolve the project against its indexes, keeping ``existing``'s versions while they fit, except those
    ``upgrade`` names, and warn of anything the resolution reported. The lock isn't written. ``download`` is for a
    caller that installs next, as for ``resolve_manifest``."""
    names = upgrade.names(manifest) if upgrade is not None else False
    resolution = resolve_manifest(settings, manifest, locked=existing, upgrade=names, download=download)
    root = None
    if manifest.root is not None:
        root = LockedRoot(
            manifest.root.name,
            manifest.root.version,
            requires_ansible=manifest.root.requires_ansible,
            dependencies=tuple(sorted(r.name for r in manifest.collections)),
        )
    return Lock(raw, resolution.collections, root), resolution


def resolve_manifest(
    settings: Settings,
    manifest: Manifest,
    *,
    locked: Lock | None = None,
    installed: _c.Mapping[str, Version] | None = None,
    upgrade: bool | _c.Collection[str] = False,
    origin: str = "the project",
    download: bool = False,
) -> Resolution:
    """Resolve ``manifest`` against the configured indexes, with progress, and warn of anything the resolution
    reported. ``origin`` names where its requirements came from, in errors. With ``download``, for a caller that
    installs next, each index's download host is connected to while resolving."""
    from ._fetch import open_fetcher
    from ._index import IndexClient
    from ._resolve import resolve

    with open_fetcher(settings) as fetcher, Live(Task("Resolving collections...")) as task:
        read_archives(settings, manifest, fetcher, locked, upgrade)
        read_git_sources(settings, manifest, locked, upgrade)
        clients = [IndexClient(index, fetcher, settings.cache_dir) for index in settings.indexes]
        if download:
            for client in clients:
                client.preconnect_downloads()
        resolution = resolve(
            manifest,
            clients,
            locked=locked,
            installed=installed,
            upgrade=upgrade,
            origin=origin,
            report=lambda n, d: task.show(n, d),
        )
    for message in resolution.warnings:
        warn(message)
    return resolution


def lock_unchanged(existing: Lock | None, manifest: Manifest, lock: Lock) -> bool:
    """Whether resolving again left ``existing`` as it was: locked from the same inputs, to the same collections."""
    return (
        existing is not None
        and existing.is_current(manifest)
        and existing.collections == lock.collections
        and existing.root == lock.root
    )


def report_changes(
    existing: Lock | None, manifest: Manifest, lock: Lock, resolution: Resolution, *, inputs: bool = False
) -> None:
    """Say how ``lock`` differs from ``existing``: a line per collection added, updated or removed, each with its
    reason under it with ``-v``, as ``alai lock`` reports them. With ``inputs``, for a check that's failing, also what
    in ``alai.toml``, and in each directory source's ``galaxy.yml`` and ``meta/runtime.yml``, differs from what
    ``existing`` was locked from, which is the only sign of a change that leaves every version as it was."""
    for name, line in changes(existing, lock):
        lock_change(line)
        if (reason := resolution.reasons.get(name)) is not None:
            detail(reason)
    if inputs and existing is not None and (found := input_changes(existing, manifest)):
        status("alai.toml has changed since alai.lock was made:")
        for line in found:
            status(f"  {line}")
    for directory, lines in local_changes(existing, manifest) if inputs and existing is not None else []:
        status(f"{directory} has changed since alai.lock was made:")
        for line in lines:
            status(f"  {line}")
