# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Reading and searching an index's plugin index, ``plugins.jsonl``, for ``alai find``: matching names, following
routing as ansible-core does, and ranking what's found.

The format is ``plugins.md`` in galaxy-sync's ``docs/index-spec/``. The file is ~100k lines, so a search scans the
raw bytes for the query and parses only the lines that contain it, both in the extension (``_native.plugin_lines``).
What needs every line, which collections look like copies of others, is worked out once per change to the file and
cached. The design is in ``design/find.md``.
"""

from __future__ import annotations

import collections.abc as _c
import fnmatch
import glob
import hashlib
import json
import os
import re
import tempfile
import typing as _t

from . import _ansible_package, _native
from ._common import Record
from ._output import debug, trace
from ._semver import Version

if _t.TYPE_CHECKING:
    from ._native import _PluginLine

BUILTIN = "ansible.builtin"
"""ansible-core's own plugins, which are always installed where Ansible is."""
LEGACY = "ansible.legacy"
"""Not a collection: ansible-core's lookup from before collections, ``library/`` and the ``*_plugins`` directories
(configured, beside the playbook, or in a role), then its own plugins, then ``ansible.builtin``'s routing. alai can't
see the directories, so it's ``ansible.builtin`` here."""

_SUMMARY_VERSION = 1
_GLOB_CHARS = "*?["
_COPY_SHARE = 0.5
"""The share of a collection's plugins named as in one ``ansible`` package collection that makes it a probable copy."""
_COPY_MIN = 3
"""Collections with fewer plugins than this aren't judged: two or three names in common is chance."""


class Plugin(Record):
    """One line of the plugin index."""

    __slots__ = ("collection", "index", "line", "name", "routing", "short_description", "since", "type", "until")

    def __init__(
        self,
        type: str,  # noqa: A002
        name: str,
        collection: str,
        *,
        since: str | None = None,
        until: str | None = None,
        short_description: str | None = None,
        routing: dict[str, object] | None = None,
        index: str = "",
        line: bytes = b"",
    ) -> None:
        self.type = type
        self.name = name
        self.collection = collection
        self.since = since
        self.until = until
        self.short_description = short_description
        self.routing = routing or {}
        """The plugin's ``plugin_routing`` entry from the collection's ``meta/runtime.yml``, as the index copied it."""
        self.index = index
        """The name of the index it came from."""
        self.line = line
        """The line it was read from, or empty."""

    @property
    def raw(self) -> dict[str, object]:
        """The line as JSON, for JSON output, which passes on what the index says, unknown fields included. Parsed
        when asked for, as only what's shown needs it."""
        return _t.cast("dict[str, object]", json.loads(self.line)) if self.line else {}

    @property
    def fqcn(self) -> str:
        return f"{self.collection}.{self.name}"

    @property
    def key(self) -> tuple[str, str]:
        return (self.type, self.fqcn)

    @property
    def redirect(self) -> str | None:
        value = self.routing.get("redirect")
        return value if isinstance(value, str) else None

    @property
    def tombstone(self) -> dict[str, object] | None:
        return _table(self.routing.get("tombstone"))

    @property
    def deprecation(self) -> dict[str, object] | None:
        return _table(self.routing.get("deprecation"))


def parse_line(line: bytes, index: str = "") -> Plugin | None:
    """One line as a plugin, or None if it can't be read or needs a newer alai: a plugin index is a search aid, so a
    bad line is skipped rather than an error."""
    return next(iter(_plugins(_native.plugin_lines(line, []), index)), None)


def _plugins(lines: list[_PluginLine], index: str) -> list[Plugin]:
    return [
        Plugin(
            type_,
            name,
            collection,
            since=since,
            until=until,
            short_description=description,
            routing=routing,
            index=index,
            line=line,
        )
        for type_, name, collection, since, until, description, routing, line in lines
    ]


class Query:
    """What ``alai find`` was asked for: part of a name, a glob, or a fully qualified name."""

    def __init__(self, text: str, *, exact: bool = False, types: _c.Collection[str] = ()) -> None:
        self.typed = text
        """The query as given; ``text`` is what's searched for, with ``ansible.legacy.`` as ``ansible.builtin.``."""
        self.legacy = text.startswith(f"{LEGACY}.")
        self.text = as_builtin(text)
        self.types = frozenset(types)
        self.glob = any(c in text for c in _GLOB_CHARS)
        self.qualified = text.count(".") >= 2
        """A FQCN, or a glob over FQCNs, rather than a plugin's short name."""
        self.exact = exact or (self.qualified and not self.glob)

    @property
    def fqcn(self) -> bool:
        """Whether this names one plugin by its FQCN, for which ``alai find`` shows the details."""
        return self.qualified and not self.glob

    def tokens(self) -> list[bytes]:
        """Bytes every matching line contains, longest first, to skip the rest without parsing them: the query, or the
        literal parts of a glob. A FQCN's parts are split at the dots, as a line holds the collection and the name as
        separate fields. Parts JSON may have escaped are left out, as the line may not hold them as typed."""
        parts = re.split(r"[.*?]|\[[^\]]*\]?", self.text)
        safe = [p for p in parts if p and p.isascii() and '"' not in p and "\\" not in p and "/" not in p]
        return sorted({p.encode() for p in safe}, key=len, reverse=True)

    def matches(self, plugin: Plugin) -> bool:
        if self.types and plugin.type not in self.types:
            return False
        if self.qualified:
            return fnmatch.fnmatchcase(plugin.fqcn, self.text) if self.glob else plugin.fqcn == self.text
        if self.glob:
            return fnmatch.fnmatchcase(plugin.name, self.text)
        return plugin.name == self.text if self.exact else self.text in plugin.name

    def is_exact(self, plugin: Plugin) -> bool:
        """Whether ``plugin`` is what was named, rather than a partial match: what's ranked first, and what keeps a
        removed plugin in the results, since looking one up is how to find where it went."""
        return self.fqcn or plugin.name == self.text


class PluginIndex:
    """One index's ``plugins.jsonl``, searched without parsing lines that can't match."""

    def __init__(self, index: str, data: bytes) -> None:
        self.index = index
        self.data = data

    def with_tokens(self, tokens: _c.Sequence[bytes] = ()) -> list[Plugin]:
        """Every plugin whose line contains all of ``tokens``; every plugin if there are none."""
        return _plugins(_native.plugin_lines(self.data, list(tokens)), self.index)

    def search(self, query: Query) -> list[Plugin]:
        return [plugin for plugin in self.with_tokens(query.tokens()) if query.matches(plugin)]

    def get(self, type_: str, fqcn: str) -> Plugin | None:
        """The plugin of type ``type_`` named ``fqcn``, or None."""
        name = fqcn.rsplit(".", 1)[-1]
        for plugin in self.search(Query(fqcn, exact=True, types=(type_,))):
            if plugin.name == name:
                return plugin
        return None

    def named(self, names: _c.Collection[str]) -> list[Plugin]:
        """Every plugin whose line has a ``name`` field that may be one of ``names``, found in one pass over the
        file."""
        return _plugins(_native.plugin_lines_named(self.data, set(names)), self.index) if names else []

    def names(self) -> set[str]:
        """Every plugin's short name, for suggestions, read without parsing each line."""
        return _native.plugin_names(self.data)

    def plugins(self) -> list[Plugin]:
        return self.with_tokens()


class Copy(Record):
    """A collection most of whose plugins are named as in one ``ansible`` package collection's: a fork, a renamed
    copy, or a bundle of another's plugins."""

    __slots__ = ("of", "shared", "total")

    def __init__(self, of: str, shared: int, total: int) -> None:
        self.of = of
        self.shared = shared
        self.total = total


class Catalog:
    """The plugin indexes of every configured index that has one, in resolution order: where a plugin is in more than
    one, the first index's line is used, as the resolver uses the first index that has a collection."""

    def __init__(self, indexes: _c.Sequence[PluginIndex], copies: dict[str, Copy] | None = None) -> None:
        self.indexes = indexes
        self.copies = copies if copies is not None else {}
        """Probable copies, by collection name."""
        self._got: dict[tuple[str, str], Plugin | None] = {}

    def search(self, query: Query) -> list[Plugin]:
        seen: set[tuple[str, str]] = set()
        found: list[Plugin] = []
        for index in self.indexes:
            for plugin in index.search(query):
                if plugin.key not in seen:
                    seen.add(plugin.key)
                    found.append(plugin)
        return found

    def preload(self, keys: _c.Iterable[tuple[str, str]]) -> None:
        """Look up each ``(type, fqcn)`` for ``get`` in one pass over each index, rather than one pass each."""
        wanted = {k for k in keys if k not in self._got}
        if not wanted:
            return
        names = {fqcn.rsplit(".", 1)[-1] for _, fqcn in wanted}
        for index in self.indexes:
            for plugin in index.named(names):
                if plugin.key in wanted and self._got.get(plugin.key) is None:
                    self._got[plugin.key] = plugin
        for key in wanted:
            _ = self._got.setdefault(key, None)

    def preload_routes(self, plugins: _c.Iterable[Plugin]) -> None:
        """Look up every plugin the redirects from ``plugins`` lead to, a hop at a time, so ``follow`` needs no more
        passes over the file."""
        pending = list(plugins)
        seen: set[tuple[str, str]] = set()
        while pending:
            keys = {(p.type, as_builtin(t)) for p in pending if (t := p.redirect) and t.count(".") == 2} - seen
            seen |= keys
            self.preload(keys)
            pending = [p for k in keys if (p := self._got.get(k)) is not None]

    def get(self, type_: str, fqcn: str) -> Plugin | None:
        key = (type_, fqcn)
        if key not in self._got:
            self._got[key] = next((p for i in self.indexes if (p := i.get(type_, fqcn)) is not None), None)
        return self._got[key]

    def suggest(self, query: Query) -> list[str]:
        """Names close to what was asked for, when nothing matched it."""
        import difflib

        names: set[str] = set()
        for index in self.indexes:
            names |= index.names()
        return difflib.get_close_matches(query.text.rsplit(".", 1)[-1], sorted(names), n=3, cutoff=0.75)


def find_copies(plugins: _c.Iterable[Plugin]) -> dict[str, Copy]:
    """The collections that look like copies of one in the ``ansible`` package: at least half their plugins share a
    type and name with that collection's. Collections in a namespace the package has a collection from aren't judged,
    as Galaxy namespaces have owners and a vendor's newer collection often reuses its older one's names."""
    names: dict[str, set[tuple[str, str]]] = {}
    for plugin in plugins:
        names.setdefault(plugin.collection, set()).add((plugin.type, plugin.name))
    package = {c: names[c] for c in _ansible_package.COLLECTIONS if c in names}
    copies: dict[str, Copy] = {}
    for collection, own in names.items():
        if collection.split(".")[0] in PACKAGE_NAMESPACES or collection == BUILTIN or len(own) < _COPY_MIN:
            continue
        shared, of = max(((len(own & theirs), name) for name, theirs in package.items()), default=(0, ""))
        if shared >= _COPY_SHARE * len(own):
            copies[collection] = Copy(of, shared, len(own))
    return copies


PACKAGE_NAMESPACES = frozenset(c.split(".")[0] for c in _ansible_package.COLLECTIONS)


def cached_copies(cache_dir: str, url: str, data: bytes) -> dict[str, Copy]:
    """``find_copies`` for one index's plugin index, from the cache when it was worked out for these same bytes and
    the same ``ansible`` package list, else worked out and cached."""
    digest = hashlib.sha256(data).hexdigest()
    path = os.path.join(cache_dir, "plugins-v1", f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.json")
    try:
        with open(path, "rb") as f:
            cached = json.load(f)
        if (
            cached.get("v") == _SUMMARY_VERSION
            and cached.get("sha256") == digest
            and cached.get("ansible") == _ansible_package.RELEASES
        ):
            trace(f"{path}: the cached summary matches {url}")
            return {name: Copy(*entry) for name, entry in cached["copies"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass

    debug(f"working out the probable copies in {url}")
    copies = find_copies(PluginIndex("", data).plugins())
    summary = {
        "v": _SUMMARY_VERSION,
        "sha256": digest,
        "ansible": _ansible_package.RELEASES,
        "copies": {name: [c.of, c.shared, c.total] for name, c in copies.items()},
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(summary, f)
        os.replace(temp, path)
    except OSError as e:
        debug(f"can't cache the summary at {path}: {e.strerror or e}")
    return copies


def cached_collections(cache_dir: str) -> set[str]:
    """The collections with a version in the artifact cache: ones the user has installed before."""
    from ._artifacts import LAYOUT

    manifests = glob.glob(os.path.join(glob.escape(cache_dir), LAYOUT, "*", "MANIFEST.json"))
    return {name for path in manifests if (name := _manifest_name(path)) is not None}


def _manifest_name(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            info = json.load(f)["collection_info"]
        return f"{info['namespace']}.{info['name']}"
    except (OSError, ValueError, KeyError, TypeError):
        return None


# Where a plugin stands for the user, worst last, which is also the order they're ranked in.
OK = "ok"
REDIRECT = "redirect"
MISSING = "missing"
"""Installed, but its redirects lead through a collection that isn't, so ansible-core can't load it yet."""
DEPRECATED = "deprecated"
NEWER = "newer"
"""In a newer version of the collection than the one installed."""
REMOVED = "removed"
BROKEN = "broken"
"""Its routing leads nowhere ansible-core can load: a loop, a tombstone, or a redirect that isn't a FQCN."""
_ORDER = (OK, REDIRECT, DEPRECATED, MISSING, NEWER, REMOVED, BROKEN)


class Route(Record):
    """Where a plugin's redirects lead, as ansible-core 2.20's ``PluginLoader`` follows them: each hop's routing is
    read before its file, so a redirect applies even where the file still exists; a tombstone anywhere fails the load;
    a redirect must be a FQCN; and reaching a name twice is a loop. The type never changes."""

    __slots__ = ("hops", "problem", "unknown")

    def __init__(self, hops: list[tuple[str, Plugin | None]], problem: str | None, unknown: str | None) -> None:
        self.hops = hops
        """Each name redirected to, in order, with its line, or None where the index has none."""
        self.problem = problem
        """Why ansible-core would fail to load the plugin, or None."""
        self.unknown = unknown
        """Why the route can't be followed to its end from the index, or None."""

    @property
    def final(self) -> str:
        return self.hops[-1][0]

    def collections(self) -> list[str]:
        """The collections the route goes through, in order, each once: each must be installed for ansible-core to
        follow it, as it reads each hop's routing from that hop's collection. ``ansible.legacy`` is ``ansible.builtin``."""
        names: list[str] = []
        for name, _ in self.hops:
            collection = as_builtin(name).rsplit(".", 1)[0]
            if collection not in names:
                names.append(collection)
        return names


def as_builtin(name: str) -> str:
    """``ansible.legacy.NAME`` as ``ansible.builtin.NAME``, which is what it is unless a local plugin shadows it."""
    return f"{BUILTIN}.{name[len(LEGACY) + 1 :]}" if name.startswith(f"{LEGACY}.") else name


def has_file(plugin: Plugin) -> bool:
    """Whether an ``ansible.builtin`` line is a plugin ansible-core ships, rather than only its routing: shipped ones
    are documented or have no redirect or tombstone. ``ansible.legacy`` finds a file before it reads any routing."""
    return plugin.short_description is not None or (plugin.redirect is None and plugin.tombstone is None)


def follow(plugin: Plugin, catalog: Catalog, installed: _c.Mapping[str, Version | None] | None = None) -> Route | None:
    """The route from ``plugin``'s redirect, or None if it has none.

    A step to a plugin its collection removed is where the route breaks, unless ``installed`` has that collection at a
    version from before the removal: the plugin's file is still there, so the route ends at it.
    """
    if plugin.redirect is None:
        return None
    hops: list[tuple[str, Plugin | None]] = []
    seen = [plugin.fqcn]
    current = plugin
    while (target := current.redirect) is not None:
        if target.count(".") != 2:
            return Route(hops, f"redirect to '{target}', which isn't a FQCN", None)
        found = catalog.get(plugin.type, as_builtin(target))
        hops.append((target, found))
        if target in seen:
            return Route(hops, "redirects to itself" if target == seen[-1] else "a redirect loop", None)
        seen.append(target)
        if found is None and target.startswith(f"{LEGACY}."):
            return Route(
                hops, None, f"ansible.builtin has no {target.rsplit('.', 1)[-1]}, so only a local plugin can be it"
            )
        if found is None:
            return Route(hops, None, f"{target} isn't in the plugin index")
        if target.startswith(f"{LEGACY}.") and has_file(found):
            break
        if _still_has(found, installed):
            break
        if found.tombstone is not None:
            return Route(hops, f"{target} was removed", None)
        if found.until is not None and found.redirect is None:
            return Route(hops, f"{target} was removed in {found.until}", None)
        current = found
    return Route(hops, None, None)


class Found(Record):
    """A plugin that matched, and where it stands for this project."""

    __slots__ = ("copy", "exact", "installed", "missing", "outdated", "plugin", "route", "state", "version")

    def __init__(
        self,
        plugin: Plugin,
        *,
        installed: bool,
        version: Version | None,
        route: Route | None,
        state: str,
        copy: Copy | None,
        exact: bool,
        missing: list[str] | None = None,
        outdated: list[tuple[str, str]] | None = None,
    ) -> None:
        self.plugin = plugin
        self.installed = installed
        """Whether its collection is installed (``ansible.builtin`` always is)."""
        self.version = version
        """The installed collection's version, if it's installed and its version can be read."""
        self.route = route
        self.state = state
        self.copy = copy
        self.exact = exact
        self.missing = missing or []
        """The collections its route goes through that aren't installed."""
        self.outdated = outdated or []
        """``(collection, since)`` for each step of its route into an installed collection older than the version
        that added the plugin there."""

    @property
    def usable(self) -> bool | None:
        """Whether ansible-core would load it by this name, or None when alai can't tell: its route goes through a
        plugin the index doesn't have, such as a local one in ``library/``."""
        if not self.installed or self.state not in (OK, REDIRECT, DEPRECATED):
            return False
        return None if self.route is not None and self.route.unknown is not None else True


def assess(plugin: Plugin, catalog: Catalog, installed: _c.Mapping[str, Version | None], query: Query) -> Found:
    """Where ``plugin`` stands, given the installed collections and their versions."""
    collection = plugin.collection
    is_installed = collection == BUILTIN or collection in installed
    version = installed.get(collection)
    route = follow(plugin, catalog, installed)
    missing = [c for c in route.collections() if c != BUILTIN and c not in installed] if route else []
    outdated: list[tuple[str, str]] = []
    for _, hop in route.hops if route else ():
        have = installed.get(hop.collection) if hop is not None else None
        added = _version(hop.since) if hop is not None else None
        if hop is not None and hop.since is not None and have is not None and added is not None and have < added:
            outdated.append((hop.collection, hop.since))
    since = _version(plugin.since)
    until = _version(plugin.until)

    if plugin.tombstone is not None or (route is not None and route.problem is not None):
        state = BROKEN if plugin.tombstone is None else REMOVED
    elif route is not None:
        state = (MISSING if missing else NEWER if outdated else REDIRECT) if is_installed else REDIRECT
    elif version is not None and since is not None and version < since:
        state = NEWER
    elif until is not None and (version is None or version >= until):
        # Not installed: gone from its latest version. Installed: gone from the version there.
        state = REMOVED
    elif plugin.deprecation is not None:
        state = DEPRECATED
    else:
        state = OK
    return Found(
        plugin,
        installed=is_installed,
        version=version,
        route=route,
        state=state,
        copy=catalog.copies.get(collection),
        exact=query.is_exact(plugin),
        missing=missing,
        outdated=outdated,
    )


def rank(found: Found, cached: _c.Container[str]) -> tuple[object, ...]:
    """The sort key for a match: what was named before partial matches, what can be used before redirects to it and
    those before what's gone, then ``ansible.builtin``, collections used before (in the artifact cache), those in the ``ansible`` package, others in
    its namespaces, probable copies last, and by name within each."""
    plugin = found.plugin
    collection = plugin.collection
    namespace = collection.split(".")[0]
    tier = 0 if collection in _ansible_package.COLLECTIONS else 1 if namespace in PACKAGE_NAMESPACES else 2
    return (
        not found.exact,
        found.state in (REMOVED, BROKEN),
        # ansible.builtin redirects every module that moved to a collection in 2.10, so without this its old names
        # would bury a real plugin; the plugin each leads to is listed in its own right.
        found.route is not None,
        collection != BUILTIN,
        collection not in cached,
        tier,
        found.copy is not None,
        _ORDER.index(found.state),
        collection,
        plugin.type,
        plugin.name,
    )


def _still_has(plugin: Plugin, installed: _c.Mapping[str, Version | None] | None) -> bool:
    """Whether the installed version of ``plugin``'s collection is from before it was removed, so it has the file: the
    index's ``until``, or a tombstone's ``removal_version``, is later than it."""
    if installed is None or plugin.collection not in installed:
        return False
    have = installed[plugin.collection]
    tombstone = plugin.tombstone or {}
    removed = _version(plugin.until) or _version(_text(tombstone.get("removal_version")))
    return have is not None and removed is not None and have < removed


def _version(text: str | None) -> Version | None:
    if text is None:
        return None
    if text not in _VERSIONS:
        try:
            _VERSIONS[text] = Version(text)
        except ValueError:
            _VERSIONS[text] = None
    return _VERSIONS[text]


# Parsed once per distinct text: a broad search assesses tens of thousands of plugins (73,263 for 'a', 2026-10-23), whose
# 'since' and 'until' are a few hundred versions, and a Version is never changed, so it's shared.
_VERSIONS: dict[str, Version | None] = {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _table(value: object) -> dict[str, object] | None:
    return _t.cast("dict[str, object]", value) if isinstance(value, dict) else None
