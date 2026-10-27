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
import os
import re
import threading
import typing as _t
import urllib.parse

from . import _native
from ._common import SHA256_RE, Error, Record, json_object
from ._config import Index
from ._fetch import Fetcher
from ._manifest import collection_name
from ._output import debug, trace, warn
from ._pep440 import SpecifierSet
from ._semver import Version, VersionReq

if _t.TYPE_CHECKING:
    from ._native import _IndexFields

FORMAT = 1
"""The highest ``config.json`` format this alai reads."""
COLLECTIONS_VERSION = 1
"""The ``collections.json`` ``v`` this alai reads."""

_MAX_MOVES = 5
_Prefetch = _t.Literal["collections", "plugins"]
_USUAL_PATHS: dict[_Prefetch, str] = {"collections": "collections.json", "plugins": "plugins.jsonl"}
"""Where the spec's layout, and ``index.alai.sh``, keep the listing and the plugin index, relative to the root."""
_DL_PLACEHOLDERS = ("{sha256-checksum}", "{sha256-prefix}", "{namespace}", "{name}", "{version}")
_HASHED = ("{sha256-checksum}", "{sha256-prefix}")
"""The index template's placeholders for an index file's SHA-256, from ``collections.json``."""


class IndexDataError(Error):
    """An index file can't be used: it isn't valid, or it needs a newer alai."""


class IndexConfig(Record):
    """An index's ``config.json``, with its paths made absolute."""

    __slots__ = ("collections_url", "dl", "format", "index", "plugins_url", "root")

    def __init__(
        self,
        *,
        root: str,
        format: int,  # noqa: A002
        index: str,
        collections_url: str | None,
        dl: str,
        plugins_url: str | None = None,
    ) -> None:
        self.root = root
        """The index root URL, after following any ``moved-to``."""
        self.format = format
        self.index = index
        """The index file URL template, as given, relative to ``root``: ``index/{namespace}/{name}.jsonl``, or one by
        SHA-256 such as ``index/{sha256-prefix}/{sha256-checksum}.jsonl``."""
        self.collections_url = collections_url
        """The listing, ``collections.json``, or None if the index has none, such as a host that serves only index
        files, or one that shows each user only what they can see: whether it has a collection is then only known from
        its index file."""
        self.dl = dl
        """The artifact URL template."""
        self.plugins_url = plugins_url
        """The plugin index, ``plugins.jsonl``, or None if the index has none."""

    @property
    def hashed(self) -> bool:
        """Whether index files are found by their SHA-256, which only ``collections.json`` gives."""
        return any(p in self.index for p in _HASHED)


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
        "problem",
        "published",
        "requires_ansible",
        "size",
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
        published: str,
        deprecated: bool | str = False,
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
        self.published = published
        self.deprecated = deprecated
        """False, True, or a message for the user."""
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

    ``collections.json`` is fetched alongside ``config.json``, from where the cached config says it is (or, with
    none cached, where the spec's layout puts it), and gives each index file's SHA-256: a cached file that matches is
    used without a request (the spec's "Cached index files"), so a run where nothing changed costs those two requests.
    Index files are cached by collection, not URL, so an index whose template finds them by SHA-256 keeps one copy of
    each rather than one for every change.

    An index with no listing finds its files by name, and each is revalidated whenever a run reads it: whether the
    index has a collection is whether its file is there, which ``has`` waits for, so a caller that wants several
    asks for them with ``prefetch`` first.
    """

    def __init__(
        self,
        index: Index,
        fetcher: Fetcher,
        cache_dir: str,
        *,
        prefetch: _Prefetch | None = "collections",
    ) -> None:
        self.index = index
        self._prefetch: _Prefetch | None = prefetch
        """Which file to start fetching with ``config.json``, ``collections.json`` or the plugin index, so the two cost
        one round trip: the one the command reads first, or None."""
        self._fetcher = fetcher
        self._cache_dir = cache_dir
        self._lock = threading.Lock()
        self._config: IndexConfig | None = None
        self._collections: dict[str, Listed] | None = None
        self._relisted = False
        """Whether ``collections.json`` has been fetched again past any cache, for an index file it named by SHA-256
        that's gone."""
        self._files: dict[str, IndexFile | None] = {}

    def config(self) -> IndexConfig:
        """The index's ``config.json``, following ``moved-to`` to where the index is now."""
        with self._lock:
            if self._config is None:
                if self._prefetch is not None:
                    self._prefetch_file(self._prefetch)
                self._config = self._load_config()
            return self._config

    def preconnect_downloads(self) -> None:
        """Start connecting to the host artifacts come from, for a command about to download them."""
        try:
            dl = self.config().dl
        except Error:
            return  # what resolving reports
        parts = urllib.parse.urlsplit(dl)
        self._fetcher.preconnect(f"{parts.scheme}://{parts.netloc}/")

    def collections(self) -> dict[str, Listed] | None:
        """Every collection in the index, from ``collections.json``, or None if the index has no listing."""
        config = self.config()
        url = config.collections_url
        if url is None:
            return None
        with self._lock:
            if self._collections is None:
                data = self._fetcher.get(url, self._cache_path(config.root, url))
                if data is None:
                    raise IndexDataError(f"{url} not found")
                self._collections = parse_collections(data, url)
            return self._collections

    def plugins(self) -> bytes | None:
        """The index's plugin index, ``plugins.jsonl``, or None if it has none."""
        config = self.config()
        if config.plugins_url is None:
            return None
        data = self._fetcher.get(config.plugins_url, self._cache_path(config.root, config.plugins_url))
        if data is None:
            debug(f"{config.plugins_url} not found, though {config.root}config.json lists it")
        return data

    def has(self, name: str) -> bool:
        """Whether the index has collection ``name``: whether its listing has it, or with no listing, whether its index
        file is there, which waits for that request."""
        known = self.known(name)
        return self.versions(name) is not None if known is None else known

    def known(self, name: str) -> bool | None:
        """Whether the index has collection ``name``, if that's known without waiting for more than the listing: from
        the listing, or with none, from its index file once read. None if only the index file can say."""
        listing = self.collections()
        if listing is not None:
            return name in listing
        with self._lock:
            return self._files[name] is not None if name in self._files else None

    def prefetch(self, names: list[str]) -> None:
        """Start fetching the index files for ``names`` that aren't current in the cache, so a later ``versions``
        call finds them ready. With no listing, that's every one of them."""
        config = self.config()
        listing = self.collections() or {}
        for name in names:
            listed = listing.get(name)
            if config.hashed and (listed is None or listed.sha256 is None):
                continue  # Not in the index, or an error ``versions`` reports.
            cache_path = self._file_cache_path(config.root, name)
            if self._current(listed, cache_path) is None:
                _ = self._fetcher.fetch(index_file_url(config, name, listed and listed.sha256), cache_path, what=name)

    def versions(self, name: str) -> IndexFile | None:
        """The index file for collection ``name``, or None if the index doesn't have it."""
        with self._lock:
            if name in self._files:
                return self._files[name]
        config = self.config()
        listed = (self.collections() or {}).get(name)
        cache_path = self._file_cache_path(config.root, name)

        if config.hashed and listed is None:
            # Only the listing says where a collection's file is, so one it doesn't list isn't in the index.
            url, data = None, None
        else:
            url = index_file_url(config, name, listed and listed.sha256)
            data = self._current(listed, cache_path)
            if data is not None:
                trace(f"{url} ({name}): the cached copy matches collections.json")
            elif config.hashed and config.collections_url is not None:  # parse_config refuses one without
                url, data = self._fetch_hashed(config, config.collections_url, name, url, cache_path)
            else:
                data = self._fetcher.get(url, cache_path, what=name)
                if data is not None and listed and listed.sha256 and _sha256(data) != listed.sha256:
                    # A cache on the way kept the old file, or the listing is the older of the two.
                    trace(f"{url} ({name}): doesn't match collections.json, fetching again past any cache")
                    data = self._fetcher.refresh(url, cache_path, what=name)
                    if data is not None and _sha256(data) != listed.sha256:
                        debug(f"{url} still doesn't match {config.collections_url}; using it as served")
        result = None if data is None or url is None else parse_index_file(data, name, url)
        with self._lock:
            return self._files.setdefault(name, result)

    def _fetch_hashed(
        self, config: IndexConfig, listing_url: str, name: str, url: str, cache_path: str | None
    ) -> tuple[str | None, bytes | None]:
        """Fetch an index file found by its SHA-256, returning its URL and body, or None for both if the index no
        longer has the collection. Such a file never changes, so one that doesn't match its SHA-256 is an error, not a
        stale copy. One that's gone means this listing is older than the host keeps replaced files for, so the
        listing is fetched again past any cache, once a run, and the file it names now tried."""
        data = self._fetcher.get(url, cache_path, what=name)
        if data is None:
            trace(f"{url} ({name}): not found, fetching {listing_url} again past any cache")
            listed = self._relist(config.root, listing_url).get(name)
            if listed is None:
                return None, None
            url = index_file_url(config, name, listed.sha256)
            data = self._fetcher.get(url, cache_path, what=name)
            if data is None:
                raise IndexDataError(f"{url} not found, though {listing_url} lists {name}")
        else:
            listed = (self.collections() or {}).get(name)
        # Offline, the cached copy is used as it is, as for an index whose files are found by name.
        if listed is not None and not self._fetcher.offline and _sha256(data) != listed.sha256:
            raise IndexDataError(f"{url} doesn't match its sha256 in {listing_url}")
        return url, data

    def _relist(self, root: str, url: str) -> dict[str, Listed]:
        """``collections.json``, at ``url``, fetched again past any cache if it hasn't been this run."""
        with self._lock:
            if not self._relisted or self._collections is None:
                data = self._fetcher.refresh(url, self._cache_path(root, url))
                if data is None:
                    raise IndexDataError(f"{url} not found")
                self._collections = parse_collections(data, url)
                self._relisted = True
            return self._collections

    def _current(self, listed: Listed | None, cache_path: str | None) -> bytes | None:
        """The cached copy of an index file if its SHA-256 is the one ``collections.json`` lists, else None."""
        if listed is None or listed.sha256 is None:
            return None
        data = self._fetcher.cached(cache_path)
        return data if data is not None and _sha256(data) == listed.sha256 else None

    def _prefetch_file(self, which: _Prefetch) -> None:
        """Start fetching ``which`` from where the cached ``config.json`` says it is, so it arrives with ``config.json``
        rather than after it; with no cached config, from where the spec's layout puts it, as ``index.alai.sh`` does. If
        the config says otherwise, the guess is just an unused request."""
        root = self.index.url
        url = urllib.parse.urljoin(root, "config.json")
        data = self._fetcher.cached(self._cache_path(root, url))
        if data is None:
            # A file index costs no round trip to save.
            guess = None if root.startswith("file:") else urllib.parse.urljoin(root, _USUAL_PATHS[which])
        else:
            try:
                config, moved_to = parse_config(data, root, url)
            except IndexDataError:
                return
            if moved_to is not None:
                return
            guess = config.collections_url if which == "collections" else config.plugins_url
        if guess is not None:
            _ = self._fetcher.fetch(guess, self._cache_path(root, guess))

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

    def _file_cache_path(self, root: str, name: str) -> str | None:
        """Where collection ``name``'s index file is cached, whatever its URL: ``index/<namespace>/<name>.jsonl`` under
        the index's cache directory."""
        namespace, short = collection_name(name).split(".")
        return self._cache_path(root, f"{root}index/{namespace}/{short}.jsonl")

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
    index = paths.get("index")  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(index, str):
        raise IndexDataError(f"{url}: 'formats.{number}' must have an 'index' template")
    if not ("{namespace}" in index and "{name}" in index) and "{sha256-checksum}" not in index:
        raise IndexDataError(
            f"{url}: 'formats.{number}.index' must be a URL template naming each file, with {{namespace}} and {{name}},"
            + " or {sha256-checksum}"
        )
    # No listing is an index that only answers for each collection: a host that serves index files alone, or one that
    # shows each user only what they can see.
    collections_path = paths.get("collections")  # pyright: ignore[reportUnknownVariableType]
    if collections_path is not None and not isinstance(collections_path, str):
        raise IndexDataError(f"{url}: 'formats.{number}.collections' must be a path")
    if not collections_path and any(p in index for p in _HASHED):
        raise IndexDataError(
            f"{url}: 'formats.{number}.index' finds files by SHA-256, which only a 'collections' listing gives"
        )
    plugins_path = paths.get("plugins")  # pyright: ignore[reportUnknownVariableType]
    if plugins_path is not None and not isinstance(plugins_path, str):
        raise IndexDataError(f"{url}: 'formats.{number}.plugins' must be a path")
    # The template's values are names and hex, which can't change where it leads, so any stand in for them.
    sample = _fill(index, "ns.name", "0" * 64)
    for key, path in (("index", sample), ("collections", collections_path), ("plugins", plugins_path)):
        if path is not None:
            _check_scheme(root, urllib.parse.urljoin(root, path), f"{url}: 'formats.{number}.{key}'")

    dl = obj.get("dl")
    if not isinstance(dl, str) or not any(p in dl for p in _DL_PLACEHOLDERS):
        raise IndexDataError(f"{url}: 'dl' must be a URL template with a placeholder such as {{sha256-checksum}}")
    moved_to = obj.get("moved-to")
    if moved_to is not None and not isinstance(moved_to, str):
        raise IndexDataError(f"{url}: 'moved-to' must be a URL or null")

    config = IndexConfig(
        root=root,
        format=number,
        index=index,
        collections_url=urllib.parse.urljoin(root, collections_path) if collections_path else None,
        dl=dl,
        plugins_url=urllib.parse.urljoin(root, plugins_path) if plugins_path else None,
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
                sha256 if isinstance(sha256, str) and SHA256_RE.fullmatch(sha256) else None,
            )
    return result


def parse_index_file(data: bytes, name: str, url: str) -> IndexFile:
    """Read collection ``name``'s index file: one JSON object per line, blank lines ignored. The extension reads each
    line and checks its fields' types (``_native.parse_index_lines``); the version, its requirements and
    ``requires_ansible`` are parsed here."""
    versions: list[CollectionVersion] = []
    invalid: list[str] = []
    seen: set[str] = set()
    entries, newer = _native.parse_index_lines(data, name)
    for number, vers, read in entries:
        try:
            # The version first, as it was checked before the fields after it, so its message wins.
            version = None if vers is None else Version(vers)
            if version is None or isinstance(read, str):
                raise ValueError(read)
            line = _version_from(name, version, read)
        except ValueError as e:
            invalid.append(f"{url}:{number}: {e}")
            continue
        if version.text in seen:
            invalid.append(f"{url}:{number}: version {version} is listed more than once")
            continue
        seen.add(version.text)
        versions.append(line)
    return IndexFile(name, tuple(versions), newer=newer, invalid=tuple(invalid))


def index_file_url(config: IndexConfig, name: str, sha256: str | None = None) -> str:
    """Where collection ``name``'s index file is, from the index template: filled in, then resolved against the index
    root. ``sha256`` is its entry's in ``collections.json``, which a template by SHA-256 needs."""
    if config.hashed and sha256 is None:
        raise IndexDataError(
            f"{config.collections_url}: {name} has no sha256, which {config.root}config.json's index template needs"
        )
    return urllib.parse.urljoin(config.root, _fill(config.index, name, sha256 or ""))


def artifact_url(config: IndexConfig, name: str, version: Version, cksum: str) -> str:
    """Where to download a collection version's artifact, from the ``dl`` template: ``name`` is ``ns.name`` and
    ``cksum`` the artifact's SHA-256, as an index line or a lock gives them."""
    namespace, short = name.split(".")
    return (
        config.dl.replace("{sha256-checksum}", cksum)
        .replace("{sha256-prefix}", cksum[:2])
        .replace("{namespace}", namespace)
        .replace("{name}", short)
        .replace("{version}", urllib.parse.quote(version.text, safe=""))
    )


def _version_from(name: str, version: Version, fields: _IndexFields) -> CollectionVersion:
    """A line the extension has read as a version: what follows makes it unusable rather than unreadable, as the line
    is fine and its metadata isn't."""
    cksum, size, yanked, published, deprecated, raw_deps, text = fields
    problems: list[str] = []
    deps: list[tuple[str, VersionReq]] = []
    for dep_name, req in raw_deps:
        dep = _dependency(dep_name, req)
        if isinstance(dep, str):
            problems.append(f"dependency {dep_name}: {dep}")
        else:
            deps.append(dep)

    requires_ansible = None
    if text is not None:
        try:
            requires_ansible = _specifier_set(text)
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
        published=published,
        deprecated=deprecated,
        problem="; ".join(problems) or None,
    )


# Parsed once per distinct text: an index repeats the same few requirements across thousands of lines (sivel.acd's 130
# index files: 14,523 dependency requirements, 711 distinct; 5,273 requires_ansible, 96 distinct), and what's parsed is
# never changed, so it's shared. A text that fails isn't kept, so it raises again each time.
_REQUIREMENTS: dict[str, VersionReq] = {}
_SPECIFIER_SETS: dict[str, SpecifierSet] = {}
_NAMES: set[str] = set()


def _dependency(name: str, req: str) -> tuple[str, VersionReq] | str:
    """A dependency as ``(name, requirement)``, or why it can't be read."""
    try:
        return _collection_name(name), _requirement(req)
    except ValueError as e:
        return str(e)


def _requirement(text: str) -> VersionReq:
    req = _REQUIREMENTS.get(text)
    if req is None:
        req = _REQUIREMENTS[text] = VersionReq.parse_index(text)
    return req


def _specifier_set(text: str) -> SpecifierSet:
    spec = _SPECIFIER_SETS.get(text)
    if spec is None:
        spec = _SPECIFIER_SETS[text] = SpecifierSet(text)
    return spec


def _collection_name(text: str) -> str:
    if text not in _NAMES:
        _NAMES.add(collection_name(text))
    return text


def _fill(template: str, name: str, sha256: str) -> str:
    namespace, short = collection_name(name).split(".")
    return (
        template.replace("{namespace}", namespace)
        .replace("{name}", short)
        .replace("{sha256-checksum}", sha256)
        .replace("{sha256-prefix}", sha256[:2])
    )


def _check_scheme(root: str, target: str, what: str) -> None:
    """Refuse a path in ``config.json`` that leads from an ``https`` index to anything else, or from one that isn't on
    disk to a local file. An index on disk can point anywhere."""
    scheme = urllib.parse.urlsplit(target).scheme
    allowed = {"file", "http", "https"} if root.startswith("file:") else {"http", "https"}
    if root.startswith("https:"):
        allowed = {"https"}
    if scheme not in allowed:
        raise IndexDataError(f"{what}: refusing {target}, from an index at {root}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_object(data: bytes, url: str) -> dict[str, object]:
    try:
        return json_object(data)
    except ValueError as e:
        raise IndexDataError(f"{url}: {e}") from None
