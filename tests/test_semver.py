# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import pytest

from alai._semver import Version, VersionReq


class TestVersion:
    @pytest.mark.parametrize("text", ["1.0", "v1.0.0", "1.0.0.0", "01.0.0", "1.0.0-", "1.0.0-01", "1.0.0+", ""])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ValueError, match="expected a semantic version"):
            Version(text)

    def test_precedence(self) -> None:
        # The order in the SemVer 2.0.0 spec, item 11.
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
            "1.0.1",
            "1.1.0",
            "2.0.0",
            "10.0.0",
        ]

        assert sorted(Version(v) for v in reversed(ordered)) == [Version(v) for v in ordered]

    def test_build_metadata_sorts_by_text(self) -> None:
        versions = [Version("1.0.0+b"), Version("1.0.0"), Version("1.0.0+a")]

        assert [v.text for v in sorted(versions)] == ["1.0.0", "1.0.0+a", "1.0.0+b"]
        assert Version("1.0.0+a") != Version("1.0.0+b")

    def test_prerelease(self) -> None:
        assert Version("1.0.0-rc.1").is_prerelease
        assert not Version("1.0.0+build").is_prerelease

    def test_hashable(self) -> None:
        assert {Version("1.0.0"), Version("1.0.0")} == {Version("1.0.0")}

    def test_comparisons(self) -> None:
        low, high = Version("1.0.0"), Version("1.0.1")

        assert low <= low < high <= high
        assert high >= high > low >= low
        assert str(low) == "1.0.0"
        assert repr(low) == "Version('1.0.0')"


class TestParse:
    @pytest.mark.parametrize(
        ("text", "clauses"),
        [
            ("*", ()),
            (" * ", ()),
            (">=9", ((">=", "9.0.0"),)),
            (">=9,<11", ((">=", "9.0.0"), ("<", "11.0.0"))),
            (" >= 9.1 , < 11 ", ((">=", "9.1.0"), ("<", "11.0.0"))),
            ("==1.2.3", (("==", "1.2.3"),)),
            ("!=1.2.3-rc.1", (("!=", "1.2.3-rc.1"),)),
            (">2.0-beta", ((">", "2.0.0-beta"),)),
            ("<=0.1", (("<=", "0.1.0"),)),
        ],
    )
    def test_valid(self, text: str, clauses: tuple[tuple[str, str], ...]) -> None:
        req = VersionReq.parse(text)

        assert [(op, v.text) for op, v in req.clauses] == list(clauses)
        assert str(req) == text

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("", "empty clause"),
            (">=1,", "empty clause"),
            ("*,>=1", "'\\*' can't be combined"),
            ("^1.0.0", "'\\^' ranges aren't supported"),
            ("~1.0", "'~' ranges aren't supported"),
            ("=1.0.0", "use '==' rather than '='"),
            ("1.0.0", "'1.0.0' needs an operator, such as '==1.0.0'"),
            ("==1.0.0+build", "has build metadata"),
            ("=>1.0", "use '>=' rather than '=>'"),
            (">=1.2.3.4", "expected an operator"),
            (">=v1", "expected an operator"),
            (">=01", "expected an operator"),
        ],
    )
    def test_invalid(self, text: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            VersionReq.parse(text)


class TestParseIndex:
    """ansible-galaxy's syntax, as meets_requirements reads it."""

    @pytest.mark.parametrize(
        ("text", "clauses"),
        [
            ("*", ()),
            (">=1.0.0", ((">=", "1.0.0"),)),
            ("1.0", (("==", "1.0.0"),)),
            ("=1.0.0", (("==", "1.0.0"),)),
            (">1.0,<2", ((">", "1.0.0"), ("<", "2.0.0"))),
            ("*,>=2", ((">=", "2.0.0"),)),
            # LooseVersion cuts the core to three parts and keeps everything from the first "-" or "+".
            ("1.2.3.4", (("==", "1.2.3"),)),
            ("1.0-rc1", (("==", "1.0.0-rc1"),)),
            ("1-rc.1", (("==", "1.0.0-rc.1"),)),
            ("1.0.0-RC1", (("==", "1.0.0-RC1"),)),
            ("01.2", (("==", "1.2.0"),)),
            (">=1.0.0-beta+build", ((">=", "1.0.0-beta+build"),)),
        ],
    )
    def test_valid(self, text: str, clauses: tuple[tuple[str, str], ...]) -> None:
        assert [(op, v.text) for op, v in VersionReq.parse_index(text).clauses] == list(clauses)

    # Each of these fails in ansible-galaxy too.
    @pytest.mark.parametrize("text", ["^3.0.0", "=>1.0", " >=1.0", ">= 1.0", ">=1.0.0,", "", "v1.0", "1.0-RC1"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ValueError, match="invalid version requirement"):
            VersionReq.parse_index(text)


class TestContains:
    @pytest.mark.parametrize(
        ("req", "version", "expected"),
        [
            ("*", "0.0.1-alpha", True),
            (">=1.0.0", "1.0.0", True),
            (">=1.0.0", "1.0.0-rc.1", False),
            (">=1.0.0", "2.0.0-rc.1", True),
            ("<2.0.0", "2.0.0-rc.1", True),
            (">1.0.0,<2.0.0", "1.5.0", True),
            (">1.0.0,<2.0.0", "2.0.0", False),
            ("!=1.2.3", "1.2.3", False),
            ("!=1.2.3", "1.2.4", True),
            # Build metadata is ignored when comparing, so == matches any build of the version.
            ("==1.0.0", "1.0.0+build", True),
            ("1.0.0-beta.11", "1.0.0-beta.2", False),
            (">1.0.0-beta.2", "1.0.0-beta.11", True),
        ],
    )
    def test_contains(self, req: str, version: str, expected: bool) -> None:
        assert VersionReq.parse_index(req).contains(Version(version)) is expected

    def test_equality_by_clauses(self) -> None:
        assert VersionReq.parse(">= 1") == VersionReq.parse_index(">=1.0.0")
        assert VersionReq.parse("*") == VersionReq.parse_index("*,*")
        assert len({VersionReq.parse(">=1"), VersionReq.parse(">=1.0.0")}) == 1
        assert repr(VersionReq.parse(">=1")) == "VersionReq('>=1')"
