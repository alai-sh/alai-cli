# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection path``: where a collection's files are on disk, for tools such as alai-doc to read: the
collection project's own checkout, else installed, else its locked version, else the newest cached, else the latest
on the indexes, downloaded into the cache. ``ansible.builtin`` and ``eda.builtin`` are ansible-core's and
ansible-rulebook's source (``_builtin``). The design is in ``design/collection-path.md``."""

from __future__ import annotations

import glob
import json
import os
import sys
import typing as _t

from alai_core.semver import Version

from ._common import EXIT_ERROR, EXIT_NOT_FOUND, Error, Record
from ._config import Settings
from ._output import debug, error, warn
from ._plugins import BUILTINS

if _t.TYPE_CHECKING:
    from alai_core.index import CollectionVersion

    from ._fetch import Fetcher
    from ._index import IndexClient
    from ._install import Installed

PROJECT = "project"
"""The collection project's own collection, read from its checkout."""
INSTALLED = "installed"
LOCKED = "locked"
"""The version ``alai.lock`` has, from the cache."""
CACHED = "cached"
DOWNLOADED = "downloaded"


class Located(Record):
    """A collection's files on disk."""

    __slots__ = ("latest", "name", "origin", "path", "version")

    def __init__(self, name: str, version: str | None, path: str, origin: str, latest: str | None = None) -> None:
        self.name = name
        self.version = version
        self.path = path
        self.origin = origin
        """``PROJECT``, ``INSTALLED``, ``LOCKED``, ``CACHED`` or ``DOWNLOADED``."""
        self.latest = latest
        """For a ``CACHED`` collection, the latest version on the indexes when it's newer; else None."""


def collection_path(settings: Settings, names: list[str], *, download: bool = True, output_format: str = "text") -> int:
    from alai_core.collection import check_name

    from ._fetch import open_fetcher
    from ._find import installed_collections

    project = project_collection(settings)
    installed = installed_collections(settings) or {}

    def one(name: str) -> Located | str | None:
        """The collection's files, or why they can't be had: one collection failing doesn't fail the rest."""
        try:
            check_name(name)
            return locate(settings, name, fetcher, project=project, installed=installed, download=download)
        except (Error, ValueError) as e:
            return str(e)

    with open_fetcher(settings) as fetcher:
        found = {name: one(name) for name in names}

    if output_format == "json":
        _print_json(project, found)
    else:
        for name, item in found.items():
            if isinstance(item, str):
                error(f"can't find {name}'s files because {item}")
            elif item is None:
                warn(f"no index has {name}" if download else f"{name} isn't on disk")
            else:
                print(item.path)
    if any(isinstance(i, str) for i in found.values()):
        return EXIT_ERROR
    return EXIT_NOT_FOUND if None in found.values() else 0


def project_collection(settings: Settings) -> str | None:
    """The collection a collection project is, from its ``galaxy.yml``, or None outside one."""
    from ._galaxy import galaxy_metadata

    if settings.project.collection_file is None:
        return None
    return galaxy_metadata(settings.project.collection_file).get_fqcn()


def locate(
    settings: Settings,
    name: str,
    fetcher: Fetcher,
    *,
    project: str | None,
    installed: dict[str, Installed],
    download: bool = True,
) -> Located | None:
    """Collection ``name``'s files, or None if no index has it, or without ``download``, if it isn't on disk."""
    if name in BUILTINS:
        from ._builtin import locate_builtin

        builtin = locate_builtin(settings, fetcher, name, download=download)
        return Located(name, *builtin) if builtin is not None else None
    if name == project and settings.project.collection_file is not None:
        from ._galaxy import galaxy_metadata

        version = galaxy_metadata(settings.project.collection_file).get_version()
        return Located(name, str(version), settings.project.root, PROJECT)
    have = installed.get(name)
    if have is not None:
        return Located(name, str(have.version) if have.version is not None else None, have.path, INSTALLED)
    if (found := _locked(settings, name, download=download)) is not None:
        return found
    if (found := _cached(settings.cache_dir, name)) is not None:
        if download:
            found.latest = _newer(settings, fetcher, name, found.version)
        return found
    return _download(settings, fetcher, name) if download else None


def _locked(settings: Settings, name: str, *, download: bool) -> Located | None:
    """The locked version from the cache, downloading it if it isn't there, when it comes from an index or a
    tarball."""
    from ._artifacts import ArtifactCache
    from ._lock import lock_path, read_lock
    from ._sync import _fetch

    if settings.project.config_file is None:
        return None
    lock = read_lock(lock_path(settings.project.root))
    entry = lock.get(name) if lock is not None else None
    if entry is None or entry.cksum is None or entry.size is None or entry.is_git():
        return None
    cached = ArtifactCache(settings.cache_dir).cached(entry.cksum)
    if cached is None and not download:
        return None
    path = cached or _fetch([entry], settings).paths[name]
    return Located(name, str(entry.version), path, LOCKED if cached else DOWNLOADED)


def _cached(cache_dir: str, name: str) -> Located | None:
    """The newest version of ``name`` in the artifact cache."""
    from ._artifacts import LAYOUT

    best: tuple[Version, str] | None = None
    for manifest in glob.glob(os.path.join(glob.escape(cache_dir), LAYOUT, "*", "MANIFEST.json")):
        try:
            with open(manifest, "rb") as f:
                info = json.load(f)["collection_info"]
            if f"{info['namespace']}.{info['name']}" != name:
                continue
            version = Version(info["version"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if best is None or version > best[0]:
            best = (version, os.path.dirname(manifest))
    return Located(name, str(best[0]), best[1], CACHED) if best is not None else None


def _latest(settings: Settings, fetcher: Fetcher, name: str) -> tuple[IndexClient, CollectionVersion] | None:
    """The first index with ``name`` and its latest usable version: not yanked or unreadable, and not a pre-release
    unless every version is."""
    from ._index import IndexClient

    for index in settings.indexes:
        client = IndexClient(index, fetcher, settings.cache_dir)
        if not client.has(name):
            continue
        listed = client.versions(name)
        usable = [v for v in (listed.versions if listed else ()) if not v.yanked and v.problem is None]
        final = [v for v in usable if not v.version.is_prerelease]
        if candidates := final or usable:
            return client, max(candidates, key=lambda v: v.version)
    return None


def _newer(settings: Settings, fetcher: Fetcher, name: str, version: str | None) -> str | None:
    """The latest version on the indexes, if it's newer than ``version``; None if it isn't, or that can't be told."""
    if version is None:
        return None
    try:
        best = _latest(settings, fetcher, name)
    except Error as e:
        debug(f"can't tell whether {name} has a newer version: {e}")
        return None
    return str(best[1].version) if best is not None and best[1].version > Version(version) else None


def _download(settings: Settings, fetcher: Fetcher, name: str) -> Located | None:
    from ._artifacts import Artifact, ArtifactCache
    from ._index import artifact_url
    from ._progress import Live, Task

    best = _latest(settings, fetcher, name)
    if best is None:
        return None
    client, line = best
    url = artifact_url(client.config(), name, line.version, line.sha256)
    artifact = Artifact(name, line.version, url=url, cksum=line.sha256, size=line.size)
    cache = ArtifactCache(settings.cache_dir, fetcher)
    origin = CACHED if cache.cached(line.sha256) is not None else DOWNLOADED
    with Live(Task("Fetching collection...", total=1)) as task:

        def report(artifact: Artifact, state: str | None) -> None:
            if state is None:
                task.end(artifact.name)
            elif state != "waiting":
                task.start(artifact.name, state)

        path = cache.get_all([artifact], report)[name]
    return Located(name, str(line.version), path, origin)


def _print_json(project: str | None, found: dict[str, Located | str | None]) -> None:
    def entry(item: Located | str | None) -> dict[str, object] | None:
        if isinstance(item, str):
            return {"error": item}
        if item is None:
            return None
        return {
            "version": item.version,
            "path": os.path.abspath(item.path),
            "origin": item.origin,
            "latest": item.latest,
        }

    result = {"v": 1, "project": project, "collections": {name: entry(item) for name, item in found.items()}}
    json.dump(result, sys.stdout, indent=2)
    print()
