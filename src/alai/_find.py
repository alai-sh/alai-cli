# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai find``: plugins by name from the indexes' plugin indexes, with the project's installed collections first.

Results go to stdout, a list of matches or, for a FQCN, the plugin's details; everything else to stderr. The design is
in ``design/find.md``.
"""

from __future__ import annotations

import json
import os
import re
import sys

from alai_core.markup import parse as parse_markup
from alai_core.semver import Version

from . import _output
from ._common import EXIT_NOT_FOUND, Error, home_path
from ._config import Settings
from ._install import Installed, installed
from ._output import (
    BLUE,
    BOLD,
    CYAN,
    DIM,
    GREEN,
    ITALIC,
    MAGENTA,
    RED,
    UNDERLINE,
    YELLOW,
    Renderer,
    debug,
    status,
    warn,
)
from ._plugins import (
    BROKEN,
    BUILTIN,
    DEPRECATED,
    LEGACY,
    MISSING,
    NEWER,
    OK,
    REDIRECT,
    REMOVED,
    Catalog,
    Copy,
    Found,
    Plugin,
    PluginIndex,
    Query,
    as_builtin,
    assess,
    cached_collections,
    cached_copies,
    has_file,
    rank,
    semver_or_none,
)

DEFAULT_LIMIT = 20
"""How many matches are listed on a terminal, unless ``--limit`` says otherwise: with the headings, ``… N more`` lines
and legend, about 26 lines, which fits a terminal window without scrolling."""

_NAME_COLUMN = 60
"""The widest the name column is padded to: 90% of full names fit (2026-10-21), and a longer one, up to 189
characters, pushes only its own description along rather than every row's."""
_MARKERS = {
    OK: ("·", DIM),
    REDIRECT: ("↪", CYAN),
    MISSING: ("↪", YELLOW),
    DEPRECATED: ("⚠", YELLOW),
    NEWER: ("↑", YELLOW),
    REMOVED: ("✗", RED),
    BROKEN: ("✗", RED),
}
_LEGEND = (
    ("✓", GREEN, "installed"),
    ("·", DIM, "not installed"),
    ("↑", YELLOW, "in a newer version"),
    ("↪", CYAN, "redirects"),
    ("↪", YELLOW, "redirects to a collection that isn't installed"),
    ("⚠", YELLOW, "deprecated"),
    ("✗", RED, "removed or broken"),
)


def find(
    settings: Settings,
    query: Query,
    *,
    installed_only: bool = False,
    include_removed: bool = False,
    limit: int | None = None,
    output_format: str = "text",
) -> int:
    catalog = load_catalog(settings)
    installed = installed_collections(settings)
    if installed is None and output_format == "text" and not query.fqcn:
        status(_output.styled(f"No project found, so only {BUILTIN} counts as installed", DIM))
    if query.legacy and output_format == "text":
        status(
            _output.styled(
                f"{LEGACY} is {BUILTIN}, unless a plugin in library/ or a *_plugins directory, beside the playbook or "
                + "in a role, has the same name; alai doesn't look there",
                DIM,
            )
        )

    matches = catalog.search(query)
    catalog.preload_routes(matches)
    versions = {name: c.version for name, c in (installed or {}).items()}
    found = [assess(p, catalog, versions, query) for p in matches]
    # A removed plugin is only listed when it was named: looking one up is how to find where it went.
    found = [f for f in found if include_removed or f.exact or f.state not in (REMOVED, BROKEN)]
    if not found:
        suggestions = catalog.suggest(query)
        also = f"; did you mean {_or(suggestions)}?" if suggestions else ""
        warn(
            f"no plugin matches '{query.typed}'{'' if not query.types else ' of type ' + _or(sorted(query.types))}{also}"
        )
        return EXIT_NOT_FOUND

    mine = [f for f in found if f.installed]
    others = [] if installed_only else [f for f in found if not f.installed]
    cached = cached_collections(settings.cache_dir) if len(others) > 1 else set[str]()
    mine.sort(key=lambda f: rank(f, cached))
    others.sort(key=lambda f: rank(f, cached))
    # Only a person at a terminal is served by a short list: a pipe or file, a pager or grep or a script, gets them all.
    if limit is None and output_format == "text" and not query.fqcn and sys.stdout.isatty():
        limit = DEFAULT_LIMIT
    # The limit is on the whole list, so the installed matches, listed first, are the ones kept.
    totals = (len(mine), len(others))
    if limit:
        others = others[: max(0, limit - len(mine))]
        mine = mine[:limit]
    hidden = sum(totals) - len(mine) - len(others)

    if output_format == "json":
        _print_json(mine + others, hidden, installed or {})
    elif query.fqcn:
        for item in mine + others:
            alternatives = same_name(item, catalog, versions, settings.cache_dir)
            _Renderer(settings, query, installed).detail(item, alternatives)
    else:
        _Renderer(settings, query, installed).listing(mine, others, totals, project=installed is not None)
    return 0


def same_name(
    item: Found, catalog: Catalog, versions: dict[str, Version | None], cache_dir: str, *, most: int = 3
) -> list[Found]:
    """For a plugin that's removed, or whose route breaks at one, with no ``warning_text`` saying what to use instead:
    the best-ranked other usable plugins of its type and name, as a guess at what replaced it. Most plugins that left
    ansible-core in 2.10 and were later removed from where they went have a successor with the same name."""
    if item.state not in (REMOVED, BROKEN):
        return []
    plugin = item.plugin
    hops = item.route.hops if item.route else []
    broke_at = hops[-1][1] if hops and hops[-1][1] is not None else plugin
    if any(_warning_text(p.tombstone) for p in (plugin, broke_at)):
        return []
    skip = {plugin.fqcn, *(as_builtin(name) for name, _ in hops)}
    query = Query(plugin.name, exact=True, types=(plugin.type,))
    candidates = [p for p in catalog.search(query) if p.fqcn not in skip]
    catalog.preload_routes(candidates)
    found = [assess(p, catalog, versions, query) for p in candidates]
    found = [f for f in found if f.state not in (REMOVED, BROKEN)]
    # Another name for one already listed adds nothing.
    names = {f.plugin.fqcn for f in found}
    found = [f for f in found if f.route is None or as_builtin(f.route.final) not in names]
    cached = cached_collections(cache_dir) if len(found) > 1 else set[str]()
    return sorted(found, key=lambda f: (not f.usable, rank(f, cached)))[:most]


def _warning_text(routing: dict[str, object] | None) -> str | None:
    text = routing.get("warning_text") if routing else None
    return text.strip() or None if isinstance(text, str) else None


def load_catalog(settings: Settings) -> Catalog:
    """The plugin index of every configured index that has one, fetched or revalidated."""
    from ._fetch import open_fetcher
    from ._index import IndexClient
    from ._progress import Live, Task

    indexes: list[PluginIndex] = []
    copies: dict[str, Copy] = {}
    with (
        open_fetcher(settings) as fetcher,
        Live(Task("Fetching plugin indexes...", total=len(settings.indexes))) as task,
    ):
        for index in settings.indexes:
            task.show(index.name)
            client = IndexClient(index, fetcher, settings.cache_dir, prefetch="plugins")
            data = client.plugins()
            url = client.config().plugins_url
            if data is not None and url is not None:
                indexes.append(PluginIndex(index.name, data))
                for name, copy in cached_copies(settings.cache_dir, url, data).items():
                    copies.setdefault(name, copy)
            else:
                debug(f"index '{index.name}' ({index.url}) has no plugin index")
            task.advance()
    if not indexes:
        raise Error("none of the configured indexes has a plugin index to search ('plugins' in its config.json)")
    return Catalog(indexes, copies)


def installed_collections(settings: Settings) -> dict[str, Installed] | None:
    """The project's installed collections, or None if there's no project: with no collections directory, only
    ``ansible.builtin`` is installed."""
    if settings.project.config_file is None and not settings.collections_dir_set:
        return None
    return installed(settings.collections_dir)


def _print_json(found: list[Found], hidden: int, installed: dict[str, Installed]) -> None:
    """The matches as facts, for scripts to draw their own conclusions: each plugin's index line, the matches in ranked
    order then every plugin their redirects reach, each once, and whether each collection mentioned is installed.

    Nothing alai concludes, such as whether a plugin is usable or where its route breaks, is included: that's the text
    output's job, and a script follows ``routing.redirect`` itself. The design is in ``design/find.md``.
    """
    plugins: dict[tuple[str, str], dict[str, object]] = {}
    collections: set[str] = set()

    def add(plugin: Plugin, *, matched: bool) -> None:
        if plugin.key in plugins:
            return
        entry = {k: v for k, v in plugin.raw.items() if k != "v"}
        entry.update({"fqcn": plugin.fqcn, "index": plugin.index, "matched": matched})
        plugins[plugin.key] = entry
        collections.add(plugin.collection)
        if plugin.redirect is not None and plugin.redirect.count(".") == 2:
            collections.add(as_builtin(plugin.redirect).rsplit(".", 1)[0])

    for item in found:
        add(item.plugin, matched=True)
    for item in found:
        for _, step in item.route.hops if item.route else ():
            if step is not None:
                add(step, matched=False)

    def collection(name: str) -> dict[str, object]:
        have = installed.get(name)
        return {
            "installed": name == BUILTIN or have is not None,
            "version": str(have.version) if have is not None and have.version is not None else None,
            "path": os.path.abspath(have.path) if have is not None else None,
        }

    result = {
        "v": 1,
        "plugins": list(plugins.values()),
        "collections": {name: collection(name) for name in sorted(collections)},
        "omitted": hidden,
    }
    json.dump(result, sys.stdout, indent=2)
    print()


class _Renderer(Renderer):
    def __init__(self, settings: Settings, query: Query, installed: dict[str, Installed] | None) -> None:
        super().__init__()
        self.settings = settings
        self.query = query
        self.installed = installed or {}

    def listing(self, mine: list[Found], others: list[Found], totals: tuple[int, int], *, project: bool) -> None:
        """The two sections, each headed by how many it has; ``totals`` counts those ``--limit`` left out too, and a
        section all of whose matches were left out is still headed, to say there are some."""
        shown = mine + others
        type_width = max(len(f.plugin.type) for f in shown)
        name_width = min(max(len(f.plugin.fqcn) for f in shown), _NAME_COLUMN)
        sections = [
            ("Installed" if project else "Installed with ansible-core", mine, totals[0]),
            ("Not installed", others, totals[1]),
        ]
        sections = [s for s in sections if s[2]]
        for i, (title, items, total) in enumerate(sections):
            if i:
                print()
            self._section(title, total)
            for item in items:
                self._row(item, type_width, name_width)
            if hidden := total - len(items):
                hint = ", --limit 0 to show all" if i == len(sections) - 1 else ""
                print(self.s(f"   … {hidden} more{hint}", DIM))
        if self.terminal and _output.verbosity >= 0:
            self._legend(shown)

    def detail(self, item: Found, alternatives: list[Found] | None = None) -> None:
        plugin = item.plugin
        title = f" {plugin.fqcn} "
        kind = f" {plugin.type} "
        rule = "─" * max(2, min(self.width, 100) - len(title) - len(kind) - 4)
        print(
            self.s("╭─", DIM)
            + self.s(title, BOLD, BLUE)
            + self.s(rule, DIM)
            + self.s(kind, MAGENTA)
            + self.s("─╮", DIM)
        )
        if plugin.short_description:
            print(f"  {self.rich(as_sentence(markup(plugin.short_description, role=plugin.type == 'role')))}")
        elif item.route and not item.route.problem and _routing_only(plugin):
            print(f"  Another name for {item.route.final}.")
        print()

        marker, colour, verdict = self._verdict(item)
        self._field("Installed", self.s(f"{marker} {verdict}", colour))
        for routing in (plugin.tombstone, plugin.deprecation):
            if text := _warning_text(routing):
                self._field("", self.s(text, RED if routing is plugin.tombstone else YELLOW))

        if plugin.collection == BUILTIN:
            # Its since is the plugin's version_added, left out when that's "historical".
            if not has_file(plugin):
                versions = "none: it's only in ansible-core's routing"
            else:
                versions = f"in ansible-core, added in {plugin.since}" if plugin.since else "in ansible-core"
        elif plugin.until is not None:
            versions = f"{plugin.since or '?'} to {plugin.until}, removed in {plugin.until}"
        elif plugin.since is not None:
            versions = f"since {plugin.since}"
        else:
            versions = "none: it's only in the collection's routing"
        self._field("Versions", versions)

        # The plugin, then each name its redirects lead to: whether each step's collection is installed, and where,
        # is what decides the verdict above, so it's shown on the step it applies to.
        steps = [(plugin.fqcn, plugin), *(item.route.hops if item.route else [])]
        width = max(len(name) for name, _ in steps)
        for i, (name, step) in enumerate(steps):
            last = i == len(steps) - 1
            if step is None:
                mark, colour = "↪", YELLOW
            elif i == 0:
                mark, colour = self._step_marker(step)
            elif step.tombstone is not None:
                mark, colour = "↪", RED
            else:
                mark, colour = "↪", CYAN if self._step_marker(step)[0] == "✓" else YELLOW
            text = self.s(name.ljust(width), BOLD) if last and i else name.ljust(width)
            info = self._step(step, describe=last and i > 0)
            self._field("Route" if i == 0 else "", f"{self.s(mark, colour)} {text}  {info}".rstrip())

        for i, other in enumerate(alternatives or []):
            mark, colour = self._marker(other)
            text = f"{self.s(mark, colour)} {other.plugin.fqcn}"
            if other.plugin.short_description:
                text += f"  {self.s(plain(other.plugin.short_description, role=other.plugin.type == 'role'), DIM)}"
            self._field("Same name" if i == 0 else "", text)
        if alternatives:
            self._field("", self.s("not redirects: only the name is the same, so check one does the job", DIM))

        if item.copy:
            self._field(
                "Note",
                self.s(
                    f"probably a copy of {item.copy.of}: {item.copy.shared} of its {item.copy.total} plugins "
                    + "have the same names",
                    DIM,
                ),
            )
        if hint := self._hint(item):
            self._field("Hint", hint)
        print(self.s("╰" + "─" * (len(title) + len(kind) + len(rule) + 2) + "╯", DIM))

    def _verdict(self, item: Found) -> tuple[str, str, str]:
        """Whether the plugin can be used by this name, as its marker, colour and a sentence saying why not."""
        plugin = item.plugin
        route = item.route
        usable = item.usable
        if usable is None and route is not None:
            return "?", YELLOW, f"can't tell: {route.unknown}"
        if usable:
            via = f", as {route.final}" if route else ""
            if item.state == DEPRECATED:
                return "⚠", YELLOW, f"yes{via}, but it's {self._notes(item, route=False)}"
            return "✓", GREEN, f"yes{via}"
        if item.state == BROKEN and route is not None:
            return "✗", RED, f"no: {route.problem}"
        if item.state == REMOVED:
            installed = f"; {item.version} is installed" if item.version is not None else ""
            return "✗", RED, f"no: {self._notes(item, route=False)}{installed}"
        if not item.installed:
            return "·", DIM, f"no: {plugin.collection} isn't installed"
        if item.state == MISSING:
            verb = "is" if len(item.missing) == 1 else "are"
            return "↪", YELLOW, f"no: {_and(item.missing)} {verb}n't installed"
        if item.outdated:
            collection, since = item.outdated[0]
            have = self.installed[collection].version if collection in self.installed else None
            return "↑", YELLOW, f"no: {collection} {have} doesn't have it, it was added in {since}"
        return "↑", YELLOW, f"no: {plugin.collection} {item.version} doesn't have it, it was added in {plugin.since}"

    def _hint(self, item: Found) -> str | None:
        """The command that makes the plugin usable: installing every collection its route needs, or upgrading to a
        version that has it."""
        project = self.settings.project.config_file is not None
        collection = item.plugin.collection
        if item.state == NEWER:
            names = " ".join(f"-P {c}" for c, _ in item.outdated) if item.outdated else f"-P {collection}"
            command = f"alai sync {names}" if project else f"alai collection install {names}"
            return f"upgrade it with {self.s(command, BOLD)}"
        if item.state in (REMOVED, BROKEN) or (item.route and (item.route.problem or item.route.unknown)):
            return None
        if item.route and not item.installed:
            # The old name needs its own collection too; the name it leads to is the one to use.
            if not item.missing:
                return f"use {item.route.final} instead, which is installed"
            install = " ".join(item.missing)
            command = f"alai add {install}" if project else f"alai collection install {install}"
            return f"use {item.route.final} instead: install it with {self.s(command, BOLD)}"
        names = item.missing or ([collection] if collection != BUILTIN and collection not in self.installed else [])
        if not names:
            return None
        command = f"alai add {' '.join(names)}" if project else f"alai collection install {' '.join(names)}"
        return f"install it with {self.s(command, BOLD)}"

    def _step_marker(self, step: Plugin) -> tuple[str, str]:
        """A route step's own state: ``✓`` its collection is installed and has it (or redirects on from it), ``·``
        not installed, ``↑`` installed but too old, ``✗`` removed from the version installed."""
        collection = step.collection
        if collection == BUILTIN:
            return "✓", GREEN
        if collection not in self.installed:
            return "·", DIM
        have = self.installed[collection].version
        since, until = semver_or_none(step.since), semver_or_none(step.until)
        if have is not None and since is not None and have < since:
            return "↑", YELLOW
        if step.tombstone is not None or (
            until is not None and step.redirect is None and (have is None or have >= until)
        ):
            return "✗", RED
        return "✓", GREEN

    def _step(self, step: Plugin | None, *, describe: bool) -> str:
        """What's known about a route step: its description with ``describe`` (where a redirect leads; the plugin's
        own is at the top), removal and deprecation, and its collection: where it's installed, or that it isn't, or is
        too old."""
        if step is None:
            return self.s("not in the plugin index", YELLOW)
        bits: list[str] = []
        if describe and step.short_description:
            bits.append(self.rich(markup(step.short_description, role=step.type == "role")))
        if step.until is not None:
            bits.append(self.s(f"removed in {step.until}", DIM))
        if step.deprecation is not None:
            bits.append(self.s("deprecated", YELLOW))
        if step.tombstone is not None and step.until is None:
            bits.append(self.s("tombstoned", RED))
        collection = step.collection
        mark = self._step_marker(step)[0]
        if collection == BUILTIN:
            bits.append(self.s("with ansible-core", DIM))
        elif collection not in self.installed:
            bits.append(self.s(f"{collection} isn't installed", YELLOW))
        else:
            found = self.installed[collection]
            version = str(found.version) if found.version is not None else "an unknown version"
            where = f"{version} in {home_path(os.path.abspath(found.path))}"
            bits.append(self.s(f"needs {step.since}, {where}", YELLOW) if mark == "↑" else self.s(where, DIM))
        return self.s(", ", DIM).join(bits)

    def _section(self, title: str, count: int) -> None:
        label = f" {title} ({count}) "
        print(
            self.s("──", DIM) + self.s(label, BOLD) + self.s("─" * max(2, min(self.width, 100) - len(label) - 2), DIM)
        )

    def _row(self, item: Found, type_width: int, name_width: int) -> None:
        plugin = item.plugin
        marker, colour = self._marker(item)
        head = f" {self.s(marker, colour)} {self.s(plugin.type.ljust(type_width), MAGENTA)}  "
        head += self._name(item) + " " * max(0, name_width - len(plugin.fqcn))
        notes = self._notes(item, route=True)
        room = None
        if self.terminal:
            room = self.width - (4 + type_width + 2 + max(name_width, len(plugin.fqcn)) + 2) - 1
        if room is not None and room < 10:
            tail = ""
        elif notes:
            tail = self.pieces([(notes, (colour,), None)], room)
        elif item.copy:
            tail = self.pieces(
                [(plain(plugin.short_description or "", role=plugin.type == "role"), (DIM,), None)], room
            )
        else:
            tail = self.rich(markup(plugin.short_description or "", role=plugin.type == "role"), room)
        print(f"{head}  {tail}".rstrip())

    def rich(self, segments: list[Segment], room: int | None = None) -> str:
        """A description's pieces with their markup styled, and ``L()`` and ``U()`` as links on a terminal; cut to
        ``room`` visible characters, if given, with an ellipsis."""
        return self.pieces([(piece, _STYLES.get(kind or "", ()), url) for piece, kind, url in segments], room)

    def _name(self, item: Found) -> str:
        """The FQCN, the collection blue (dim for a probable copy) and the name bold, with the part that matched a
        partial query underlined."""
        plugin = item.plugin
        collection = self.s(f"{plugin.collection}.", DIM if item.copy else BLUE)
        name = plugin.name
        text = self.query.text
        at = name.find(text) if not self.query.glob and not self.query.qualified and not item.exact else -1
        if at < 0:
            return collection + self.s(name, BOLD)
        end = at + len(text)
        return collection + self.s(name[:at], BOLD) + self.s(name[at:end], BOLD, UNDERLINE) + self.s(name[end:], BOLD)

    def _marker(self, item: Found) -> tuple[str, str]:
        if item.state == OK and item.installed:
            return "✓", GREEN
        return _MARKERS[item.state]

    def _notes(self, item: Found, *, route: bool) -> str:
        """Why the plugin isn't simply there to use, or an empty string if it is. ``route`` includes where its
        redirects lead, which the details show hop by hop instead."""
        plugin = item.plugin
        notes: list[str] = []
        installed = f", installed {item.version}" if item.version is not None else ""
        if item.state == NEWER and not item.outdated:
            notes.append(f"added in {plugin.since}{installed}")
        tombstone = plugin.tombstone
        if tombstone is not None:
            when = tombstone.get("removal_version") or tombstone.get("removal_date")
            notes.append(f"removed in {when}" if when else "removed")
        elif plugin.until is not None and item.state in (REMOVED, REDIRECT, MISSING):
            notes.append(f"removed in {plugin.until}{installed if item.state == REMOVED else ''}")
        if item.route and tombstone is None:
            if item.route.problem:
                notes.append(f"broken redirect: {item.route.problem}")
            elif route and item.route.unknown:
                notes.append(f"→ {item.route.final} (not in the plugin index)")
            elif route:
                more = len(item.route.hops) - 1
                notes.append(f"→ {item.route.final}" + (f" (via {more} more)" if more else ""))
        if item.state == MISSING:
            notes.append(f"{_and(item.missing)} {'is' if len(item.missing) == 1 else 'are'}n't installed")
        for collection, since in item.outdated if item.state == NEWER else ():
            have = self.installed[collection].version if collection in self.installed else None
            notes.append(f"added to {collection} in {since}, installed {have}")
        deprecation = plugin.deprecation
        if deprecation is not None and tombstone is None:
            when = deprecation.get("removal_version") or deprecation.get("removal_date")
            notes.append(f"deprecated, removal in {when}" if when else "deprecated")
        return "; ".join(notes)

    def _legend(self, shown: list[Found]) -> None:
        markers = {self._marker(f) for f in shown}
        parts = [f"{self.s(m, colour)} {self.s(text, DIM)}" for m, colour, text in _LEGEND if (m, colour) in markers]
        if any(f.copy for f in shown):
            parts.append(f"{self.s('gray', DIM)} {self.s('probably a copy of another collection', DIM)}")
        print()
        print("  ".join(parts))

    def _field(self, label: str, value: str) -> None:
        print(f"  {self.s(label.ljust(10), DIM)} {value}")


def _routing_only(plugin: Plugin) -> bool:
    """Whether the line is only its collection's routing, with no plugin file in any version: another name."""
    if plugin.collection == BUILTIN:
        return not has_file(plugin)
    return plugin.since is None and plugin.until is None


Segment = tuple[str, "str | None", "str | None"]
"""A piece of a description: its text, the markup it came from (alai-core's kind, such as ``code``, or None for plain
text), and the URL it links to, for ``L()`` and ``U()``."""

_STYLES: dict[str, tuple[str, ...]] = {
    "bold": (BOLD,),
    "italic": (ITALIC,),
    "code": (CYAN,),
    "option_value": (CYAN,),
    "env_var": (CYAN,),
    "option_name": (BOLD,),
    "return_value": (BOLD,),
    "module": (BLUE,),
    "plugin": (BLUE,),
}
"""How each markup is shown in colour: code and values cyan, options and return values bold as docs.ansible.com shows
them, plugin names blue as collections are in the list. ``L()`` and ``U()`` are links instead; ``R()``'s anchor is
on docs.ansible.com, so its text is plain."""


def markup(text: str, *, role: bool = False) -> list[Segment]:
    """A description split at Ansible's documentation markup, each piece the text it shows: ``M()`` and ``P()`` the
    name, ``L()`` and ``R()`` the text, ``U()`` the URL, ``O()`` and ``RV()`` the option or return value (and
    ``=value``) without the plugin, entry point or ``ignore:`` before it, and ``V()`` and ``E()`` their value.
    ``HORIZONTALLINE`` is a space. Markup that doesn't parse is shown as written. ``role`` is whether it's a role's,
    whose ``O(entrypoint:name)`` names another entry point."""
    out: list[Segment] = []
    for part in parse_markup(text, role=role):
        kind = part.kind
        url = None
        if kind in ("text", "error"):
            shown, kind = part.text or "", None
        elif kind == "horizontal_line":
            shown, kind = " ", None
        elif kind in ("module", "plugin"):
            shown = part.fqcn or ""
        elif kind in ("url", "link"):
            url = part.url if (part.url or "").startswith(("http://", "https://")) else None
            shown = (part.url if kind == "url" else part.text) or ""
        elif kind in ("env_var", "option_name", "return_value"):
            shown = (part.name or "") + (f"={part.value}" if part.value is not None else "")
        elif kind == "option_value":
            shown = part.value or ""
        else:
            shown = part.text or ""
        out.append((shown, kind, url))
    return out


def plain(text: str, *, role: bool = False) -> str:
    """A description with its markup reduced to the text it shows."""
    return "".join(piece for piece, _, _ in markup(text, role=role))


def as_sentence(segments: list[Segment]) -> list[Segment]:
    """A description's pieces as a sentence: capitalised and ending in a full stop, as Ansible's convention leaves them
    out and collections differ. The first piece is only capitalised if it's plain text, and a first word with anything
    but lowercase letters (``oVirt``, ``floating_ips``, ``tasks/main.yml``) is a name, so it's left as it is."""
    segments = [s for s in segments if s[0]]
    if segments:
        segments[0] = (segments[0][0].lstrip(), *segments[0][1:])
        segments[-1] = (segments[-1][0].rstrip(), *segments[-1][1:])
    segments = [s for s in segments if s[0]]
    if not segments:
        return segments
    first, kind, url = segments[0]
    if kind is None:
        segments[0] = (_capitalise(first), kind, url)
    if segments[-1][0][-1] not in ".!?":
        segments.append((".", None, None))
    return segments


def _capitalise(text: str) -> str:
    """``text`` with its first letter upper case, unless its first word is a name rather than a word: anything but
    lowercase letters (``oVirt``, ``floating_ips``, ``tasks/main.yml``)."""
    first = text.split()[0].rstrip(",:;") if text.split() else ""
    if re.fullmatch(r"[a-z]+(?:[-/][a-z]+)*", first):
        return text[0].upper() + text[1:]
    return text


def _and(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _or(items: list[str]) -> str:
    """``'a'``, ``'a' or 'b'``, ``'a', 'b' or 'c'``."""
    quoted = [f"'{i}'" for i in items]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"
