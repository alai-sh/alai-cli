# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import contextlib
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from index_data import make_index, v
from index_server import IndexServer, serve

from alai import _output
from alai._config import Index
from alai._fetch import Fetcher
from alai._index import (
    IndexClient,
    IndexConfig,
    IndexDataError,
    Listed,
    artifact_url,
    index_file_url,
    parse_collections,
    parse_config,
    parse_index_file,
)
from alai._pep440 import Version as AnsibleVersion
from alai._semver import Version

EXAMPLE = Path(__file__).parent / "data" / "index"
ROOT = "https://index.example/"
CKSUM = "a" * 64


def line(**fields: object) -> dict[str, object]:
    """A valid index line for ns.coll 1.0.0, with ``fields`` changed (None removes one)."""
    obj: dict[str, object] = {
        "v": 1,
        "name": "ns.coll",
        "vers": "1.0.0",
        "deps": [],
        "cksum": CKSUM,
        "size": 10,
        "yanked": False,
        "published": "2026-10-05T17:30:02Z",
    }
    obj.update(fields)
    return {k: v for k, v in obj.items() if v is not None}


def index_file(*lines: dict[str, object] | str) -> bytes:
    return b"\n".join((json.dumps(x) if isinstance(x, dict) else x).encode() for x in lines)


def config(**changes: object) -> bytes:
    obj: dict[str, object] = {
        "formats": {"1": {"index": "index/{namespace}/{name}.jsonl", "collections": "collections.json"}},
        "dl": "https://dl.example/sha256/{sha256-prefix}/{sha256-checksum}.tar.gz",
        "moved-to": None,
    }
    obj.update(changes)
    return json.dumps(obj).encode()


class TestConfig:
    def test_example(self) -> None:
        parsed, moved_to = parse_config((EXAMPLE / "config.json").read_bytes(), ROOT, f"{ROOT}config.json")

        assert parsed == IndexConfig(
            root=ROOT,
            format=1,
            index="index/{namespace}/{name}.jsonl",
            collections_url=f"{ROOT}collections.json",
            dl="https://dl.alai.sh/sha256/{sha256-prefix}/{sha256-checksum}.tar.gz",
            plugins_url=f"{ROOT}plugins.jsonl",
        )
        assert moved_to is None

    def test_no_plugin_index(self) -> None:
        parsed, _ = parse_config(config(), ROOT, "u")

        assert parsed.plugins_url is None

    def test_highest_readable_format(self) -> None:
        formats = {
            "1": {"index": "v1/{namespace}/{name}.jsonl", "collections": "c1.json"},
            "2": {"index": "v2/{namespace}/{name}.jsonl", "collections": "c2.json"},
        }

        parsed, _ = parse_config(config(formats=formats), ROOT, "u")

        assert (parsed.format, parsed.index) == (1, "v1/{namespace}/{name}.jsonl")

    @pytest.mark.parametrize(
        ("template", "hashed"),
        [
            ("index/{namespace}/{name}.jsonl", False),
            ("index/{sha256-prefix}/{sha256-checksum}.jsonl", True),
            ("https://files.example/{namespace}.{name}-{sha256-checksum}.jsonl", True),
        ],
    )
    def test_index_template(self, template: str, hashed: bool) -> None:
        parsed, _ = parse_config(config(formats={"1": {"index": template, "collections": "c.json"}}), ROOT, "u")

        assert (parsed.index, parsed.hashed) == (template, hashed)

    @pytest.mark.parametrize(
        ("root", "path"),
        [
            ("file:///srv/index/", "https://files.example/{namespace}/{name}.jsonl"),
            ("http://index.example/", "https://files.example/{namespace}/{name}.jsonl"),
        ],
    )
    def test_index_elsewhere(self, root: str, path: str) -> None:
        parsed, _ = parse_config(config(formats={"1": {"index": path, "collections": "c.json"}}), root, "u")

        assert parsed.index == path

    def test_moved_to(self) -> None:
        assert parse_config(config(**{"moved-to": "https://new.example/"}), ROOT, "u")[1] == "https://new.example/"

    @pytest.mark.parametrize(
        ("data", "match"),
        [
            (b"[]", "expected a JSON object"),
            (b"{", "not valid JSON"),
            (config(formats=[]), "'formats' must be an object"),
            (config(formats={"2": {}, "x": {}}), "needs a newer alai: it offers index format 2, this alai reads 1"),
            (config(formats={}), "it offers index format none"),
            (config(formats={"1": "index/"}), r"'formats\.1' must be an object"),
            (config(formats={"1": {"index": "index/"}}), "must have 'index' and 'collections' paths"),
            (
                config(formats={"1": {"index": "index/", "collections": "c.json"}}),
                r"'formats\.1\.index' must be a URL template naming each file",
            ),
            (
                config(formats={"1": {"index": "index/{name}.jsonl", "collections": "c.json"}}),
                r"'formats\.1\.index' must be a URL template naming each file",
            ),
            (
                config(formats={"1": {"index": "index/{sha256-prefix}.jsonl", "collections": "c.json"}}),
                r"'formats\.1\.index' must be a URL template naming each file",
            ),
            (
                config(formats={"1": {"index": "http://files.example/{namespace}/{name}.jsonl", "collections": "c"}}),
                r"'formats\.1\.index': refusing http://files\.example/ns/name\.jsonl, from an index at https://",
            ),
            (
                config(formats={"1": {"index": "file:///srv/{sha256-checksum}.jsonl", "collections": "c.json"}}),
                r"'formats\.1\.index': refusing file:///srv/0{64}\.jsonl",
            ),
            (
                config(formats={"1": {"index": "i/{namespace}/{name}", "collections": "http://e.example/c.json"}}),
                r"'formats\.1\.collections': refusing http://e\.example/c\.json",
            ),
            (
                config(formats={"1": {"index": "i/{namespace}/{name}", "collections": "c", "plugins": "ftp://p"}}),
                r"'formats\.1\.plugins': refusing ftp://p",
            ),
            (config(dl="https://dl.example/artifact.tar.gz"), "'dl' must be a URL template with a placeholder"),
            (config(dl=None), "'dl' must be a URL template"),
            (config(**{"moved-to": 1}), "'moved-to' must be a URL or null"),
            (
                config(formats={"1": {"index": "i/{namespace}/{name}", "collections": "c.json", "plugins": 1}}),
                r"'formats\.1\.plugins' must be a path",
            ),
        ],
    )
    def test_invalid(self, data: bytes, match: str) -> None:
        with pytest.raises(IndexDataError, match=match):
            _ = parse_config(data, ROOT, f"{ROOT}config.json")


class TestCollections:
    def test_example(self) -> None:
        parsed = parse_collections((EXAMPLE / "collections.json").read_bytes(), "u")

        assert {name: entry.updated for name, entry in parsed.items()} == {
            "alai_example.demo": "2026-09-20T09:00:00Z",
            "community.general": "2026-10-05T17:30:02Z",
            "community.library_inventory_filtering_v1": "2025-10-25T11:31:55Z",
        }
        demo = EXAMPLE / "index" / "alai_example" / "demo.jsonl"
        assert parsed["alai_example.demo"].sha256 == hashlib.sha256(demo.read_bytes()).hexdigest()

    @pytest.mark.parametrize("value", ["ABC", "a" * 63, "A" * 64, 1, None])
    def test_malformed_sha256_ignored(self, value: object) -> None:
        data = json.dumps({"v": 1, "namespaces": {"ns": {"coll": {"updated": "t", "sha256": value}}}}).encode()

        assert parse_collections(data, "u") == {"ns.coll": Listed("t")}

    @pytest.mark.parametrize(
        ("data", "match"),
        [
            (b'{"v": 2, "namespaces": {}}', "needs a newer alai: it's version 2"),
            (b'{"namespaces": {}}', "expected 'v': 1 and a 'namespaces' object"),
            (b'{"v": 1, "namespaces": []}', "expected 'v': 1"),
            (b'{"v": 1, "namespaces": {"ns": []}}', "namespace 'ns' must be an object"),
        ],
    )
    def test_invalid(self, data: bytes, match: str) -> None:
        with pytest.raises(IndexDataError, match=match):
            _ = parse_collections(data, "u")

    def test_missing_fields(self) -> None:
        assert parse_collections(b'{"v": 1, "namespaces": {"ns": {"coll": {}}, "x": {"y": 1}}}', "u") == {
            "ns.coll": Listed(""),
            "x.y": Listed(""),
        }


class TestIndexFile:
    def test_example(self) -> None:
        url = f"{ROOT}index/alai_example/demo.jsonl"

        parsed = parse_index_file(
            (EXAMPLE / "index" / "alai_example" / "demo.jsonl").read_bytes(), "alai_example.demo", url
        )

        assert [v.version.text for v in parsed.versions] == ["0.1.0", "0.1.1", "0.2.0-rc.1", "0.2.0"]
        assert [v.yanked for v in parsed.versions] == [False, True, False, False]
        newest = parsed.versions[-1]
        assert [(name, str(req)) for name, req in newest.deps] == [("community.general", ">=13.0.0,<14.0.0")]
        assert newest.deps[0][1].contains(Version("13.5.0"))
        assert newest.requires_ansible is not None
        assert newest.requires_ansible.contains(AnsibleVersion("2.18.0"))
        assert newest.problem is None
        assert (parsed.newer, parsed.invalid) == (0, ())

    def test_real_data(self) -> None:
        data = (EXAMPLE / "index" / "community" / "general.jsonl").read_bytes()

        parsed = parse_index_file(data, "community.general", "u")

        assert [v.version.text for v in parsed.versions] == ["13.4.0", "12.6.6", "13.5.0"]
        assert all(v.problem is None for v in parsed.versions)

    def test_optional_fields(self) -> None:
        parsed = parse_index_file(
            index_file(line(deprecated="use other.coll", requires_ansible=None, sig=[])),
            "ns.coll",
            "u",
        )

        version = parsed.versions[0]
        assert version.deprecated == "use other.coll"
        assert version.requires_ansible is None

    def test_blank_lines_and_unknown_fields(self) -> None:
        parsed = parse_index_file(index_file("", line(future_field={"x": 1}), "   ", ""), "ns.coll", "u")

        assert len(parsed.versions) == 1
        assert parsed.invalid == ()

    def test_newer_lines_skipped_and_counted(self) -> None:
        parsed = parse_index_file(
            index_file(line(), line(v=2, vers="2.0.0", anything="new"), line(v=3)), "ns.coll", "u"
        )

        assert [v.version.text for v in parsed.versions] == ["1.0.0"]
        assert parsed.newer == 2

    @pytest.mark.parametrize(
        ("bad", "match"),
        [
            ("{", "u:2: not valid JSON"),
            ("[1]", "u:2: not a JSON object"),
            (line(v=0), "'v' must be 1"),
            (line(v=True), "'v' must be 1"),
            (line(name="ns.other"), "'name' must be 'ns.coll', the file's collection"),
            (line(vers="1.0"), "invalid version '1.0'"),
            (line(vers=None), "'vers' is required and must be a string"),
            (line(cksum="ABC"), "'cksum' must be a lowercase hex sha256"),
            (line(size=-1), "'size' must not be negative"),
            (line(size=True), "'size' is required and must be an integer"),
            (line(yanked="no"), "'yanked' is required and must be a boolean"),
            (line(deps={}), "'deps' is required and must be an array"),
            (line(deps=[{"name": "a.b"}]), "each of 'deps' must have a 'name' and a 'req' string"),
            (line(deprecated=1), "'deprecated' must be true or a message"),
            (line(requires_ansible=2), "'requires_ansible' must be a string"),
            (line(vers="0.1.0"), None),  # a valid line, to show the others are judged alone
        ],
    )
    def test_invalid_line_skipped(self, bad: dict[str, object] | str, match: str | None) -> None:
        parsed = parse_index_file(index_file(line(), bad), "ns.coll", "u")

        assert parsed.versions[0].version.text == "1.0.0"
        if match is None:
            assert parsed.invalid == ()
        else:
            assert len(parsed.versions) == 1
            assert len(parsed.invalid) == 1
            assert match in parsed.invalid[0]
            assert parsed.invalid[0].startswith("u:2: ")

    def test_version_checked_first(self) -> None:
        # The version was checked before the fields after it, so its message wins when both are wrong.
        parsed = parse_index_file(index_file(line(vers="1.0", cksum="ABC", size=-1)), "ns.coll", "u")

        [invalid] = parsed.invalid
        assert invalid.startswith("u:1: invalid version '1.0'")

    @pytest.mark.parametrize(
        ("text", "accepted"),
        [
            ('{"v": 1.0', True),  # equal to 1, as Python compared it
            ('{"v": 2.0', False),  # not an integer, so not a newer line: unreadable
            ('{"v": -1', False),
        ],
    )
    def test_v_by_value(self, text: str, accepted: bool) -> None:
        bad = json.dumps(line(v=None)).replace("{", f"{text}, ", 1)

        parsed = parse_index_file(index_file(bad), "ns.coll", "u")

        assert (len(parsed.versions), parsed.newer) == ((1, 0) if accepted else (0, 0))
        assert parsed.invalid == (() if accepted else ("u:1: 'v' must be 1",))

    def test_whitespace_and_byte_order_mark(self) -> None:
        # bytes.strip's whitespace, \x0b and \x0c included, makes a line blank, and json.loads skips a BOM.
        data = b"\x0b\x0c \t\r\n\xef\xbb\xbf" + json.dumps(line()).encode() + b"\r\n"

        parsed = parse_index_file(data, "ns.coll", "u")

        assert [v.version.text for v in parsed.versions] == ["1.0.0"]
        assert parsed.invalid == ()

    def test_null_optional_fields(self) -> None:
        # null is no requires_ansible, but neither a deprecated flag nor a message.
        parsed = parse_index_file(
            index_file(
                json.dumps(line()).replace("{", '{"requires_ansible": null, ', 1),
                line(vers="2.0.0") | {"deprecated": None},
            ),
            "ns.coll",
            "u",
        )

        assert [v.version.text for v in parsed.versions] == ["1.0.0"]
        assert parsed.versions[0].requires_ansible is None
        assert parsed.invalid == ("u:2: 2.0.0: 'deprecated' must be true or a message",)

    def test_duplicate_version(self) -> None:
        parsed = parse_index_file(index_file(line(), line(cksum="b" * 64)), "ns.coll", "u")

        assert [v.cksum for v in parsed.versions] == [CKSUM]
        assert parsed.invalid == ("u:2: version 1.0.0 is listed more than once",)

    @pytest.mark.parametrize(
        ("fields", "problem"),
        [
            ({"deps": [{"name": "a.b", "req": "^3.0.0"}]}, "dependency a.b: invalid version requirement '^3.0.0'"),
            ({"deps": [{"name": "Not.Valid", "req": "*"}]}, "dependency Not.Valid: invalid collection name"),
            ({"requires_ansible": "2.17"}, "requires_ansible '2.17': invalid specifier '2.17'"),
        ],
    )
    def test_unusable_version_kept(self, fields: dict[str, object], problem: str) -> None:
        parsed = parse_index_file(index_file(line(**fields)), "ns.coll", "u")

        assert parsed.invalid == ()
        version = parsed.versions[0]
        assert version.problem is not None
        assert version.problem.startswith(problem)

    def test_unusable_keeps_what_was_read(self) -> None:
        deps = [{"name": "a.b", "req": "^1"}, {"name": "c.d", "req": ">=1"}]

        version = parse_index_file(index_file(line(deps=deps)), "ns.coll", "u").versions[0]

        assert [name for name, _ in version.deps] == ["c.d"]
        assert version.problem is not None
        assert version.problem.startswith("dependency a.b:")

    def test_artifact_url(self) -> None:
        version = parse_index_file(index_file(line(vers="1.0.0+build.1")), "ns.coll", "u").versions[0]
        dl = "https://dl.example/{namespace}/{name}/{version}/{sha256-prefix}/{sha256-checksum}.tar.gz"
        parsed = IndexConfig(root=ROOT, format=1, index="i/{namespace}/{name}", collections_url=ROOT, dl=dl)

        assert (
            artifact_url(parsed, version.name, version.version, version.cksum)
            == f"https://dl.example/ns/coll/1.0.0%2Bbuild.1/aa/{CKSUM}.tar.gz"
        )


@pytest.fixture
def fetcher() -> _c.Iterator[Fetcher]:
    with Fetcher(user_agent="alai-test") as f:
        yield f


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    _ = shutil.copytree(EXAMPLE, root)
    return root


@pytest.fixture
def server(site: Path) -> _c.Iterator[IndexServer]:
    yield from serve(site)


def client(url: str, fetcher: Fetcher, cache: Path) -> IndexClient:
    return IndexClient(Index("test", url, source="alai.toml"), fetcher, str(cache))


class TestIndexClient:
    @pytest.mark.parametrize("over", ["file", "http"])
    def test_reads_example(self, request: pytest.FixtureRequest, fetcher: Fetcher, tmp_path: Path, over: str) -> None:
        url = f"{EXAMPLE.as_uri()}/" if over == "file" else request.getfixturevalue("server").url
        index = client(url, fetcher, tmp_path / "cache")

        assert index_file_url(index.config(), "community.general") == f"{url}index/community/general.jsonl"
        assert sorted(index.collections()) == [
            "alai_example.demo",
            "community.general",
            "community.library_inventory_filtering_v1",
        ]
        assert index.has("community.general")
        assert not index.has("community.docker")
        general = index.versions("community.general")
        assert general is not None
        assert len(general.versions) == 3
        assert index.versions("community.docker") is None

    def test_http_cache_layout(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        index = client(server.url, fetcher, tmp_path / "cache")

        _ = index.versions("community.general")

        [directory] = (tmp_path / "cache" / "index").iterdir()
        assert directory.name.startswith("127.0.0.1-")
        assert sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()) == [
            "collections.json",
            "config.json",
            "index/community/general.jsonl",
        ]

    def test_file_index_not_cached(self, fetcher: Fetcher, tmp_path: Path) -> None:
        _ = client(f"{EXAMPLE.as_uri()}/", fetcher, tmp_path / "cache").versions("community.general")

        assert not (tmp_path / "cache").exists()

    def test_each_file_once(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        index = client(server.url, fetcher, tmp_path / "cache")

        for _ in range(3):
            _ = index.config()
            _ = index.collections()
            _ = index.versions("community.general")
            _ = index.versions("community.docker")

        assert sorted(server.paths()) == [
            "/collections.json",
            "/config.json",
            "/index/community/docker.jsonl",
            "/index/community/general.jsonl",
        ]

    def test_prefetch(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        index = client(server.url, fetcher, tmp_path / "cache")
        names = ["community.general", "community.library_inventory_filtering_v1", "alai_example.demo"]

        index.prefetch(names)
        files = [index.versions(n) for n in names]

        assert all(f is not None for f in files)
        assert sorted(server.paths()) == [
            "/collections.json",
            "/config.json",
            "/index/alai_example/demo.jsonl",
            "/index/community/general.jsonl",
            "/index/community/library_inventory_filtering_v1.jsonl",
        ]

    def test_first_run_guesses_listing(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        index = client(server.url, fetcher, tmp_path / "cache")

        _ = index.config()
        # Started with config.json, from where the spec's layout puts it, before the config said where it is.
        _ = fetcher.fetch(f"{server.url}collections.json", None).result()

        assert sorted(server.paths()) == ["/collections.json", "/config.json"]

    def test_first_run_guess_elsewhere(self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path) -> None:
        (site / "listing").mkdir()
        _ = (site / "collections.json").rename(site / "listing" / "collections.json")
        _ = (site / "config.json").write_bytes(
            config(
                formats={"1": {"index": "index/{namespace}/{name}.jsonl", "collections": "listing/collections.json"}}
            )
        )
        index = client(server.url, fetcher, tmp_path / "cache")

        assert index.has("community.general")
        _ = fetcher.fetch(f"{server.url}collections.json", None).result()
        # The guess was one unused request.
        assert sorted(server.paths()) == ["/collections.json", "/config.json", "/listing/collections.json"]

    def test_no_prefetch(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        # As sync's downloads read it: config.json alone, for the artifact URLs.
        for _ in range(2):  # with nothing cached, then with the config cached
            index = IndexClient(Index("test", server.url, source="t"), fetcher, str(tmp_path / "cache"), prefetch=None)
            _ = index.config()

        assert server.paths() == ["/config.json"]

    def test_plugins(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        index = IndexClient(Index("test", server.url, source="t"), fetcher, str(tmp_path / "cache"), prefetch="plugins")

        data = index.plugins()

        assert data == (EXAMPLE / "plugins.jsonl").read_bytes()
        # collections.json, which finding plugins doesn't use, isn't fetched.
        assert sorted(server.paths()) == ["/config.json", "/plugins.jsonl"]

    def test_no_plugins(self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path) -> None:
        _ = (site / "config.json").write_bytes(config())

        assert client(server.url, fetcher, tmp_path / "cache").plugins() is None

    def test_plugins_missing(self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path) -> None:
        (site / "plugins.jsonl").unlink()

        assert client(server.url, fetcher, tmp_path / "cache").plugins() is None

    def test_not_an_index(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        with pytest.raises(IndexDataError, match=r"config\.json not found: is .* an alai index\?"):
            _ = client(f"{server.url}nope/", fetcher, tmp_path / "cache").config()

    def test_collections_missing(self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path) -> None:
        (site / "collections.json").unlink()

        with pytest.raises(IndexDataError, match=r"collections\.json not found"):
            _ = client(server.url, fetcher, tmp_path / "cache").collections()

    def test_invalid_name(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid collection name"):
            _ = client(server.url, fetcher, tmp_path / "cache").versions("../../etc")


class TestMovedTo:
    def test_follows_and_warns(
        self,
        server: IndexServer,
        site: Path,
        fetcher: Fetcher,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        old = site / "old"
        old.mkdir()
        _ = (old / "config.json").write_bytes(config(**{"moved-to": server.url.rstrip("/")}))
        monkeypatch.setattr(_output, "verbosity", 0)

        index = client(f"{server.url}old/", fetcher, tmp_path / "cache")

        assert index.config().root == server.url
        assert index.versions("community.general") is not None
        # With nothing cached, the listing was guessed to be beside the old config, where it isn't.
        paths = server.paths()
        assert "/old/collections.json" in paths
        assert [p for p in paths if p != "/old/collections.json"] == [
            "/old/config.json",
            "/config.json",
            "/collections.json",
            "/index/community/general.jsonl",
        ]
        assert capsys.readouterr().err == (
            f"warning: index 'test' has moved to {server.url}; update its url (from alai.toml)\n"
        )

    def test_loop(
        self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, target in (("a", "b"), ("b", "a")):
            (site / name).mkdir()
            _ = (site / name / "config.json").write_bytes(config(**{"moved-to": f"{server.url}{target}/"}))
        monkeypatch.setattr(_output, "verbosity", -1)

        with pytest.raises(IndexDataError, match="moved-to loops back to"):
            _ = client(f"{server.url}a/", fetcher, tmp_path / "cache").config()

    def test_too_many(
        self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for i in range(7):
            (site / str(i)).mkdir()
            _ = (site / str(i) / "config.json").write_bytes(config(**{"moved-to": f"{server.url}{i + 1}/"}))
        monkeypatch.setattr(_output, "verbosity", -1)

        with pytest.raises(IndexDataError, match="more than 5 moved-to hops"):
            _ = client(f"{server.url}0/", fetcher, tmp_path / "cache").config()

    def test_refuses_downgrade(self, tmp_path: Path) -> None:
        class Stub:
            def cached(self, cache_path: str | None) -> None:
                return None

            def get(self, url: str, cache_path: str | None) -> bytes:
                return config(**{"moved-to": "http://index.example/"})

            def fetch(self, url: str, cache_path: str | None) -> None:
                return None

        index = IndexClient(Index("test", ROOT), Stub(), str(tmp_path))  # pyright: ignore[reportArgumentType]

        with pytest.raises(IndexDataError, match=r"refusing to follow moved-to from https to http://index\.example/"):
            _ = index.config()


def relist(site: Path) -> None:
    """Rewrite the served collections.json with each index file's current SHA-256, as a publisher would."""
    listing = json.loads((site / "collections.json").read_text())
    for namespace, entries in listing["namespaces"].items():
        for name, entry in entries.items():
            entry["sha256"] = hashlib.sha256((site / "index" / namespace / f"{name}.jsonl").read_bytes()).hexdigest()
    _ = (site / "collections.json").write_text(json.dumps(listing))


@contextlib.contextmanager
def run(url: str, cache: Path, *, offline: bool = False) -> _c.Iterator[IndexClient]:
    """One alai run: a fresh Fetcher, so nothing is remembered from the last but the cache on disk."""
    with Fetcher(user_agent="alai-test", offline=offline) as f:
        yield IndexClient(Index("test", url, source="alai.toml"), f, str(cache))


GENERAL = "/index/community/general.jsonl"


class TestCachedIndexFiles:
    """The spec's "Cached index files": collections.json's sha256 against the cache's own bytes."""

    def test_unchanged_files_need_no_request(self, server: IndexServer, tmp_path: Path) -> None:
        with run(server.url, tmp_path) as index:
            _ = index.versions("community.general")
        server.requests.clear()

        with run(server.url, tmp_path) as index:
            general = index.versions("community.general")

        assert general is not None
        assert len(general.versions) == 3
        assert sorted(server.paths()) == ["/collections.json", "/config.json"]

    def test_listing_fetched_with_config(self, server: IndexServer, tmp_path: Path) -> None:
        with run(server.url, tmp_path) as index:
            _ = index.collections()
        server.requests.clear()
        server.delays["/config.json"] = 0.3

        with run(server.url, tmp_path) as index:
            _ = index.collections()

        arrived = {r.path: r.arrived for r in server.requests}
        # Asked for from the cached config's paths, before config.json answered.
        assert arrived["/collections.json"] < arrived["/config.json"] + 0.3

    def test_moved_listing_not_guessed_twice(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        with run(server.url, tmp_path) as index:
            _ = index.collections()
        _ = (site / "listing.json").write_bytes((site / "collections.json").read_bytes())
        _ = (site / "config.json").write_bytes(
            config(formats={"1": {"index": "index/{namespace}/{name}.jsonl", "collections": "listing.json"}})
        )
        server.requests.clear()

        with run(server.url, tmp_path) as index:
            assert index.has("community.general")

        # The guess from the old config is a wasted request; the new path is used.
        assert sorted(server.paths()) == ["/collections.json", "/config.json", "/listing.json"]

    def test_changed_file_fetched(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        with run(server.url, tmp_path) as index:
            # Waited for, not just prefetched: closing a Fetcher cancels prefetches that haven't started.
            assert index.versions("community.general") is not None
            assert index.versions("alai_example.demo") is not None
        general = site / GENERAL.lstrip("/")
        _ = general.write_bytes(general.read_bytes() + b"\n")
        relist(site)
        server.requests.clear()

        with run(server.url, tmp_path) as index:
            index.prefetch(["community.general", "alai_example.demo"])
            assert index.versions("alai_example.demo") is not None
            assert index.versions("community.general") is not None

        assert sorted(server.paths()) == ["/collections.json", "/config.json", GENERAL]

    def test_stale_copy_refetched_past_caches(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        with run(server.url, tmp_path) as index:
            _ = index.versions("community.general")
        general = site / GENERAL.lstrip("/")
        old = general.read_bytes()
        _ = general.write_bytes(old.replace(b'"yanked":false', b'"yanked":true', 1))
        relist(site)
        # A CDN still serving the old file at the plain URL.
        server.responses[GENERAL] = [(200, {}, old)]
        server.requests.clear()

        with run(server.url, tmp_path) as index:
            result = index.versions("community.general")

        assert result is not None
        assert result.versions[0].yanked
        targets = [r.target for r in server.requests if r.path == GENERAL]
        assert targets[0] == GENERAL
        assert targets[1].startswith(f"{GENERAL}?r=")
        assert len(targets[1]) == len(f"{GENERAL}?r=") + 16
        server.requests.clear()

        # The refetched copy was cached under the plain URL, so the next run needs no request for it.
        with run(server.url, tmp_path) as index:
            assert index.versions("community.general") == result
        assert GENERAL not in server.paths()

    def test_listing_older_than_file(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        listing = json.loads((site / "collections.json").read_text())
        listing["namespaces"]["community"]["general"]["sha256"] = "0" * 64
        _ = (site / "collections.json").write_text(json.dumps(listing))

        with run(server.url, tmp_path) as index:
            result = index.versions("community.general")

        # Fetched, refetched, still different: what was served is used.
        assert result is not None
        assert len(result.versions) == 3
        assert [r.target.partition("?")[0] for r in server.requests if r.path == GENERAL] == [GENERAL, GENERAL]

    def test_without_sha256_revalidates(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        listing = json.loads((site / "collections.json").read_text())
        for entries in listing["namespaces"].values():
            for entry in entries.values():
                del entry["sha256"]
        _ = (site / "collections.json").write_text(json.dumps(listing))
        with run(server.url, tmp_path) as index:
            _ = index.versions("community.general")
        server.requests.clear()

        with run(server.url, tmp_path) as index:
            _ = index.versions("community.general")

        [request] = [r for r in server.requests if r.path == GENERAL]
        assert "if-none-match" in request.headers

    def test_offline(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        with run(server.url, tmp_path) as index:
            _ = index.versions("community.general")
        general = site / GENERAL.lstrip("/")
        _ = general.write_bytes(general.read_bytes() + b"\n")
        relist(site)
        server.requests.clear()

        # Offline uses the cached listing and file, even where they disagree, and never asks the server.
        with run(server.url, tmp_path, offline=True) as index:
            assert index.versions("community.general") is not None
        assert server.requests == []

    @pytest.mark.parametrize("cached", [b"{not json", config(**{"moved-to": "https://elsewhere.example/"})])
    def test_no_guess_from_unusable_cached_config(
        self, server: IndexServer, tmp_path: Path, cached: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with run(server.url, tmp_path) as index:
            _ = index.collections()
            config_cache = index._cache_path(server.url, f"{server.url}config.json")
        assert config_cache is not None
        # No ETag, so the server sends its real config.json rather than a 304 for this one.
        header = json.dumps({"v": 1, "url": f"{server.url}config.json", "etag": None}).encode()
        _ = Path(config_cache).write_bytes(header + b"\n" + cached)
        monkeypatch.setattr(_output, "verbosity", -1)
        server.requests.clear()
        server.delays["/config.json"] = 0.2

        with run(server.url, tmp_path) as index:
            _ = index.collections()

        # No guess, so the listing waits for config.json.
        assert server.paths() == ["/config.json", "/collections.json"]


class TestIndexFileUrl:
    @pytest.mark.parametrize(
        ("template", "sha256", "expected"),
        [
            ("index/{namespace}/{name}.jsonl", None, f"{ROOT}index/ns/coll.jsonl"),
            ("index/{sha256-prefix}/{sha256-checksum}.jsonl", CKSUM, f"{ROOT}index/aa/{CKSUM}.jsonl"),
            # Absolute, on another host.
            ("https://files.example/{namespace}/{sha256-checksum}", CKSUM, f"https://files.example/ns/{CKSUM}"),
            # Filled in, then resolved: a leading / is from the host's root, as RFC 3986 has it.
            ("/i/{namespace}/{name}.jsonl", None, "https://index.example/i/ns/coll.jsonl"),
        ],
    )
    def test_filled_then_resolved(self, template: str, sha256: str | None, expected: str) -> None:
        parsed = IndexConfig(root=ROOT, format=1, index=template, collections_url=f"{ROOT}c.json", dl="d")

        assert index_file_url(parsed, "ns.coll", sha256) == expected

    def test_hashed_needs_sha256(self) -> None:
        parsed = IndexConfig(root=ROOT, format=1, index="i/{sha256-checksum}", collections_url=f"{ROOT}c.json", dl="d")

        with pytest.raises(
            IndexDataError, match=r"ns\.coll has no sha256, which .*config\.json's index template needs"
        ):
            _ = index_file_url(parsed, "ns.coll")


def hashed_site(root: Path) -> Path:
    """An index whose files are found by SHA-256, as index.alai.sh serves them."""
    _ = make_index(root, {"a.b": [v("1.0.0")], "c.d": [v("1.0.0", {"a.b": "*"})]}, hashed=True)
    return root


def republish(site: Path, name: str, extra: bytes, *, keep_old: bool = True) -> str:
    """Publish a new index file for ``name`` with ``extra`` appended, as a sync would: the new file first, then the
    listing pointing at it. The old file stays for a while, unless ``keep_old`` is False. Returns its new path."""
    listing = json.loads((site / "collections.json").read_text())
    namespace, short = name.split(".")
    entry = listing["namespaces"][namespace][short]
    old = site / "index" / entry["sha256"][:2] / f"{entry['sha256']}.jsonl"
    data = old.read_bytes() + extra
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    new = site / "index" / entry["sha256"][:2] / f"{entry['sha256']}.jsonl"
    new.parent.mkdir(exist_ok=True)
    _ = new.write_bytes(data)
    _ = (site / "collections.json").write_text(json.dumps(listing))
    if not keep_old:
        old.unlink()
    return f"/{new.relative_to(site)}"


def hashed_path(site: Path, name: str) -> str:
    namespace, short = name.split(".")
    sha256 = json.loads((site / "collections.json").read_text())["namespaces"][namespace][short]["sha256"]
    return f"/index/{sha256[:2]}/{sha256}.jsonl"


class TestHashedIndexFiles:
    """An index template with {sha256-checksum}: files that never change, found only through collections.json."""

    @pytest.fixture
    def hsite(self, tmp_path: Path) -> Path:
        return hashed_site(tmp_path / "hsite")

    @pytest.fixture
    def hserver(self, hsite: Path) -> _c.Iterator[IndexServer]:
        yield from serve(hsite)

    def test_reads_by_sha256(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path / "cache") as index:
            result = index.versions("c.d")

        assert result is not None
        assert [str(x.version) for x in result.versions] == ["1.0.0"]
        assert hashed_path(hsite, "c.d") in hserver.paths()

    def test_cached_by_collection(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path / "cache") as index:
            _ = index.versions("a.b")
        _ = republish(hsite, "a.b", b"\n")
        with run(hserver.url, tmp_path / "cache") as index:
            _ = index.versions("a.b")

        [directory] = (tmp_path / "cache" / "index").iterdir()
        # One copy, at the collection's own path, replaced rather than kept beside the new one.
        assert sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()) == [
            "collections.json",
            "config.json",
            "index/a/b.jsonl",
        ]

    def test_unchanged_files_need_no_request(self, hserver: IndexServer, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path) as index:
            _ = index.versions("a.b")
        hserver.requests.clear()

        with run(hserver.url, tmp_path) as index:
            assert index.versions("a.b") is not None

        assert sorted(hserver.paths()) == ["/collections.json", "/config.json"]

    def test_changed_file_fetched_without_etag(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path) as index:
            _ = index.versions("a.b")
        new = republish(hsite, "a.b", b"\n")
        hserver.requests.clear()

        with run(hserver.url, tmp_path) as index:
            assert index.versions("a.b") is not None

        [request] = [r for r in hserver.requests if r.path == new]
        # The cached copy's ETag is for the old URL, so it isn't sent to the new one.
        assert "if-none-match" not in request.headers

    def test_unlisted_needs_no_request(self, hserver: IndexServer, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path) as index:
            assert index.versions("x.y") is None
            index.prefetch(["x.y"])

        assert sorted(hserver.paths()) == ["/collections.json", "/config.json"]

    def test_prefetch(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path) as index:
            index.prefetch(["a.b", "c.d"])
            assert index.versions("a.b") is not None
            assert index.versions("c.d") is not None

        assert sorted(hserver.paths()) == sorted(
            ["/collections.json", "/config.json", hashed_path(hsite, "a.b"), hashed_path(hsite, "c.d")]
        )

    def test_gone_file_relists(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path) as index:
            _ = index.collections()
        cached = (hsite / "collections.json").read_bytes()
        old = hashed_path(hsite, "a.b")
        new = republish(hsite, "a.b", b"\n", keep_old=False)
        # A cache on the way still has the old listing, which names a file the host has since deleted.
        hserver.responses["/collections.json"] = [(200, {}, cached)]
        hserver.requests.clear()

        with run(hserver.url, tmp_path) as index:
            result = index.versions("a.b")

        assert result is not None
        targets = [r.target for r in hserver.requests]
        assert targets.count(old) == 1
        [relisted] = [t for t in targets if t.startswith("/collections.json?r=")]
        assert targets.index(old) < targets.index(relisted) < targets.index(new)

    def test_gone_file_still_listed(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        (hsite / hashed_path(hsite, "a.b").lstrip("/")).unlink()

        with (
            run(hserver.url, tmp_path) as index,
            pytest.raises(IndexDataError, match=r"not found, though .* lists a\.b"),
        ):
            _ = index.versions("a.b")

    def test_relisted_once(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        for name in ("a.b", "c.d"):
            (hsite / hashed_path(hsite, name).lstrip("/")).unlink()

        with run(hserver.url, tmp_path) as index:
            for name in ("a.b", "c.d"):
                with pytest.raises(IndexDataError, match="not found, though"):
                    _ = index.versions(name)

        assert len([r for r in hserver.requests if r.target.startswith("/collections.json?r=")]) == 1

    def test_removed_while_cached_listing_was_old(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        listing = json.loads((hsite / "collections.json").read_text())
        stale = json.dumps(listing).encode()
        (hsite / hashed_path(hsite, "a.b").lstrip("/")).unlink()
        del listing["namespaces"]["a"]
        _ = (hsite / "collections.json").write_text(json.dumps(listing))
        hserver.responses["/collections.json"] = [(200, {}, stale)]

        with run(hserver.url, tmp_path) as index:
            assert index.versions("a.b") is None

    def test_mismatch_is_an_error(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        path = hsite / hashed_path(hsite, "a.b").lstrip("/")
        _ = path.write_bytes(path.read_bytes() + b"\n")

        with run(hserver.url, tmp_path) as index, pytest.raises(IndexDataError, match="doesn't match its sha256 in"):
            _ = index.versions("a.b")

    def test_missing_sha256(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        listing = json.loads((hsite / "collections.json").read_text())
        del listing["namespaces"]["a"]["b"]["sha256"]
        _ = (hsite / "collections.json").write_text(json.dumps(listing))

        with run(hserver.url, tmp_path) as index:
            index.prefetch(["a.b"])
            with pytest.raises(IndexDataError, match=r"a\.b has no sha256"):
                _ = index.versions("a.b")

    def test_offline(self, hserver: IndexServer, hsite: Path, tmp_path: Path) -> None:
        with run(hserver.url, tmp_path) as index:
            _ = index.versions("a.b")
        # A later run fetched the new listing but not a.b's new file.
        _ = republish(hsite, "a.b", b"\n")
        with run(hserver.url, tmp_path) as index:
            _ = index.collections()
        hserver.requests.clear()

        # Offline uses the cached file, though the cached listing names another.
        with run(hserver.url, tmp_path, offline=True) as index:
            assert index.versions("a.b") is not None
        assert hserver.requests == []

    def test_trace_names_the_collection(
        self,
        hserver: IndexServer,
        hsite: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_output, "verbosity", 2)
        with run(hserver.url, tmp_path / "cache") as index:
            _ = index.versions("a.b")
        with run(hserver.url, tmp_path / "cache") as index:
            _ = index.versions("a.b")
        with run(f"{hsite.as_uri()}/", tmp_path / "cache") as index:
            _ = index.versions("c.d")

        err = capsys.readouterr().err
        url = f"{hserver.url}{hashed_path(hsite, 'a.b').lstrip('/')}"
        # The URL names only a hash; the trace says whose file it is, fetched, from the cache, or read from disk.
        assert f"trace: GET {url} (a.b): 200, " in err
        assert f"trace: {url} (a.b): the cached copy matches collections.json" in err
        assert f"{hashed_path(hsite, 'c.d').lstrip('/')} (c.d): " in err

    def test_file_index(self, hsite: Path, tmp_path: Path) -> None:
        with run(f"{hsite.as_uri()}/", tmp_path) as index:
            result = index.versions("c.d")

        assert result is not None
        assert not (tmp_path / "index").exists()
