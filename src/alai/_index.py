# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Reading an alai index: ``config.json``, ``collections.json`` and the per-collection index files.

The format, and reading each file, is alai-core's (``alai_core.index``). Where the files are, and the URLs and caching,
is this module's. The design is in ``design/index-client.md``.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import typing as _t
import urllib.parse

from alai_core import index as _format
from alai_core.index import Collections, CollectionVersion, NewerFormatError
from alai_core.semver import Version

from ._common import Error, Record
from ._config import Index
from ._fetch import Fetcher
from ._manifest import collection_name
from ._output import debug, trace, warn

_MAX_MOVES = 5
_Prefetch = _t.Literal["collections", "plugins"]
_NO_COLLECTIONS = Collections({})
_USUAL_PATHS: dict[_Prefetch, str] = {"collections": "collections.json", "plugins": "plugins.jsonl"}
"""Where the spec's layout, and ``index.alai.sh``, keep the listing and the plugin index, relative to the root."""


class IndexDataError(Error):
    """An index file can't be used: it isn't valid, or it needs a newer alai."""


class IndexConfig(Record):
    """An index's ``config.json``, with its paths made absolute."""

    __slots__ = ("collections_url", "file", "plugins_url", "root")

    def __init__(
        self, *, root: str, file: _format.Config, collections_url: str | None, plugins_url: str | None = None
    ) -> None:
        self.root = root
        """The index root URL, after following any ``moved-to``."""
        self.file = file
        """The file as read, its paths relative to ``root``."""
        self.collections_url = collections_url
        """The listing, ``collections.json``, or None if the index has none, such as a host that serves only index
        files, or one that shows each user only what they can see: whether it has a collection is then only known from
        its index file."""
        self.plugins_url = plugins_url
        """The plugin index, ``plugins.jsonl``, or None if the index has none."""

    @property
    def dl(self) -> str:
        return self.file.dl

    @property
    def hashed(self) -> bool:
        return self.file.hashed


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
        self._collections: Collections | None = None
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

    def collections(self) -> Collections | None:
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
        listing = self.collections() or _NO_COLLECTIONS
        for name in names:
            listed = listing.get(name)
            if config.hashed and (listed is None or listed.sha256 is None):
                continue  # Not in the index, or an error ``versions`` reports.
            cache_path = self._file_cache_path(config.root, name)
            if self._current(listed, cache_path) is None:
                self._fetcher.fetch(index_file_url(config, name, listed and listed.sha256), cache_path, what=name)

    def versions(self, name: str) -> IndexFile | None:
        """The index file for collection ``name``, or None if the index doesn't have it."""
        with self._lock:
            if name in self._files:
                return self._files[name]
        config = self.config()
        listed = (self.collections() or _NO_COLLECTIONS).get(name)
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
                url, data = self._fetch_hashed(config, config.collections_url, listed, name, url, cache_path)
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
        self,
        config: IndexConfig,
        listing_url: str,
        listed: _format.CollectionEntry | None,
        name: str,
        url: str,
        cache_path: str | None,
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
        # Offline, the cached copy is used as it is, as for an index whose files are found by name.
        if listed is not None and not self._fetcher.offline and _sha256(data) != listed.sha256:
            raise IndexDataError(f"{url} doesn't match its sha256 in {listing_url}")
        return url, data

    def _relist(self, root: str, url: str) -> Collections:
        """``collections.json``, at ``url``, fetched again past any cache if it hasn't been this run."""
        with self._lock:
            if not self._relisted or self._collections is None:
                data = self._fetcher.refresh(url, self._cache_path(root, url))
                if data is None:
                    raise IndexDataError(f"{url} not found")
                self._collections = parse_collections(data, url)
                self._relisted = True
            return self._collections

    def _current(self, listed: _format.CollectionEntry | None, cache_path: str | None) -> bytes | None:
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
            self._fetcher.fetch(guess, self._cache_path(root, guess))

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
            _check_scheme(root, target, f"{url}: 'moved-to'")
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
        segments = url[len(root) :].split("/") if url.startswith(root) else []
        # urljoin drops '..' from a path, but not from a query or fragment, which config.json can give.
        if not segments or any(s in ("", ".", "..") or "?" in s or "#" in s for s in segments):
            segments = ["_other", hashlib.sha256(url.encode()).hexdigest()]
        return os.path.join(self._cache_dir, "index", url_dir(root), *segments)


def url_dir(root: str) -> str:
    """The cache directory name for the index at ``root``: its host, for a reader, and a hash of the whole URL."""
    host = re.sub(r"[^A-Za-z0-9.-]", "_", urllib.parse.urlsplit(root).hostname or "index")
    return f"{host}-{hashlib.sha256(root.encode()).hexdigest()[:16]}"


def parse_config(data: bytes, root: str, url: str) -> tuple[IndexConfig, str | None]:
    """Read ``config.json``: the highest format this alai reads, and the ``moved-to`` URL if the index has moved."""
    try:
        parsed = _format.Config.parse(data)
    except NewerFormatError as e:
        offered = ", ".join(str(n) for n in e.found) or "none"
        raise IndexDataError(
            f"{root} needs a newer alai: it offers index format {offered}, this alai reads {e.supported}"
        ) from e
    except ValueError as e:
        raise IndexDataError(f"{url}: {e}") from e
    number = parsed.format
    # The template's values are names and hex, which can't change where it leads, so any stand in for them.
    sample = parsed.index_path(name="ns.name", sha256="0" * 64)
    for key, path in (("index", sample), ("collections", parsed.collections), ("plugins", parsed.plugins)):
        if path is not None:
            _check_scheme(root, urllib.parse.urljoin(root, path), f"{url}: 'formats.{number}.{key}'")
    dl = parsed.artifact_path(name="ns.name", version=Version("1.0.0"), sha256="0" * 64)
    _check_scheme(root, urllib.parse.urljoin(root, dl), f"{url}: 'dl'")
    config = IndexConfig(
        root=root,
        file=parsed,
        collections_url=urllib.parse.urljoin(root, parsed.collections) if parsed.collections else None,
        plugins_url=urllib.parse.urljoin(root, parsed.plugins) if parsed.plugins else None,
    )
    return config, parsed.moved_to


def parse_collections(data: bytes, url: str) -> Collections:
    """Read ``collections.json``: each collection's entry, by ``namespace.name``."""
    try:
        return Collections.parse(data)
    except NewerFormatError as e:
        raise IndexDataError(
            f"{url} needs a newer alai: it's version {e.found[0]}, this alai reads {e.supported}"
        ) from e
    except ValueError as e:
        raise IndexDataError(f"{url}: {e}") from e


def parse_index_file(data: bytes, name: str, url: str) -> IndexFile:
    """Read collection ``name``'s index file, with each line that can't be read named by its URL and number."""
    parsed = _format.CollectionIndex.parse(data, name)
    invalid = tuple(f"{url}:{number}: {why}" for number, why in parsed.invalid)
    return IndexFile(name, parsed.lines, newer=parsed.newer, invalid=invalid)


def index_file_url(config: IndexConfig, name: str, sha256: str | None = None) -> str:
    """Where collection ``name``'s index file is, from the index template: filled in, then resolved against the index
    root. ``sha256`` is its entry's in ``collections.json``, which a template by SHA-256 needs."""
    if config.hashed and sha256 is None:
        raise IndexDataError(
            f"{config.collections_url}: {name} has no sha256, which {config.root}config.json's index template needs"
        )
    return urllib.parse.urljoin(config.root, config.file.index_path(name=name, sha256=sha256))


def artifact_url(config: IndexConfig, name: str, version: Version, cksum: str) -> str:
    """Where to download a collection version's artifact, from the ``dl`` template resolved against the index root:
    ``name`` is ``ns.name`` and ``cksum`` the artifact's SHA-256, as an index line or a lock gives them."""
    return urllib.parse.urljoin(config.root, config.file.artifact_path(name=name, version=version, sha256=cksum))


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
