# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import typing as _t
from pathlib import Path

import pytest
from alai_core.plugins import PluginEntry
from alai_core.semver import Version
from index_data import plugin

from alai import _ansible_package, _plugins
from alai._plugins import (
    BROKEN,
    DEPRECATED,
    MISSING,
    NEWER,
    OK,
    REDIRECT,
    REMOVED,
    Catalog,
    Copy,
    Plugin,
    PluginIndex,
    Query,
    assess,
    cached_collections,
    cached_copies,
    find_copies,
    follow,
    rank,
)


def parse_line(line: bytes) -> Plugin | None:
    entry = PluginEntry.parse(line)
    return None if entry is None else _plugins.PluginIndex("", b"")._plugins([entry])[0]


EXAMPLE = Path(__file__).parent / "data" / "index" / "plugins.jsonl"


def data(*lines: dict[str, object]) -> bytes:
    return "".join(json.dumps(line) + "\n" for line in lines).encode()


def catalog(*lines: dict[str, object], copies: dict[str, Copy] | None = None) -> Catalog:
    return Catalog([PluginIndex("test", data(*lines))], copies)


def names(found: list[Plugin]) -> list[str]:
    return sorted(f"{p.type}:{p.fqcn}" for p in found)


class TestParseLine:
    def test_example(self) -> None:
        plugins = list(PluginIndex("alai", EXAMPLE.read_bytes()).with_tokens())

        assert len(plugins) == 7
        old = next(p for p in plugins if p.name == "old_thing")
        assert old.fqcn == "alai_example.demo.old_thing"
        assert (old.since, old.until, old.redirect, old.index) == ("0.1.0", "0.2.0", "alai_example.demo.thing", "alai")

    @pytest.mark.parametrize(
        "line",
        [
            b"not json",
            b"[]",
            b'{"v": 2, "type": "module", "name": "a", "collection": "a.a"}',
            b'{"v": true, "type": "module", "name": "a", "collection": "a.a"}',
            b'{"v": 1, "name": "a", "collection": "a.a"}',
            b'{"v": 1, "type": "module", "name": "a", "collection": "a"}',
            b'{"v": 1.0, "type": "module", "name": "a", "collection": "a.a"}',
            b'{"v": 1, "type": "module", "name": 1, "collection": "a.a"}',
        ],
    )
    def test_skipped(self, line: bytes) -> None:
        assert parse_line(line) is None

    def test_older_v_read(self) -> None:
        assert parse_line(b'{"v": 0, "type": "module", "name": "a", "collection": "a.a"}') is not None

    def test_fields_not_strings(self) -> None:
        parsed = parse_line(
            b'{"v": 1, "type": "module", "name": "a", "collection": "a.a", "since": 1, "short_description": null}'
        )

        assert parsed is not None
        assert (parsed.since, parsed.short_description) == (None, None)

    def test_unknown_fields_kept(self) -> None:
        parsed = parse_line(b'{"v": 1, "type": "new_type", "name": "a", "collection": "a.a", "extra": 1}')

        assert parsed is not None
        assert parsed.type == "new_type"
        assert parsed.raw["extra"] == 1

    def test_routing_not_a_table(self) -> None:
        parsed = parse_line(b'{"v": 1, "type": "module", "name": "a", "collection": "a.a", "routing": "x"}')

        assert parsed is not None
        assert parsed.routing == {}


class TestPluginIndex:
    def test_every_token_once(self) -> None:
        index = PluginIndex("test", data(plugin("a.a.user_user"), plugin("a.a.user"), plugin("b.b.other")))

        found = index.with_tokens([b"user_user", b"a.a"])

        assert [p.fqcn for p in found] == ["a.a.user_user"]
        assert [p.fqcn for p in index.with_tokens([b"user"])] == ["a.a.user_user", "a.a.user"]

    def test_named(self) -> None:
        lines = data(plugin("a.a.user"), plugin("a.a.ping"), plugin("b.b.users"))
        spaced = b'{"v": 1, "type": "module", "name" :\t"user", "collection": "c.c"}\n'
        index = PluginIndex("test", lines + spaced)

        assert [p.fqcn for p in index.named({"user", "nope"})] == ["a.a.user", "c.c.user"]
        assert index.named(set()) == []

    def test_names(self) -> None:
        escaped = b'{"v": 1, "type": "module", "name": "a\\"b", "collection": "c.c"}\n'
        index = PluginIndex("test", data(plugin("a.a.user"), plugin("b.b.ping")) + escaped + b"not json\n")

        assert index.names() == {"user", "ping"}

    def test_raw_read_when_asked(self) -> None:
        [found] = PluginIndex("test", data(plugin("a.a.user") | {"extra": [1, 2]})).with_tokens()

        assert found.line.startswith(b"{")
        assert found.raw["extra"] == [1, 2]


class TestQuery:
    LINES = (
        plugin("a.a.user"),
        plugin("a.a.win_user"),
        plugin("b.b.user", "role"),
        plugin("b.b.users_info"),
        plugin("c.c.other"),
    )

    @pytest.mark.parametrize(
        ("text", "kwargs", "expected"),
        [
            ("user", {}, ["module:a.a.user", "module:a.a.win_user", "module:b.b.users_info", "role:b.b.user"]),
            ("user", {"exact": True}, ["module:a.a.user", "role:b.b.user"]),
            ("user", {"types": ["role"]}, ["role:b.b.user"]),
            ("*_info", {}, ["module:b.b.users_info"]),
            ("u?er", {}, ["module:a.a.user", "role:b.b.user"]),
            ("a.a.*", {}, ["module:a.a.user", "module:a.a.win_user"]),
            ("*.*.user", {}, ["module:a.a.user", "role:b.b.user"]),
            ("a.a.user", {}, ["module:a.a.user"]),
            ("a.a.use", {}, []),
            ("nothing", {}, []),
        ],
    )
    def test_matches(self, text: str, kwargs: dict[str, object], expected: list[str]) -> None:
        assert names(catalog(*self.LINES).search(Query(text, **kwargs))) == expected  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize(
        ("text", "tokens"),
        [
            ("user", [b"user"]),
            ("*_in[fo]o", [b"_in", b"o"]),
            ("community.general.*", [b"community", b"general"]),
            ("ü", []),
            ('a"b', []),
        ],
    )
    def test_tokens(self, text: str, tokens: list[bytes]) -> None:
        assert Query(text).tokens() == tokens

    def test_escaped_name_still_found(self) -> None:
        # JSON may write a name with escapes, which the raw bytes don't contain as typed, so it's parsed instead.
        index = Catalog([PluginIndex("t", b'{"v":1,"type":"module","name":"caf\\u00e9","collection":"a.a"}\n')])

        assert names(index.search(Query("café"))) == ["module:a.a.café"]

    def test_legacy_is_builtin(self) -> None:
        index = catalog(plugin("ansible.builtin.copy"), plugin("ansible.builtin.ping"))

        query = Query("ansible.legacy.copy")

        assert (query.legacy, query.typed, query.text) == (True, "ansible.legacy.copy", "ansible.builtin.copy")
        assert names(index.search(query)) == ["module:ansible.builtin.copy"]
        assert names(index.search(Query("ansible.legacy.*"))) == [
            "module:ansible.builtin.copy",
            "module:ansible.builtin.ping",
        ]

    def test_fqcn(self) -> None:
        assert Query("a.a.user").fqcn
        assert not Query("a.a.*").fqcn
        assert not Query("user").fqcn

    def test_is_exact(self) -> None:
        query = Query("user")

        assert query.is_exact(Plugin("module", "user", "a.a"))
        assert not query.is_exact(Plugin("module", "win_user", "a.a"))
        assert Query("a.a.user").is_exact(Plugin("module", "user", "a.a"))


class TestCatalog:
    def test_first_index_wins(self) -> None:
        first = PluginIndex("first", data(plugin("a.a.thing", short_description="first")))
        second = PluginIndex("second", data(plugin("a.a.thing", short_description="second"), plugin("b.b.thing")))

        found = Catalog([first, second]).search(Query("thing"))

        assert [(p.fqcn, p.index) for p in found] == [("a.a.thing", "first"), ("b.b.thing", "second")]

    def test_get(self) -> None:
        index = catalog(plugin("a.a.thing"), plugin("a.a.thing", "filter"), plugin("a.a.thing_two"))

        found = index.get("filter", "a.a.thing")

        assert found is not None
        assert found.type == "filter"
        assert index.get("lookup", "a.a.thing") is None

    def test_line_per_match(self) -> None:
        # A line that holds the token more than once is still one result.
        index = catalog(plugin("a.a.user", short_description="user user user"))

        assert len(index.search(Query("user"))) == 1

    def test_preload_routes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = catalog(
            plugin("a.a.one", routing={"redirect": "b.b.two"}),
            plugin("b.b.two", routing={"redirect": "c.c.three"}),
            plugin("c.c.three"),
        )
        start = index.search(Query("a.a.one"))

        index.preload_routes(start)
        monkeypatch.setattr(PluginIndex, "search", lambda *_: pytest.fail("searched after preloading"))
        route = follow(start[0], index)

        assert route is not None
        assert [name for name, _ in route.hops] == ["b.b.two", "c.c.three"]

    def test_suggest(self) -> None:
        index = catalog(plugin("a.a.docker_container"), plugin("a.a.docker_image"), plugin("a.a.copy"))

        assert index.suggest(Query("dockr_container")) == ["docker_container"]
        assert index.suggest(Query("zzz")) == []


class TestFollow:
    def route(self, *lines: dict[str, object]) -> tuple[list[str], str | None, str | None]:
        index = catalog(*lines)
        start = index.get("module", "a.a.start")
        assert start is not None
        route = follow(start, index)
        assert route is not None
        return [name for name, _ in route.hops], route.problem, route.unknown

    def test_no_redirect(self) -> None:
        assert follow(Plugin("module", "x", "a.a"), catalog()) is None

    def test_one_hop(self) -> None:
        assert self.route(plugin("a.a.start", routing={"redirect": "b.b.end"}), plugin("b.b.end")) == (
            ["b.b.end"],
            None,
            None,
        )

    def test_chain(self) -> None:
        hops, problem, _ = self.route(
            plugin("a.a.start", until="2.0.0", routing={"redirect": "b.b.middle"}),
            plugin("b.b.middle", until="2.1.0", routing={"redirect": "b.b.end", "deprecation": {}}),
            plugin("b.b.end"),
        )

        assert (hops, problem) == (["b.b.middle", "b.b.end"], None)

    def test_type_stays_the_same(self) -> None:
        _, _, unknown = self.route(plugin("a.a.start", routing={"redirect": "b.b.end"}), plugin("b.b.end", "filter"))

        assert unknown == "b.b.end isn't in the plugin index"

    def test_to_itself(self) -> None:
        assert self.route(plugin("a.a.start", routing={"redirect": "a.a.start"}))[1] == "redirects to itself"

    def test_loop(self) -> None:
        _, problem, _ = self.route(
            plugin("a.a.start", routing={"redirect": "b.b.b"}), plugin("b.b.b", routing={"redirect": "a.a.start"})
        )

        assert problem == "a redirect loop"

    def test_not_fqcn(self) -> None:
        assert self.route(plugin("a.a.start", routing={"redirect": "short"}))[1] == (
            "redirect to 'short', which isn't a FQCN"
        )

    def test_to_tombstone(self) -> None:
        _, problem, _ = self.route(
            plugin("a.a.start", routing={"redirect": "b.b.end"}), plugin("b.b.end", routing={"tombstone": {}})
        )

        assert problem == "b.b.end was removed"

    def test_to_legacy_file(self) -> None:
        # ansible.legacy finds ansible-core's own file before reading any routing, as ansible.builtin.smart's
        # redirect to ansible.legacy.ssh relies on.
        hops, problem, unknown = self.route(
            plugin("a.a.start", routing={"redirect": "ansible.legacy.ssh"}),
            plugin("ansible.builtin.ssh", short_description="connect via SSH client binary"),
        )

        assert (hops, problem, unknown) == (["ansible.legacy.ssh"], None, None)

    def test_to_legacy_routing(self) -> None:
        # With no file, ansible.legacy falls back to ansible.builtin's routing.
        hops, problem, _ = self.route(
            plugin("a.a.start", routing={"redirect": "ansible.legacy.thing"}),
            plugin("ansible.builtin.thing", routing={"redirect": "b.b.thing"}),
            plugin("b.b.thing"),
        )

        assert (hops, problem) == (["ansible.legacy.thing", "b.b.thing"], None)

    def test_to_legacy_nothing(self) -> None:
        _, problem, unknown = self.route(plugin("a.a.start", routing={"redirect": "ansible.legacy.local"}))

        assert problem is None
        assert unknown == "ansible.builtin has no local, so only a local plugin can be it"

    @pytest.mark.parametrize(
        ("installed", "ends"),
        [
            # An installed version from before the removal still has the plugin, so the route ends there.
            ({"b.b": "1.5.0"}, True),
            ({"b.b": "2.0.0"}, False),
            ({}, False),
        ],
    )
    def test_removed_step_in_an_older_installed_version(self, installed: dict[str, str], ends: bool) -> None:
        index = catalog(plugin("a.a.start", routing={"redirect": "b.b.end"}), plugin("b.b.end", until="2.0.0"))
        start = index.get("module", "a.a.start")
        assert start is not None

        route = follow(start, index, {name: Version(v) for name, v in installed.items()})

        assert route is not None
        assert route.problem == (None if ends else "b.b.end was removed in 2.0.0")

    def test_tombstoned_step_in_an_older_installed_version(self) -> None:
        index = catalog(
            plugin("a.a.start", routing={"redirect": "b.b.end"}),
            plugin("b.b.end", routing={"tombstone": {"removal_version": "3.0.0"}}),
        )
        start = index.get("module", "a.a.start")
        assert start is not None

        route = follow(start, index, {"b.b": Version("2.9.0")})

        assert route is not None
        assert route.problem is None

    def test_to_removed(self) -> None:
        _, problem, _ = self.route(
            plugin("a.a.start", routing={"redirect": "b.b.end"}), plugin("b.b.end", until="3.0.0")
        )

        assert problem == "b.b.end was removed in 3.0.0"


class TestAssess:
    def state(self, line: dict[str, object], *others: dict[str, object], installed: str | None = None) -> str:
        index = catalog(line, *others)
        [found] = index.search(Query(f"{line['collection']}.{line['name']}"))
        versions = {str(line["collection"]): Version(installed)} if installed else {}
        return assess(found, index, versions, Query("x")).state

    def test_ok(self) -> None:
        assert self.state(plugin("a.a.x", since="1.0.0")) == OK

    def test_newer_than_installed(self) -> None:
        assert self.state(plugin("a.a.x", since="2.0.0"), installed="1.5.0") == NEWER

    def test_removed_from_latest(self) -> None:
        assert self.state(plugin("a.a.x", since="1.0.0", until="2.0.0")) == REMOVED

    def test_removed_after_installed_version(self) -> None:
        assert self.state(plugin("a.a.x", since="1.0.0", until="2.0.0"), installed="1.5.0") == OK

    def test_removed_by_installed_version(self) -> None:
        assert self.state(plugin("a.a.x", since="1.0.0", until="2.0.0"), installed="2.0.0") == REMOVED

    def test_redirect_wins_over_removal(self) -> None:
        # ansible-core reads routing before the plugin's file, so a removed plugin that redirects still loads.
        line = plugin("a.a.x", until="2.0.0", routing={"redirect": "b.b.y"})

        assert self.state(line, plugin("b.b.y")) == REDIRECT

    def test_tombstone_wins_over_redirect(self) -> None:
        line = plugin("a.a.x", routing={"tombstone": {}, "redirect": "b.b.y"})

        assert self.state(line, plugin("b.b.y")) == REMOVED

    def test_broken_redirect(self) -> None:
        assert self.state(plugin("a.a.x", routing={"redirect": "a.a.x"})) == BROKEN

    def test_deprecated(self) -> None:
        assert self.state(plugin("a.a.x", routing={"deprecation": {"removal_version": "3.0.0"}})) == DEPRECATED

    def test_redirect_to_missing_collection(self) -> None:
        line = plugin("a.a.x", routing={"redirect": "b.b.y"})

        assert self.state(line, plugin("b.b.y"), installed="1.0.0") == MISSING
        # Not installed itself, it's a redirect like any other; installing what it leads to is the hint.
        assert self.state(line, plugin("b.b.y")) == REDIRECT

    def test_every_hop_must_be_installed(self) -> None:
        index = catalog(
            plugin("a.a.x", routing={"redirect": "b.b.y"}),
            plugin("b.b.y", routing={"redirect": "c.c.z"}),
            plugin("c.c.z"),
        )
        [start] = index.search(Query("a.a.x"))
        versions = {"a.a": Version("1.0.0"), "c.c": Version("1.0.0")}

        found = assess(start, index, versions, Query("x"))

        assert (found.state, found.missing) == (MISSING, ["b.b"])

    def test_route_into_an_old_version(self) -> None:
        index = catalog(plugin("a.a.x", routing={"redirect": "b.b.y"}), plugin("b.b.y", since="2.0.0"))
        [start] = index.search(Query("a.a.x"))

        found = assess(start, index, {"a.a": None, "b.b": Version("1.0.0")}, Query("x"))

        assert (found.state, found.outdated, found.usable) == (NEWER, [("b.b", "2.0.0")], False)

    @pytest.mark.parametrize(
        ("lines", "installed", "usable"),
        [
            ([plugin("a.a.x")], {"a.a"}, True),
            ([plugin("a.a.x")], set[str](), False),
            ([plugin("a.a.x", routing={"deprecation": {}})], {"a.a"}, True),
            ([plugin("a.a.x", routing={"redirect": "b.b.y"}), plugin("b.b.y")], {"a.a", "b.b"}, True),
            ([plugin("a.a.x", routing={"redirect": "b.b.y"})], {"a.a", "b.b"}, None),
            ([plugin("a.a.x", until="1.0.0")], {"a.a"}, False),
        ],
    )
    def test_usable(self, lines: list[dict[str, object]], installed: set[str], usable: bool | None) -> None:
        index = catalog(*lines)
        [start] = index.search(Query("a.a.x"))

        found = assess(start, index, dict.fromkeys(installed), Query("x"))

        assert found.usable is usable

    def test_builtin_is_installed(self) -> None:
        index = catalog(plugin("ansible.builtin.copy"))

        found = assess(index.search(Query("copy"))[0], index, {}, Query("copy"))

        assert found.installed
        assert found.version is None


class TestRank:
    def order(self, *lines: dict[str, object], query: str = "user", cached: set[str] | None = None) -> list[str]:
        index = catalog(*lines, copies={"fork.general": Copy("community.general", 9, 10)})
        found = [assess(p, index, {}, Query(query)) for p in index.search(Query(query))]
        return [f.plugin.fqcn for f in sorted(found, key=lambda f: rank(f, cached or set()))]

    def test_order(self) -> None:
        assert self.order(
            plugin("zzz.zzz.user"),
            plugin("fork.general.user"),
            plugin("community.zzz.user"),
            plugin("community.general.user"),
            plugin("ansible.builtin.user"),
            plugin("aaa.aaa.win_user"),
            plugin("gone.gone.user", until="1.0.0"),
        ) == [
            "ansible.builtin.user",
            "community.general.user",  # in the ansible package
            "community.zzz.user",  # in a namespace the package has
            "zzz.zzz.user",
            "fork.general.user",  # a probable copy
            "gone.gone.user",  # removed
            "aaa.aaa.win_user",  # a partial match
        ]

    def test_redirects_after_plugins(self) -> None:
        assert self.order(
            plugin("ansible.builtin.win_user", routing={"redirect": "ansible.windows.win_user"}),
            plugin("zzz.zzz.win_user"),
            plugin("ansible.windows.win_user"),
            query="win_user",
        ) == ["ansible.windows.win_user", "zzz.zzz.win_user", "ansible.builtin.win_user"]

    def test_cached_first(self) -> None:
        assert self.order(plugin("community.general.user"), plugin("zzz.zzz.user"), cached={"zzz.zzz"}) == [
            "zzz.zzz.user",
            "community.general.user",
        ]


class TestCopies:
    def test_copy_of_package_collection(self) -> None:
        own = [plugin(f"community.general.p{i}") for i in range(10)]
        copied = [plugin(f"fork.general.p{i}") for i in range(6)] + [plugin("fork.general.mine")]
        renamed = [plugin(f"community.renamed.p{i}") for i in range(5)]  # a package namespace: never judged
        few = [plugin(f"tiny.tiny.p{i}") for i in range(2)]  # too few plugins to judge
        mostly_own = [plugin(f"own.own.p{i}") for i in range(2)] + [plugin(f"own.own.x{i}") for i in range(5)]

        copies = find_copies(Plugin(**_kw(line)) for line in [*own, *copied, *renamed, *few, *mostly_own])

        assert copies == {"fork.general": Copy("community.general", 6, 7)}

    def test_cached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        body = data(*[plugin(f"community.general.p{i}") for i in range(4)], *[plugin(f"f.f.p{i}") for i in range(4)])
        url = "https://index.example/plugins.jsonl"

        first = cached_copies(str(tmp_path), url, body)
        monkeypatch.setattr("alai._plugins.find_copies", lambda _: pytest.fail("worked out again"))
        again = cached_copies(str(tmp_path), url, body)

        assert first == again == {"f.f": Copy("community.general", 4, 4)}

    def test_cache_follows_the_file(self, tmp_path: Path) -> None:
        url = "https://index.example/plugins.jsonl"
        _ = cached_copies(str(tmp_path), url, data(*[plugin(f"community.general.p{i}") for i in range(4)]))

        changed = cached_copies(
            str(tmp_path),
            url,
            data(*[plugin(f"community.general.p{i}") for i in range(4)], *[plugin(f"f.f.p{i}") for i in range(4)]),
        )

        assert "f.f" in changed

    def test_cache_follows_the_package_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "https://index.example/plugins.jsonl"
        body = data(*[plugin(f"community.general.p{i}") for i in range(4)], *[plugin(f"f.f.p{i}") for i in range(4)])
        _ = cached_copies(str(tmp_path), url, body)
        monkeypatch.setattr(_ansible_package, "RELEASES", "99-100")
        monkeypatch.setattr("alai._plugins.find_copies", lambda _: {})

        assert cached_copies(str(tmp_path), url, body) == {}


def test_cached_collections(tmp_path: Path) -> None:
    for cksum, name in (("a" * 64, "a"), ("b" * 64, "b")):
        directory = tmp_path / "collections-v1" / cksum
        directory.mkdir(parents=True)
        _ = (directory / "MANIFEST.json").write_text(json.dumps({"collection_info": {"namespace": "ns", "name": name}}))
    (tmp_path / "collections-v1" / ("c" * 64)).mkdir()
    _ = (tmp_path / "collections-v1" / ("c" * 64) / "MANIFEST.json").write_text("{")

    assert cached_collections(str(tmp_path)) == {"ns.a", "ns.b"}


def _kw(line: dict[str, object]) -> dict[str, _t.Any]:
    return {"type": str(line["type"]), "name": str(line["name"]), "collection": str(line["collection"])}
