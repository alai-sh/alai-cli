# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import sys
from pathlib import Path

import pytest
from index_data import cksum, make_index, v
from index_server import serve

from alai import _resolve
from alai._config import Index, raw_manifest
from alai._fetch import Fetcher
from alai._index import IndexClient
from alai._lock import IndexSource, Lock, LockedCollection
from alai._manifest import Manifest, parse_manifest
from alai._pep440 import SpecifierSet
from alai._pep440 import Version as AnsibleVersion
from alai._resolve import ResolutionError, admits_lower_bound, narrower_than, resolve
from alai._semver import Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def manifest(toml: str) -> Manifest:
    return parse_manifest(raw_manifest(tomllib.loads(toml), "alai.toml"))


@pytest.fixture
def fetcher(tmp_path: Path) -> _c.Iterator[Fetcher]:
    with Fetcher(user_agent="alai-test") as f:
        yield f


class Indexes:
    def __init__(self, tmp_path: Path, fetcher: Fetcher) -> None:
        self.tmp_path = tmp_path
        self.fetcher = fetcher
        self.clients: list[IndexClient] = []
        """In the order added, which is the order they're searched."""
        self.urls: dict[str, str] = {}

    def add(self, name: str, collections: dict[str, list[dict[str, object]]], **kwargs: dict[str, str]) -> Indexes:
        url = make_index(self.tmp_path / name, collections, **kwargs)
        self.urls[name] = url
        self.clients.append(IndexClient(Index(name, url), self.fetcher, str(self.tmp_path / "cache")))
        return self


@pytest.fixture
def indexes(tmp_path: Path, fetcher: Fetcher) -> Indexes:
    return Indexes(tmp_path, fetcher)


def chosen(result: _resolve.Resolution) -> dict[str, str]:
    return {c.name: c.version.text for c in result.collections}


PROJECT = '[project]\ncollections = ["a.a"]\n'


class TestBasic:
    def test_highest_with_dependencies(self, indexes: Indexes) -> None:
        indexes.add(
            "alai",
            {
                "a.a": [v("1.0.0"), v("2.0.0", {"b.b": ">=1.0.0"}), v("1.5.0")],
                "b.b": [v("1.0.0"), v("1.2.0")],
            },
        )

        result = resolve(manifest(PROJECT), indexes.clients)

        assert result.collections == (
            LockedCollection(
                "a.a",
                Version("2.0.0"),
                source=IndexSource(indexes.urls["alai"]),
                cksum=cksum("a.a", "2.0.0"),
                size=100,
                dependencies=("b.b",),
            ),
            LockedCollection(
                "b.b", Version("1.2.0"), source=IndexSource(indexes.urls["alai"]), cksum=cksum("b.b", "1.2.0"), size=100
            ),
        )
        assert result.warnings == ()

    def test_requirement_limits_version(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0"), v("3.0.0")]})

        assert chosen(resolve(manifest('[project]\ncollections = ["a.a>=1,<3"]\n'), indexes.clients)) == {
            "a.a": "2.0.0"
        }

    def test_backtracks(self, indexes: Indexes) -> None:
        # a.a 2.0.0 needs a b.b that doesn't exist, so 1.0.0 is chosen instead.
        indexes.add(
            "alai",
            {"a.a": [v("1.0.0", {"b.b": ">=1.0.0"}), v("2.0.0", {"b.b": ">=2.0.0"})], "b.b": [v("1.0.0")]},
        )

        assert chosen(resolve(manifest(PROJECT), indexes.clients)) == {"a.a": "1.0.0", "b.b": "1.0.0"}

    def test_backtracks_past_a_missing_dependency(self, indexes: Indexes) -> None:
        # a.a 2.0.0 depends on a collection the index doesn't have, so 1.0.0 is chosen, with no warning.
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", {"gone.gone": "*"})]})

        result = resolve(manifest(PROJECT), indexes.clients)

        assert chosen(result) == {"a.a": "1.0.0"}
        assert result.warnings == ()

    def test_backtracks_past_a_listed_but_missing_dependency(self, indexes: Indexes, tmp_path: Path) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", {"c.c": "*"})], "c.c": [v("1.0.0")]})
        (tmp_path / "alai" / "index" / "c" / "c.jsonl").unlink()

        result = resolve(manifest(PROJECT), indexes.clients)

        assert chosen(result) == {"a.a": "1.0.0"}
        assert result.warnings == ()

    def test_groups_resolved_with_the_project(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")], "t.t": [v("1.0.0", {"a.a": "<2.0.0"})]})
        toml = PROJECT + '[project.groups.test]\ncollections = ["t.t"]\n'

        # One resolution: the group's dependency holds a.a back for the project too.
        assert chosen(resolve(manifest(toml), indexes.clients)) == {"a.a": "1.0.0", "t.t": "1.0.0"}

    def test_requires_ansible_text_recorded(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15")]})

        assert resolve(manifest(PROJECT), indexes.clients).collections[0].requires_ansible == ">=2.15"


class TestFailures:
    def test_conflict_explained(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")], "b.b": [v("1.0.0", {"a.a": "<2.0.0"})]})
        toml = '[project]\ncollections = ["a.a>=2", "b.b"]\n'

        with pytest.raises(ResolutionError) as e:
            _ = resolve(manifest(toml), indexes.clients)

        assert str(e.value) == (
            "no version of a.a meets every requirement on it:\n"
            "  a.a>=2 (from the project)\n"
            "  a.a<2.0.0 (from b.b 1.0.0)"
        )

    def test_not_in_any_index(self, indexes: Indexes) -> None:
        indexes.add("alai", {"community.general": [v("1.0.0")]})

        with pytest.raises(
            ResolutionError,
            match=r"no configured index \('alai'\) has community\.generall; did you mean 'community\.general'\?",
        ):
            _ = resolve(manifest('[project]\ncollections = ["community.generall"]\n'), indexes.clients)

    def test_dependency_no_index_has(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", {"c.c": ">=1.0.0"})]})

        with pytest.raises(ResolutionError, match=r"^no configured index has c\.c, which a\.a 1\.0\.0 needs$"):
            _ = resolve(manifest(PROJECT), indexes.clients)

    def test_listed_but_missing(self, indexes: Indexes, tmp_path: Path) -> None:
        # The listing says the index has it, but the file isn't there: they're a sync apart.
        indexes.add("alai", {"a.a": [v("1.0.0", {"c.c": "*"})], "c.c": [v("1.0.0")]})
        (tmp_path / "alai" / "index" / "c" / "c.jsonl").unlink()

        with pytest.raises(ResolutionError, match=r"^index 'alai' doesn't have c\.c, which a\.a 1\.0\.0 needs$"):
            _ = resolve(manifest(PROJECT), indexes.clients)

    def test_reasons_versions_were_left_out(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", yanked=True), v("2.0.0", ra=">=2.20")]})
        toml = '[project]\nrequires_ansible = ">=2.17"\ncollections = ["a.a"]\n'

        with pytest.raises(ResolutionError) as e:
            _ = resolve(manifest(toml), indexes.clients)

        assert str(e.value).endswith(
            "  versions left out: 1 needing a newer ansible-core than the project's >=2.17, 1 yanked"
        )

    def test_too_deep(self, indexes: Indexes, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_resolve, "_MAX_ROUNDS", 1)
        indexes.add("alai", {"a.a": [v("1.0.0", {"b.b": "*"})], "b.b": [v("1.0.0")]})

        with pytest.raises(ResolutionError, match="gave up after 1 rounds"):
            _ = resolve(manifest(PROJECT), indexes.clients)


def test_bare_dependency_version_shown_exact(indexes: Indexes) -> None:
    # An index allows a bare version for an exact one; messages show it as a manifest would.
    indexes.add("alai", {"a.a": [v("1.0.0", {"b.b": "1.0.0"})], "b.b": [v("2.0.0")]})

    with pytest.raises(ResolutionError, match=r"b\.b==1\.0\.0 \(from a\.a 1\.0\.0\)"):
        _ = resolve(manifest(PROJECT), indexes.clients)


class TestCandidates:
    def test_yanked_skipped(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", yanked=True)]})

        assert chosen(resolve(manifest(PROJECT), indexes.clients)) == {"a.a": "1.0.0"}

    def test_yanked_kept_when_locked(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", yanked=True)]})
        locked = lock_of(indexes, {"a.a": "2.0.0"})

        result = resolve(manifest(PROJECT), indexes.clients, locked=locked)

        assert chosen(result) == {"a.a": "2.0.0"}
        assert result.warnings == ("a.a 2.0.0 is yanked",)

    def test_yanked_left_out_of_a_range(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", yanked=True)]})

        with pytest.raises(ResolutionError, match="versions left out: 1 yanked"):
            _ = resolve(manifest('[project]\ncollections = ["a.a>=1.5"]\n'), indexes.clients)

    @pytest.mark.parametrize(
        "toml",
        [
            '[project]\ncollections = ["a.a==2.0.0"]\n',
            '[project]\ncollections = ["b.b"]\n[project.groups.pin]\ncollections = ["a.a==2.0.0"]\n',
            '[project]\ncollections = ["b.b"]\n[project.overrides]\n"a.a" = "==2.0.0"\n',
        ],
    )
    def test_yanked_pinned_exactly_by_the_user(self, indexes: Indexes, toml: str) -> None:
        # As uv: the user's own == pin can choose a yanked version, with a warning.
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", yanked=True)], "b.b": [v("1.0.0", {"a.a": "*"})]})

        result = resolve(manifest(toml), indexes.clients)

        assert chosen(result)["a.a"] == "2.0.0"
        assert result.warnings == ("a.a 2.0.0 is yanked",)

    def test_yanked_pinned_by_a_dependency_left_out(self, indexes: Indexes) -> None:
        # Only the user's pins count, so a dependency can't pull in a yanked version.
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", yanked=True)], "b.b": [v("1.0.0", {"a.a": "==2.0.0"})]})

        with pytest.raises(ResolutionError, match="versions left out: 1 yanked"):
            _ = resolve(manifest('[project]\ncollections = ["b.b"]\n'), indexes.clients)

    def test_pre_release_skipped(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0-rc.1")]})

        assert chosen(resolve(manifest(PROJECT), indexes.clients)) == {"a.a": "1.0.0"}

    def test_pre_release_when_named(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0-rc.1")]})
        toml = '[project]\ncollections = ["a.a>=2.0.0-rc.1"]\n'

        assert chosen(resolve(manifest(toml), indexes.clients)) == {"a.a": "2.0.0-rc.1"}

    def test_pre_release_when_no_stable_version_fits(self, indexes: Indexes) -> None:
        # uv's default (if-necessary): stable first, a pre-release only when nothing stable fits.
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0-rc.1")]})

        result = resolve(manifest('[project]\ncollections = ["a.a>=1.5"]\n'), indexes.clients)

        assert chosen(result) == {"a.a": "2.0.0-rc.1"}

    def test_pre_release_for_a_dependency(self, indexes: Indexes) -> None:
        indexes.add("alai", {"p.p": [v("1.0.0", {"a.a": ">=1.5.0"})], "a.a": [v("1.0.0"), v("2.0.0-rc.1")]})

        result = resolve(manifest('[project]\ncollections = ["p.p"]\n'), indexes.clients)

        assert chosen(result)["a.a"] == "2.0.0-rc.1"

    def test_stable_preferred_over_a_newer_pre_release(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("1.1.0"), v("2.0.0-rc.1"), v("2.0.0-rc.2")]})

        assert chosen(resolve(manifest(PROJECT), indexes.clients)) == {"a.a": "1.1.0"}

    def test_pre_release_when_nothing_else(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("0.1.0-beta.1"), v("0.1.0-beta.2")]})

        assert chosen(resolve(manifest(PROJECT), indexes.clients)) == {"a.a": "0.1.0-beta.2"}

    def test_unusable_skipped_with_warning(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", {"b.b": "^1.0.0"})], "b.b": [v("1.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients)

        assert chosen(result) == {"a.a": "1.0.0"}
        assert result.warnings == (
            "skipped a.a 2.0.0, which can't be used: dependency b.b: invalid version requirement '^1.0.0': "
            + "can't read '^1.0.0'",
        )

    def test_unusable_older_than_chosen_not_mentioned(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra="2.17"), v("2.0.0")]})

        assert resolve(manifest(PROJECT), indexes.clients).warnings == ()

    def test_index_file_warnings(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("1.1.0", v=2)]}, raw_lines={"a.a": "{not json"})

        result = resolve(manifest(PROJECT), indexes.clients)

        assert chosen(result) == {"a.a": "1.0.0"}
        assert result.warnings == (
            "a.a: 1 versions in index 'alai' need a newer alai",
            "a.a: skipped 1 unreadable lines in index 'alai'",
        )


class TestRequiresAnsible:
    TOML = '[project]\nrequires_ansible = ">=2.17"\ncollections = ["a.a"]\n'

    def test_newer_minimum_skipped(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15"), v("2.0.0", ra=">=2.18")]})

        assert chosen(resolve(manifest(self.TOML), indexes.clients)) == {"a.a": "1.0.0"}

    def test_upper_bound_ignored_but_warned(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15,<2.20")]})

        result = resolve(manifest(self.TOML), indexes.clients)

        assert chosen(result) == {"a.a": "1.0.0"}
        assert result.warnings == (
            "a.a 1.0.0 supports ansible-core >=2.15,<2.20, which ends inside the project's requires_ansible >=2.17",
        )

    def test_upper_bound_beyond_the_project_not_warned(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15,<2.22")]})
        toml = self.TOML.replace(">=2.17", ">=2.17,<2.20")

        assert resolve(manifest(toml), indexes.clients).warnings == ()

    def test_ignore_requires_ansible(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15"), v("2.0.0", ra=">=2.18,<2.19")]})
        toml = self.TOML + '[project.overrides]\nignore_requires_ansible = ["a.a"]\n'

        result = resolve(manifest(toml), indexes.clients)

        assert chosen(result) == {"a.a": "2.0.0"}
        assert result.warnings == ()

    def test_unset_filters_nothing(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15"), v("2.0.0", ra=">=2.99")]})

        assert chosen(resolve(manifest(PROJECT), indexes.clients)) == {"a.a": "2.0.0"}

    def test_ansible_version_honours_upper_bounds(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15"), v("2.0.0", ra=">=2.15,<2.19")]})

        result = resolve(manifest(self.TOML), indexes.clients, ansible_version=AnsibleVersion("2.19.0"))

        assert chosen(result) == {"a.a": "1.0.0"}
        assert result.warnings == ()

    def test_ansible_version_replaces_the_range_rule(self, indexes: Indexes) -> None:
        # >=2.18 starts after the project's >=2.17, but supports the one version being resolved for.
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15"), v("2.0.0", ra=">=2.18")]})

        result = resolve(manifest(self.TOML), indexes.clients, ansible_version=AnsibleVersion("2.19.1"))

        assert chosen(result) == {"a.a": "2.0.0"}

    def test_ansible_version_compares_its_base(self, indexes: Indexes) -> None:
        # As ansible-core does: 2.19.0rc1 counts as 2.19.0.
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.19")]})

        result = resolve(manifest(PROJECT), indexes.clients, ansible_version=AnsibleVersion("2.19.0rc1"))

        assert chosen(result) == {"a.a": "1.0.0"}

    def test_ansible_version_without_a_project_range(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.15"), v("2.0.0", ra=">=2.20")]})

        result = resolve(manifest(PROJECT), indexes.clients, ansible_version=AnsibleVersion("2.17.0"))

        assert chosen(result) == {"a.a": "1.0.0"}

    def test_ansible_version_with_ignore(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra=">=2.99")]})
        toml = PROJECT + '[project.overrides]\nignore_requires_ansible = ["a.a"]\n'

        assert chosen(resolve(manifest(toml), indexes.clients, ansible_version=AnsibleVersion("2.17.0"))) == {
            "a.a": "1.0.0"
        }

    def test_ansible_version_left_out_reason(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", ra="<2.17")]})

        with pytest.raises(ResolutionError, match=r"versions left out: 1 not supporting ansible-core 2\.19\.0$"):
            _ = resolve(manifest(PROJECT), indexes.clients, ansible_version=AnsibleVersion("2.19.0"))

    def test_ansible_version_outside_the_project_range(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0")]})

        with pytest.raises(
            ResolutionError, match=r"^ansible-core 2\.16\.0 isn't in the project's requires_ansible >=2\.17$"
        ):
            _ = resolve(manifest(self.TOML), indexes.clients, ansible_version=AnsibleVersion("2.16.0"))

    @pytest.mark.parametrize(
        ("collection", "project", "expected"),
        [
            (">=2.15", ">=2.17", True),
            (">=2.17", ">=2.17", True),
            (">=2.18", ">=2.17", False),
            (">2.17", ">=2.17", False),
            (">2.17", ">2.17", True),
            (">=2.17", ">2.17", True),
            (">=2.15,<2.16", ">=2.17", True),  # upper bounds are ignored
            ("<2.20", ">=2.17", True),
            (">=2.99", "<2.20", True),  # a project with no lower bound filters nothing
            ("~=2.16", ">=2.17", True),
            ("==2.18.*", ">=2.17", False),
        ],
    )
    def test_admits_lower_bound(self, collection: str, project: str, expected: bool) -> None:
        assert admits_lower_bound(SpecifierSet(collection), SpecifierSet(project)) is expected

    @pytest.mark.parametrize(
        ("collection", "project", "expected"),
        [
            (">=2.15", ">=2.17", False),
            ("<2.20", ">=2.17", True),
            ("<2.20", ">=2.17,<2.20", False),
            ("<2.20", ">=2.17,<=2.20", True),
            ("<=2.20", ">=2.17,<=2.20", False),
            ("<2.19", ">=2.17,<2.20", True),
            ("~=2.15", ">=2.17", True),
        ],
    )
    def test_narrower_than(self, collection: str, project: str, expected: bool) -> None:
        assert narrower_than(SpecifierSet(collection), SpecifierSet(project)) is expected


class TestOverrides:
    def test_replaces_a_dependency_requirement(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", {"b.b": "<2.0.0"})], "b.b": [v("1.0.0"), v("2.0.0")]})
        toml = PROJECT + '[project.overrides]\n"b.b" = ">=2"\n'

        assert chosen(resolve(manifest(toml), indexes.clients)) == {"a.a": "1.0.0", "b.b": "2.0.0"}

    def test_replaces_the_project_requirement(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]})
        toml = '[project]\ncollections = ["a.a<2"]\n[project.overrides]\n"a.a" = "*"\n'

        assert chosen(resolve(manifest(toml), indexes.clients)) == {"a.a": "2.0.0"}

    def test_named_in_conflicts(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", {"b.b": "<2.0.0"})], "b.b": [v("1.0.0")]})
        toml = PROJECT + '[project.overrides]\n"b.b" = ">=2"\n'

        with pytest.raises(ResolutionError) as e:
            _ = resolve(manifest(toml), indexes.clients)

        assert "  b.b>=2 ([project.overrides], replacing b.b<2.0.0) (from a.a 1.0.0)" in str(e.value)


class TestIndexes:
    def test_first_index_that_has_it(self, indexes: Indexes) -> None:
        indexes.add("first", {"x.x": [v("1.0.0")]}).add("second", {"a.a": [v("1.0.0")], "x.x": [v("9.0.0")]})
        toml = '[project]\ncollections = ["a.a", "x.x"]\n'

        result = resolve(manifest(toml), indexes.clients)

        assert {c.name: (c.version.text, c.source.url) for c in result.collections} == {
            "a.a": ("1.0.0", indexes.urls["second"]),
            "x.x": ("1.0.0", indexes.urls["first"]),
        }

    def test_dependency_from_the_first_index_that_has_it(self, indexes: Indexes) -> None:
        # As uv's first-index: where a dependency comes from doesn't depend on its parent's index.
        indexes.add("first", {"b.b": [v("9.0.0")]}).add(
            "second", {"a.a": [v("1.0.0", {"b.b": "*"})], "b.b": [v("1.0.0")]}
        )

        result = resolve(manifest(PROJECT), indexes.clients)

        assert {c.name: c.source.url for c in result.collections}["b.b"] == indexes.urls["first"]
        assert chosen(result)["b.b"] == "9.0.0"

    def test_source_pins_a_root(self, indexes: Indexes) -> None:
        indexes.add("first", {"a.a": [v("1.0.0")]}).add("second", {"a.a": [v("2.0.0")]})
        toml = PROJECT + '[project.sources]\n"a.a" = { index = "second" }\n'

        assert chosen(resolve(manifest(toml), indexes.clients)) == {"a.a": "2.0.0"}

    def test_source_pins_a_dependency(self, indexes: Indexes) -> None:
        indexes.add("first", {"a.a": [v("1.0.0", {"b.b": "*"})], "b.b": [v("1.0.0")]}).add(
            "second", {"b.b": [v("2.0.0")]}
        )
        toml = PROJECT + '[project.sources]\n"b.b" = { index = "second" }\n'

        result = resolve(manifest(toml), indexes.clients)

        assert {c.name: (c.version.text, c.source.url) for c in result.collections}["b.b"] == (
            "2.0.0",
            indexes.urls["second"],
        )

    def test_unknown_source_index(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0")]})
        toml = PROJECT + '[project.sources]\n"a.a" = { index = "alia" }\n'

        with pytest.raises(
            ResolutionError, match=r"takes a\.a from index 'alia', which isn't configured; did you mean 'alai'\?"
        ):
            _ = resolve(manifest(toml), indexes.clients)

    def test_one_index_per_name(self, indexes: Indexes) -> None:
        # The project and a.a, from different indexes, both ask for b.b: it comes from one place, the first index.
        indexes.add("first", {"b.b": [v("1.0.0")]}).add(
            "second", {"a.a": [v("1.0.0", {"b.b": "*"})], "b.b": [v("2.0.0")]}
        )
        toml = '[project]\ncollections = ["a.a", "b.b"]\n'

        result = resolve(manifest(toml), indexes.clients)

        assert {c.name: (c.version.text, c.source.url) for c in result.collections}["b.b"] == (
            "1.0.0",
            indexes.urls["first"],
        )

    def test_first_index_versions_only(self, indexes: Indexes) -> None:
        # Only the first index that has a collection counts, even if a later one has a version that would fit:
        # that's what keeps a public index from shadowing a private name (dependency confusion).
        indexes.add("first", {"a.a": [v("1.0.0")]}).add("second", {"a.a": [v("2.0.0")]})

        with pytest.raises(ResolutionError, match=r"no version of a\.a meets"):
            _ = resolve(manifest('[project]\ncollections = ["a.a>=2"]\n'), indexes.clients)


def lock_of(indexes: Indexes, versions: dict[str, str], index: str = "alai") -> Lock:
    raw = raw_manifest({}, "alai.toml")
    return Lock(
        raw,
        [
            LockedCollection(
                name, Version(vers), source=IndexSource(indexes.urls[index]), cksum=cksum(name, vers), size=1
            )
            for name, vers in versions.items()
        ],
    )


def test_index_given_twice(indexes: Indexes) -> None:
    indexes.add("alai", {"a.a": [v("1.0.0")]})

    with pytest.raises(ValueError, match="index 'alai' is given more than once"):
        _ = resolve(manifest(PROJECT), [*indexes.clients, *indexes.clients])


class TestPreferences:
    def test_locked_version_kept(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}))

        assert chosen(result) == {"a.a": "1.0.0"}

    def test_upgrade_everything(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}), upgrade=True)

        assert chosen(result) == {"a.a": "2.0.0"}

    def test_upgrade_one(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")], "b.b": [v("1.0.0"), v("2.0.0")]})
        toml = '[project]\ncollections = ["a.a", "b.b"]\n'
        locked = lock_of(indexes, {"a.a": "1.0.0", "b.b": "1.0.0"})

        result = resolve(manifest(toml), indexes.clients, locked=locked, upgrade={"b.b"})

        assert chosen(result) == {"a.a": "1.0.0", "b.b": "2.0.0"}

    def test_locked_version_that_no_longer_fits(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0"), v("3.0.0")]})
        toml = '[project]\ncollections = ["a.a>=2"]\n'

        result = resolve(manifest(toml), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}))

        assert chosen(result) == {"a.a": "3.0.0"}

    def test_locked_from_another_index_ignored(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]}).add("old", {"a.a": [v("1.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}, index="old"))

        assert chosen(result) == {"a.a": "2.0.0"}


class TestReasons:
    def test_kept_has_none(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}))

        assert result.reasons == {}

    def test_added(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0", {"b.b": "*"})], "b.b": [v("1.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {}))

        assert result.reasons == {"a.a": "a.a is needed by the project", "b.b": "b.b is needed by a.a 1.0.0"}

    def test_removed(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0")], "c.c": [v("1.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0", "c.c": "1.0.0"}))

        assert result.reasons == {"c.c": "nothing needs c.c any more"}

    def test_requirement_not_met(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]})
        toml = '[project]\ncollections = ["a.a>=2"]\n'

        result = resolve(manifest(toml), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}))

        assert result.reasons == {"a.a": "a.a 1.0.0 doesn't meet a.a>=2 (from the project)"}

    @pytest.mark.parametrize("upgrade", [True, {"a.a"}])
    def test_upgrade(self, indexes: Indexes, upgrade: bool | set[str]) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}), upgrade=upgrade)

        assert result.reasons == {"a.a": "upgrading a.a was asked for"}

    def test_index_changed(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0")]}).add("old", {"a.a": [v("1.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0"}, index="old"))

        assert result.reasons == {"a.a": "a.a now comes from index 'alai'"}

    def test_no_longer_on_the_index(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0")]})

        result = resolve(manifest(PROJECT), indexes.clients, locked=lock_of(indexes, {"a.a": "1.5.0"}))

        assert result.reasons == {"a.a": "a.a 1.5.0 is no longer on index 'alai'"}

    def test_left_out(self, indexes: Indexes) -> None:
        indexes.add("alai", {"a.a": [v("1.0.0"), v("2.0.0", ra=">=2.19")]})
        toml = '[project]\nrequires_ansible = ">=2.17"\ncollections = ["a.a"]\n'

        result = resolve(manifest(toml), indexes.clients, locked=lock_of(indexes, {"a.a": "2.0.0"}))

        assert result.reasons == {
            "a.a": "a.a 2.0.0 is left out (needing a newer ansible-core than the project's >=2.17)"
        }

    def test_dependency_conflict(self, indexes: Indexes) -> None:
        # A bundle pinning everything exactly: pinning one of its collections lower moves the bundle back, and with it
        # every other collection it pins. The bundle's reason is the pin, not c.c, which only followed it.
        indexes.add(
            "alai",
            {
                "b.b": [v("1.0.0", {"c.c": "1.0.0", "w.w": "1.0.0"}), v("2.0.0", {"c.c": "2.0.0", "w.w": "2.0.0"})],
                "c.c": [v("1.0.0"), v("2.0.0")],
                "w.w": [v("1.0.0"), v("2.0.0")],
            },
        )
        toml = '[project]\ncollections = ["b.b", "w.w==1.0.0"]\n'
        locked = lock_of(indexes, {"b.b": "2.0.0", "c.c": "2.0.0", "w.w": "2.0.0"})

        result = resolve(manifest(toml), indexes.clients, locked=locked)

        assert result.reasons == {
            "b.b": "b.b 2.0.0 needs w.w==2.0.0, which conflicts with w.w==1.0.0 (from the project)",
            "c.c": "c.c 2.0.0 doesn't meet c.c==1.0.0 (from b.b 1.0.0)",
            "w.w": "w.w 2.0.0 doesn't meet w.w==1.0.0 (from the project), w.w==1.0.0 (from b.b 1.0.0)",
        }

    def test_dependency_override_applies(self, indexes: Indexes) -> None:
        indexes.add(
            "alai",
            {"a.a": [v("1.0.0", {"b.b": "<2"}), v("2.0.0", {"b.b": "<2"})], "b.b": [v("1.0.0"), v("2.0.0")]},
        )
        toml = '[project]\ncollections = ["a.a", "b.b>=2"]\n[project.overrides]\n"b.b" = ">=1"\n'

        result = resolve(manifest(toml), indexes.clients, locked=lock_of(indexes, {"a.a": "1.0.0", "b.b": "2.0.0"}))

        # The override replaces a.a's <2, so a.a 1.0.0 is kept.
        assert "a.a" not in result.reasons


def test_prefetches_every_named_dependency(tmp_path: Path) -> None:
    # Dependencies of every version in a file are fetched as soon as it arrives, not only of the one chosen.
    make_index(
        tmp_path / "site",
        {
            "a.a": [v("1.0.0", {"old.dep": "*"}), v("2.0.0", {"b.b": "*"})],
            "b.b": [v("1.0.0")],
            "old.dep": [v("1.0.0")],
        },
    )
    with Fetcher(user_agent="t") as fetcher:
        for server in serve(tmp_path / "site"):
            client = IndexClient(Index("alai", server.url), fetcher, str(tmp_path / "cache"))

            result = resolve(manifest(PROJECT), [client])

            assert chosen(result) == {"a.a": "2.0.0", "b.b": "1.0.0"}
            assert "/index/old/dep.jsonl" in server.paths()
