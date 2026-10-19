# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Reading an alai index: ``config.json``, ``collections.json`` and the per-collection index files.

The format is the index spec in galaxy-sync's ``docs/index-spec/README.md``. Parsing follows its rules for clients:
unknown fields are ignored, a line with a newer ``v`` is skipped and counted, and a version whose requirements can't be
read is kept but marked unusable, for the resolver to skip with a warning. The design is in
``design/index-client.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import typing as _t
import urllib.parse

from ._common import Error, Record
from ._config import Index
from ._fetch import Fetcher
from ._manifest import collection_name
from ._output import debug, trace, warn
from ._pep440 import SpecifierSet
from ._semver import Version, VersionReq

FORMAT = 1
"""The highest ``config.json`` format this alai reads."""
LINE_VERSION = 1
"""The highest index line ``v`` this alai reads."""
COLLECTIONS_VERSION = 1
"""The ``collections.json`` ``v`` this alai reads."""

_MAX_MOVES = 5
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_T = _t.TypeVar("_T")
_DL_PLACEHOLDERS = ("{sha256-checksum}", "{sha256-prefix}", "{namespace}", "{name}", "{version}")


class IndexDataError(Error):
    """An index file can't be used: it isn't valid, or it needs a newer alai."""


class IndexConfig(Record):
    """An index's ``config.json``, with its paths made absolute."""

    __slots__ = ("collections_url", "dl", "format", "index_url", "root")

    def __init__(self, *, root: str, format: int, index_url: str, collections_url: str, dl: str) -> None:  # noqa: A002
        self.root = root
        """The index root URL, after following any ``moved-to``."""
        self.format = format
        self.index_url = index_url
        """The directory holding ``<namespace>/<name>.jsonl``, ending in ``/``."""
        self.collections_url = collections_url
        self.dl = dl
        """The artifact URL template."""


class Listed(Record):
    """A collection's entry in ``collections.json``."""

    __slots__ = ("sha256", "updated")

    def __init__(self, updated: str, sha256: str | None = None) -> None:
        self.updated = updated
        """When its index file last changed, or empty if the index didn't say."""
        self.sha256 = sha256
        """The SHA-256 of its index file, or None if the index doesn't publish one."""


class CollectionVersion(Record):
    """One line of an index file: one version of a collection."""

    __slots__ = (
        "cksum",
        "deprecated",
        "deps",
        "name",
        "origin",
        "problem",
        "published",
        "requires_ansible",
        "size",
        "upstream_conflict",
        "version",
        "yanked",
    )

    def __init__(
        self,
        name: str,
        version: Version,
        *,
        deps: tuple[tuple[str, VersionReq], ...],
        cksum: str,
        size: int,
        requires_ansible: SpecifierSet | None,
        yanked: bool,
        origin: str,
        published: str,
        deprecated: bool | str = False,
        upstream_conflict: bool = False,
        problem: str | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.deps = deps
        """``(name, requirement)`` for each dependency, in the order listed."""
        self.cksum = cksum
        self.size = size
        self.requires_ansible = requires_ansible
        """The ansible-core versions it supports, or None for any."""
        self.yanked = yanked
        self.origin = origin
        self.published = published
        self.deprecated = deprecated
        """False, True, or a message for the user."""
        self.upstream_conflict = upstream_conflict
        self.problem = problem
        """Why this version can't be used, such as a dependency requirement ansible-galaxy can't read, or None.

        ``deps`` and ``requires_ansible`` hold what could be read."""


class IndexFile(Record):
    """The usable lines of one collection's index file, in the file's order (publication, not version)."""

    __slots__ = ("invalid", "name", "newer", "versions")

    def __init__(
        self, name: str, versions: tuple[CollectionVersion, ...], *, newer: int = 0, invalid: tuple[str, ...] = ()
    ) -> None:
        self.name = name
        self.versions = versions
        self.newer = newer
        """Lines skipped because their ``v`` needs a newer alai."""
        self.invalid = invalid
        """Why each line that couldn't be read at all was skipped."""


class IndexClient:
    """One configured index, read through a shared Fetcher.

    The cache for an index lives under ``<cache_dir>/index/<host>-<hash>/``, keyed by its root URL rather than its
    name, so renaming an index in ``alai.toml`` doesn't lose the cache and two names for one URL share it.

    ``collections.json`` is fetched alongside ``config.json``, from where the cached config says it is, and gives
    each index file's SHA-256: a cached file that matches is used without a request (the spec's "Cached index
    files"), so a run where nothing changed costs those two requests.
    """

    def __init__(self, index: Index, fetcher: Fetcher, cache_dir: str) -> None:
        self.index = index
        self._fetcher = fetcher
        self._cache_dir = cache_dir
        self._lock = threading.Lock()
        self._config: IndexConfig | None = None
        self._collections: dict[str, Listed] | None = None
        self._files: dict[str, IndexFile | None] = {}

    def config(self) -> IndexConfig:
        """The index's ``config.json``, following ``moved-to`` to where the index is now."""
        with self._lock:
            if self._config is None:
                self._prefetch_collections()
                self._config = self._load_config()
            return self._config

    def collections(self) -> dict[str, Listed]:
        """Every collection in the index, from ``collections.json``."""
        config = self.config()
        with self._lock:
            if self._collections is None:
                url = config.collections_url
                data = self._fetcher.get(url, self._cache_path(config.root, url))
                if data is None:
                    raise IndexDataError(f"{url} not found")
                self._collections = parse_collections(data, url)
            return self._collections

    def has(self, name: str) -> bool:
        """Whether the index lists collection ``name``."""
        return name in self.collections()

    def prefetch(self, names: list[str]) -> None:
        """Start fetching the index files for ``names`` that aren't current in the cache, so a later ``versions``
        call finds them ready."""
        config = self.config()
        listing = self.collections()
        for name in names:
            url = self._file_url(config, name)
            cache_path = self._cache_path(config.root, url)
            if self._current(listing.get(name), cache_path) is None:
                _ = self._fetcher.fetch(url, cache_path)

    def versions(self, name: str) -> IndexFile | None:
        """The index file for collection ``name``, or None if the index doesn't have it."""
        with self._lock:
            if name in self._files:
                return self._files[name]
        config = self.config()
        listed = self.collections().get(name)
        url = self._file_url(config, name)
        cache_path = self._cache_path(config.root, url)

        data = self._current(listed, cache_path)
        if data is not None:
            trace(f"{url}: the cached copy matches collections.json")
        else:
            data = self._fetcher.get(url, cache_path)
            if data is not None and listed and listed.sha256 and _sha256(data) != listed.sha256:
                # A cache on the way kept the old file, or the listing is the older of the two.
                trace(f"{url}: doesn't match collections.json, fetching again past any cache")
                data = self._fetcher.refresh(url, cache_path)
                if data is not None and _sha256(data) != listed.sha256:
                    debug(f"{url} still doesn't match {config.collections_url}; using it as served")
        result = None if data is None else parse_index_file(data, name, url)
        with self._lock:
            return self._files.setdefault(name, result)

    def _current(self, listed: Listed | None, cache_path: str | None) -> bytes | None:
        """The cached copy of an index file if its SHA-256 is the one ``collections.json`` lists, else None."""
        if listed is None or listed.sha256 is None:
            return None
        data = self._fetcher.cached(cache_path)
        return data if data is not None and _sha256(data) == listed.sha256 else None

    def _prefetch_collections(self) -> None:
        """Start fetching ``collections.json`` from where the cached ``config.json`` says it is, so it arrives with
        ``config.json`` rather than after it. If the config has changed, the guess is just an unused request."""
        root = self.index.url
        url = urllib.parse.urljoin(root, "config.json")
        data = self._fetcher.cached(self._cache_path(root, url))
        if data is None:
            return
        try:
            guess, moved_to = parse_config(data, root, url)
        except IndexDataError:
            return
        if moved_to is None:
            _ = self._fetcher.fetch(guess.collections_url, self._cache_path(root, guess.collections_url))

    def _load_config(self) -> IndexConfig:
        root = self.index.url
        seen = [root]
        for _ in range(_MAX_MOVES + 1):
            url = urllib.parse.urljoin(root, "config.json")
            data = self._fetcher.get(url, self._cache_path(root, url))
            if data is None:
                raise IndexDataError(f"{url} not found: is {root} an alai index?")
            config, moved_to = parse_config(data, root, url)
            if moved_to is None:
                return config
            target = moved_to if moved_to.endswith("/") else f"{moved_to}/"
            if root.startswith("https://") and not target.startswith("https://"):
                raise IndexDataError(f"{url}: refusing to follow moved-to from https to {target}")
            if target in seen:
                raise IndexDataError(f"{url}: moved-to loops back to {target}")
            warn(f"index '{self.index.name}' has moved to {target}; update its url (from {self.index.source})")
            seen.append(target)
            root = target
        raise IndexDataError(f"{self.index.url}: more than {_MAX_MOVES} moved-to hops")

    def _file_url(self, config: IndexConfig, name: str) -> str:
        namespace, short = collection_name(name).split(".")
        return f"{config.index_url}{namespace}/{short}.jsonl"

    def _cache_path(self, root: str, url: str) -> str | None:
        """Where ``url``'s response is cached: its path under the index's own cache directory."""
        if root.startswith("file:"):
            return None  # Already on disk.
        parts = urllib.parse.urlsplit(root)
        host = re.sub(r"[^A-Za-z0-9.-]", "_", parts.hostname or "index")
        directory = f"{host}-{hashlib.sha256(root.encode()).hexdigest()[:16]}"
        relative = url[len(root) :] if url.startswith(root) else f"_other/{hashlib.sha256(url.encode()).hexdigest()}"
        return os.path.join(self._cache_dir, "index", directory, *relative.split("/"))


def parse_config(data: bytes, root: str, url: str) -> tuple[IndexConfig, str | None]:
    """Read ``config.json``: the highest format this alai reads, and the ``moved-to`` URL if the index has moved."""
    obj = _json_object(data, url)
    formats = obj.get("formats")
    if not isinstance(formats, dict):
        raise IndexDataError(f"{url}: 'formats' must be an object")
    offered = sorted(int(k) for k in formats if isinstance(k, str) and k.isdecimal())  # pyright: ignore[reportUnknownVariableType]
    readable = [n for n in offered if n <= FORMAT]
    if not readable:
        listed = ", ".join(str(n) for n in offered) or "none"
        raise IndexDataError(f"{root} needs a newer alai: it offers index format {listed}, this alai reads {FORMAT}")
    number = readable[-1]
    paths = formats[str(number)]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(paths, dict):
        raise IndexDataError(f"{url}: 'formats.{number}' must be an object")
    index_path = paths.get("index")  # pyright: ignore[reportUnknownVariableType]
    collections_path = paths.get("collections")  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(index_path, str) or not isinstance(collections_path, str):
        raise IndexDataError(f"{url}: 'formats.{number}' must have 'index' and 'collections' paths")

    dl = obj.get("dl")
    if not isinstance(dl, str) or not any(p in dl for p in _DL_PLACEHOLDERS):
        raise IndexDataError(f"{url}: 'dl' must be a URL template with a placeholder such as {{sha256-checksum}}")
    moved_to = obj.get("moved-to")
    if moved_to is not None and not isinstance(moved_to, str):
        raise IndexDataError(f"{url}: 'moved-to' must be a URL or null")

    index_url = urllib.parse.urljoin(root, index_path)
    config = IndexConfig(
        root=root,
        format=number,
        index_url=index_url if index_url.endswith("/") else f"{index_url}/",
        collections_url=urllib.parse.urljoin(root, collections_path),
        dl=dl,
    )
    return config, moved_to or None


def parse_collections(data: bytes, url: str) -> dict[str, Listed]:
    """Read ``collections.json``: each collection's entry, by ``namespace.name``.

    A missing or malformed ``updated`` or ``sha256`` is left out rather than an error: neither is needed to resolve,
    and without ``sha256`` the client just revalidates the index file as usual.
    """
    obj = _json_object(data, url)
    version = obj.get("v")
    if isinstance(version, int) and version > COLLECTIONS_VERSION:
        raise IndexDataError(f"{url} needs a newer alai: it's version {version}, this alai reads {COLLECTIONS_VERSION}")
    namespaces = obj.get("namespaces")
    if version != COLLECTIONS_VERSION or not isinstance(namespaces, dict):
        raise IndexDataError(f"{url}: expected 'v': {COLLECTIONS_VERSION} and a 'namespaces' object")
    result: dict[str, Listed] = {}
    for namespace, entries in namespaces.items():  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(entries, dict):
            raise IndexDataError(f"{url}: namespace '{namespace}' must be an object")
        for name, entry in entries.items():  # pyright: ignore[reportUnknownVariableType]
            fields: dict[str, object] = entry if isinstance(entry, dict) else {}  # pyright: ignore[reportUnknownVariableType]
            updated = fields.get("updated")
            sha256 = fields.get("sha256")
            result[f"{namespace}.{name}"] = Listed(
                updated if isinstance(updated, str) else "",
                sha256 if isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256) else None,
            )
    return result


def parse_index_file(data: bytes, name: str, url: str) -> IndexFile:
    """Read collection ``name``'s index file: one JSON object per line, blank lines ignored."""
    versions: list[CollectionVersion] = []
    invalid: list[str] = []
    newer = 0
    seen: set[str] = set()
    for number, line in enumerate(data.split(b"\n"), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            invalid.append(f"{url}:{number}: not valid JSON")
            continue
        if not isinstance(obj, dict):
            invalid.append(f"{url}:{number}: not a JSON object")
            continue
        v = obj.get("v")  # pyright: ignore[reportUnknownVariableType]
        if isinstance(v, int) and not isinstance(v, bool) and v > LINE_VERSION:
            newer += 1
            continue
        try:
            version = _parse_line(obj, name)
        except ValueError as e:
            invalid.append(f"{url}:{number}: {e}")
            continue
        if version.version.text in seen:
            invalid.append(f"{url}:{number}: version {version.version} is listed more than once")
            continue
        seen.add(version.version.text)
        versions.append(version)
    return IndexFile(name, tuple(versions), newer=newer, invalid=tuple(invalid))


def artifact_url(config: IndexConfig, version: CollectionVersion) -> str:
    """Where to download ``version``'s artifact, from the ``dl`` template."""
    namespace, name = version.name.split(".")
    return (
        config.dl.replace("{sha256-checksum}", version.cksum)
        .replace("{sha256-prefix}", version.cksum[:2])
        .replace("{namespace}", namespace)
        .replace("{name}", name)
        .replace("{version}", urllib.parse.quote(version.version.text, safe=""))
    )


def _parse_line(obj: dict[str, object], name: str) -> CollectionVersion:
    """One index line as a version, raising ValueError if it can't be read at all."""
    if obj.get("v") != LINE_VERSION or isinstance(obj.get("v"), bool):
        raise ValueError(f"'v' must be {LINE_VERSION}")
    if obj.get("name") != name:
        raise ValueError(f"'name' must be '{name}', the file's collection")
    vers = _field(obj, "vers", str)
    try:
        version = Version(vers)
    except ValueError as e:
        raise ValueError(str(e)) from None
    cksum = _field(obj, "cksum", str)
    if not _SHA256_RE.fullmatch(cksum):
        raise ValueError(f"{vers}: 'cksum' must be a lowercase hex sha256")
    size = _field(obj, "size", int)
    if size < 0:
        raise ValueError(f"{vers}: 'size' must not be negative")
    yanked = _field(obj, "yanked", bool)
    origin = _field(obj, "origin", str)
    published = _field(obj, "published", str)
    raw_deps = _t.cast("list[object]", _field(obj, "deps", list))
    deprecated = obj.get("deprecated", False)
    if not isinstance(deprecated, (bool, str)):
        raise ValueError(f"{vers}: 'deprecated' must be true or a message")
    upstream_conflict = obj.get("upstream_conflict", False)
    if not isinstance(upstream_conflict, bool):
        raise ValueError(f"{vers}: 'upstream_conflict' must be a boolean")

    # What follows makes the version unusable rather than unreadable: the line is fine, its metadata isn't.
    problems: list[str] = []
    deps: list[tuple[str, VersionReq]] = []
    for dep in raw_deps:
        dep_name = dep.get("name") if isinstance(dep, dict) else None  # pyright: ignore[reportUnknownVariableType]
        req = dep.get("req") if isinstance(dep, dict) else None  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(dep_name, str) or not isinstance(req, str):
            raise ValueError(f"{vers}: each of 'deps' must have a 'name' and a 'req' string")
        try:
            deps.append((collection_name(dep_name), VersionReq.parse_index(req)))
        except ValueError as e:
            problems.append(f"dependency {dep_name}: {e}")

    requires_ansible = None
    text = obj.get("requires_ansible")
    if text is not None:
        if not isinstance(text, str):
            raise ValueError(f"{vers}: 'requires_ansible' must be a string")
        try:
            requires_ansible = SpecifierSet(text)
        except ValueError as e:
            problems.append(f"requires_ansible '{text}': {e}")

    return CollectionVersion(
        name,
        version,
        deps=tuple(deps),
        cksum=cksum,
        size=size,
        requires_ansible=requires_ansible,
        yanked=yanked,
        origin=origin,
        published=published,
        deprecated=deprecated,
        upstream_conflict=upstream_conflict,
        problem="; ".join(problems) or None,
    )


def _field(obj: dict[str, object], key: str, kind: type[_T]) -> _T:
    value = obj.get(key)
    # bool is an int, so a size of true isn't accepted as 1.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ValueError(f"'{key}' is required and must be {_KIND_NAMES[kind]}")
    return value


_KIND_NAMES: dict[type, str] = {str: "a string", int: "an integer", bool: "a boolean", list: "an array"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_object(data: bytes, url: str) -> dict[str, object]:
    try:
        obj = json.loads(data)
    except ValueError as e:
        raise IndexDataError(f"{url}: not valid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise IndexDataError(f"{url}: expected a JSON object")
    return obj  # pyright: ignore[reportUnknownVariableType]
