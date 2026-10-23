# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import datetime as dt
import math
import random
import sys
import typing as _t
from pathlib import Path

import pytest

from alai._common import ConfigError, read_toml, read_toml_document
from alai._native import TomlDocument, TomlEditError, TomlError, toml_dumps, toml_loads, toml_read

P = ("project", "collections")


def edited(text: str, method: str, *args: object) -> str:
    doc = TomlDocument(text)
    getattr(doc, method)(*args)
    return str(doc)


class TestLoads:
    def test_types(self) -> None:
        text = (
            'a = "s"\nb = \'lit\'\nc = 0x1F\nd = 1_000\ne = 1.5\nf = inf\ng = true\nh = [1, [2], { x = "y" }]\n'
            + "[t]\nk.dotted = 1\n[[aot]]\nn = 1\n[[aot]]\nn = 2\n"
        )

        assert toml_loads(text) == {
            "a": "s",
            "b": "lit",
            "c": 31,
            "d": 1000,
            "e": 1.5,
            "f": math.inf,
            "g": True,
            "h": [1, [2], {"x": "y"}],
            "t": {"k": {"dotted": 1}},
            "aot": [{"n": 1}, {"n": 2}],
        }

    def test_datetimes(self) -> None:
        # As tomllib gives them: aware with an offset, fractional seconds cut to microseconds.
        loaded = toml_loads(
            "a = 1979-05-27T07:32:00.123456789+05:30\nb = 1979-05-27 07:32:00Z\nc = 1979-05-27T07:32:00\n"
            + "d = 1979-05-27\ne = 07:32:01.5\n"
        )

        tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
        assert loaded == {
            "a": dt.datetime(1979, 5, 27, 7, 32, 0, 123456, tzinfo=tz),
            "b": dt.datetime(1979, 5, 27, 7, 32, tzinfo=dt.timezone.utc),
            "c": dt.datetime(1979, 5, 27, 7, 32),  # noqa: DTZ001  # a local datetime has no offset
            "d": dt.date(1979, 5, 27),
            "e": dt.time(7, 32, 1, 500000),
        }

    def test_toml_1_1(self) -> None:
        # A multi-line inline table, a trailing comma in one, and a time without seconds, which only Python 3.15's
        # tomllib reads.
        loaded = toml_loads('[project.sources]\n"a.b" = {\n    git = "https://x",\n    rev = "v1",\n}\nt = 07:32\n')

        assert loaded == {"project": {"sources": {"a.b": {"git": "https://x", "rev": "v1"}, "t": dt.time(7, 32)}}}

    def test_byte_order_mark(self) -> None:
        assert toml_loads("﻿a = 1\n") == {"a": 1}

    @pytest.mark.parametrize(
        ("text", "where"),
        [
            ("a = ", "(at line 1, column 5)"),
            ("x = 1\ny = [1,\n  ", "(at line 2, column 8)"),
            ("[a]\n[a]\n", "(at line 2, column 2)"),
            ('é = "\\q"', None),
        ],
    )
    def test_errors(self, text: str, where: str | None) -> None:
        with pytest.raises(TomlError) as e:
            _ = toml_loads(text)

        assert "\n" not in str(e.value)
        if where is not None:
            assert str(e.value).endswith(where)

    def test_error_column_in_characters(self) -> None:
        with pytest.raises(TomlError, match=r"\(at line 1, column 9\)"):
            _ = toml_loads('a = "é" b')

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is Python 3.11 and later")
    def test_as_tomllib(self) -> None:
        import tomllib  # pyright: ignore[reportUnreachable]

        text = (
            '[project]\ncollections = ["a.a", \'b.b>=1\']  # c\n"quoted key" = """multi\nline"""\n'
            + '[tool.x]\nn = -0.0\nwhen = 2024-01-01T00:00:00-08:00\nnested = [[1, 2], {a = "]"}]\n'
            + "[[fruit]]\nname = 'apple'\n[fruit.texture]\nsmooth = true\n"
        )

        assert toml_loads(text) == tomllib.loads(text)


class TestRead:
    def test_file(self, tmp_path: Path) -> None:
        path = tmp_path / "a.toml"
        _ = path.write_bytes(b'a = "\xc3\xa9"\r\n')

        assert toml_read(path) == {"a": "é"}
        assert toml_read(str(path)) == {"a": "é"}

    def test_document_keeps_newlines(self, tmp_path: Path) -> None:
        path = tmp_path / "alai.toml"
        _ = path.write_bytes(b'[project]\r\ncollections = ["a.a"]\r\n')

        doc = TomlDocument.read(path)
        doc.insert(P, 1, "b.b")

        assert str(doc) == '[project]\r\ncollections = [\r\n    "a.a",\r\n    "b.b",\r\n]\r\n'

    def test_not_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "a.toml"
        _ = path.write_bytes(b'a = 1\nb = "\xc3\xa9\xff"\n')

        with pytest.raises(TomlError, match=r"^not UTF-8 \(at line 2, column 7\)$"):
            _ = toml_read(path)

    def test_os_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError) as e:
            _ = toml_read(tmp_path / "missing.toml")

        assert e.value.filename == tmp_path / "missing.toml"

    @pytest.mark.parametrize("read", [read_toml, read_toml_document])
    def test_errors_name_the_file(self, tmp_path: Path, read: _t.Callable[[str], object]) -> None:
        path = tmp_path / "alai.toml"
        _ = path.write_text("a = \n")

        with pytest.raises(ConfigError, match=rf"^{path}: invalid TOML: .* \(at line 1, column 5\)$"):
            _ = read(str(path))
        with pytest.raises(ConfigError, match=rf"^can't read {tmp_path}: Is a directory$"):
            _ = read(str(tmp_path))


class TestDumps:
    def test_layout(self) -> None:
        table = {
            "version": 1,
            "sources": {"a.b": {"index": "x", "editable": True}},
            "project": {"requirements": ["c.c"], "groups": {"test": {"requirements": []}}},
            "collection": [{"name": "x", "size": 3}, {"name": "y"}],
        }

        assert toml_dumps(table, "# head\n", 80) == (
            "# head\nversion = 1\n\n"
            + '[sources]\n"a.b" = { index = "x", editable = true }\n\n'
            + '[project]\nrequirements = ["c.c"]\ngroups.test.requirements = []\n\n'
            + '[[collection]]\nname = "x"\nsize = 3\n\n[[collection]]\nname = "y"\n'
        )

    def test_wide_array(self) -> None:
        assert toml_dumps({"t": {"a": ["x" * 10, "y" * 10]}}, "", 20) == (
            '[t]\na = [\n    "xxxxxxxxxx",\n    "yyyyyyyyyy",\n]\n'
        )

    def test_strings_read_back(self) -> None:
        values = ['q"uote', "back\\slash", "ctl\x01\x7f", "tab\tnew\nline", "é ☃"]
        table = {"t": {f"k{i}": v for i, v in enumerate(values)}}

        assert toml_loads(toml_dumps(table, "", 80)) == table

    def test_unsupported(self) -> None:
        with pytest.raises(ValueError, match="can't write a NoneType as TOML"):
            _ = toml_dumps({"a": None}, "", 80)


class TestInsert:
    # The layouts uv 0.13.5's `uv add --frozen` gave for the same arrays.
    @pytest.mark.parametrize(
        ("text", "index", "expected"),
        [
            # One line: one item per line, with a trailing comma.
            ('collections = ["a.a", "c.c"]\n', 1, 'collections = [\n    "a.a",\n    "b.b",\n    "c.c",\n]\n'),
            ('collections = ["c.c"]\n', 0, 'collections = [\n    "b.b",\n    "c.c",\n]\n'),
            ('collections = ["a.a",]\n', 1, 'collections = [\n    "a.a",\n    "b.b",\n]\n'),
            ("collections = []\n", 0, 'collections = [\n    "b.b",\n]\n'),
            ("collections = [\n]\n", 0, 'collections = [\n    "b.b",\n]\n'),
            # Multi-line: indented as the first item, comments kept on their lines.
            ('collections = [\n    "a.a"\n]\n', 1, 'collections = [\n    "a.a",\n    "b.b",\n]\n'),
            ('collections = [\n  "a.a",  # why\n]\n', 1, 'collections = [\n  "a.a",  # why\n  "b.b",\n]\n'),
            (
                'collections = [\n  "a.a",  # one\n  # networking\n  "r.r",  # two\n]  # end\n',
                1,
                'collections = [\n  "a.a",  # one\n  # networking\n  "b.b",\n  "r.r",  # two\n]  # end\n',
            ),
            # At the front: a comment on the bracket's line stays there, one on its own line moves down.
            ('collections = [ # top\n  "f.f",\n]\n', 0, 'collections = [ # top\n  "b.b",\n  "f.f",\n]\n'),
            ('collections = [\n  # c\n  "f.f",\n]\n', 0, 'collections = [\n  "b.b",\n  # c\n  "f.f",\n]\n'),
            ("collections = [\n\t'a.a',\n\t'f.f'\n]\n", 2, "collections = [\n\t'a.a',\n\t'f.f',\n\t\"b.b\",\n]\n"),
            ('collections = [\n    "a.a"]\n', 1, 'collections = [\n    "a.a",\n    "b.b",\n]\n'),
        ],
    )
    def test_layouts(self, text: str, index: int, expected: str) -> None:
        assert edited("[project]\n" + text, "insert", P, index, "b.b") == "[project]\n" + expected

    def test_quotes(self) -> None:
        doc = TomlDocument("[project]\ncollections = []\n")

        doc.insert(P, 0, 'a.a "odd" \\ value')

        assert doc.values(P) == ['a.a "odd" \\ value']
        assert TomlDocument(str(doc)).values(P) == ['a.a "odd" \\ value']


class TestRemove:
    # As uv 0.13.5's `uv remove --frozen` left them, but for the indent after removing the first item of a one-line
    # array, where uv indents by the space after the bracket.
    @pytest.mark.parametrize(
        ("text", "index", "expected"),
        [
            ('collections = [\n    "a.a",\n    "b.b",\n]\n', 0, 'collections = [\n    "b.b",\n]\n'),
            ('collections = [\n    "a.a",\n    "b.b",\n]\n', 1, 'collections = [\n    "a.a",\n]\n'),
            # A comment on the removed line stays, on its own.
            ('collections = [\n    "a.a",  # why\n    "b.b",\n]\n', 0, 'collections = [\n    # why\n    "b.b",\n]\n'),
            # One on the line before stays there.
            ('collections = [\n    "a.a",  # why\n    "b.b",\n]\n', 1, 'collections = [\n    "a.a",  # why\n]\n'),
            (
                'collections = [\n    # about a\n    "a.a",\n    "b.b",\n]\n',
                0,
                'collections = [\n    # about a\n    "b.b",\n]\n',
            ),
            ('collections = ["a.a", "b.b", "c.c"]\n', 1, 'collections = [\n    "a.a",\n    "c.c",\n]\n'),
            ('collections = ["a.a", "b.b"]\n', 0, 'collections = [\n    "b.b",\n]\n'),
            ('collections = ["a.a"]\n', 0, "collections = []\n"),
            ('collections = [\n    "a.a"]\n', 0, "collections = []\n"),
        ],
    )
    def test_layouts(self, text: str, index: int, expected: str) -> None:
        assert edited("[project]\n" + text, "remove", P, index) == "[project]\n" + expected


class TestReplace:
    def test_keeps_the_comment(self) -> None:
        text = "[project]\ncollections = [\n    'a.a>=1',  # pinned\n]\n"

        assert edited(text, "replace", P, 0, "a.a>=2") == '[project]\ncollections = [\n    "a.a>=2",  # pinned\n]\n'

    def test_one_line(self) -> None:
        text = '[project]\ncollections = ["a.a>=1", "b.b"]\n'

        assert edited(text, "replace", P, 0, "a.a>=2") == '[project]\ncollections = [\n    "a.a>=2",\n    "b.b",\n]\n'


class TestCreate:
    def test_in_its_table(self) -> None:
        text = '[project]\nrequires_ansible = ">=2.17"  # note\n\n[other]\nx = 1\n'

        assert edited(text, "create", P, ["a.a"]) == (
            '[project]\nrequires_ansible = ">=2.17"  # note\ncollections = [\n    "a.a",\n]\n\n[other]\nx = 1\n'
        )

    def test_new_table_after_its_parent(self) -> None:
        text = '[project]\ncollections = ["a.a"]\n\n[tool.x]\ny = 1\n'

        assert edited(text, "create", ("project", "groups", "dev", "collections"), ["b.b"]) == (
            '[project]\ncollections = ["a.a"]\n\n[project.groups.dev]\ncollections = [\n    "b.b",\n]\n\n'
            + "[tool.x]\ny = 1\n"
        )

    def test_no_project(self) -> None:
        assert edited('cache_dir = "x"', "create", P, ["b.b"]) == (
            'cache_dir = "x"\n\n[project]\ncollections = [\n    "b.b",\n]\n'
        )

    def test_quoted_group_name(self) -> None:
        assert '[project.groups."my.group"]' in edited(
            "[project]\n", "create", ("project", "groups", "my.group", "c"), []
        )

    def test_dotted_keys(self) -> None:
        # A table dotted keys define gains a dotted key, not a header, which would define it twice.
        text = "[project]\ngroups.test.other = 1\n"

        assert edited(text, "create", ("project", "groups", "test", "collections"), ["b.b"]) == (
            '[project]\ngroups.test.other = 1\ngroups.test.collections = [\n    "b.b",\n]\n'
        )

    def test_dotted_at_the_root(self) -> None:
        text = 'project.collections = ["a.a"]\n'

        assert edited(text, "create", ("project", "groups", "x", "collections"), []) == (
            'project.collections = ["a.a"]\nproject.groups.x.collections = []\n'
        )


S = ("project", "sources", "ns.col")


class TestSetTable:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # A new table, after the last of [project]'s.
            (
                "[project]\ncollections = []\n\n[project.groups.x]\ncollections = []\n\n[tool.x]\n",
                "[project]\ncollections = []\n\n[project.groups.x]\ncollections = []\n\n"
                + '[project.sources]\n"ns.col" = { path = "p" }\n\n[tool.x]\n',
            ),
            ("", '[project.sources]\n"ns.col" = { path = "p" }\n'),
            # After the table's last key, before what follows.
            (
                '[project.sources]\n"a.b" = { index = "x" }  # pin\n\n[tool.x]\n',
                '[project.sources]\n"a.b" = { index = "x" }  # pin\n"ns.col" = { path = "p" }\n\n[tool.x]\n',
            ),
            # In place, keeping a comment after it, where uv moves it to the end without one.
            (
                '[project.sources]\n"ns.col" = { index = "x" }  # why\n"z.z" = { path = "z" }\n',
                '[project.sources]\n"ns.col" = { path = "p" }  # why\n"z.z" = { path = "z" }\n',
            ),
            # Written as dotted keys, or inline.
            (
                '[project]\nsources."a.b" = { index = "x" }\n',
                '[project]\nsources."a.b" = { index = "x" }\nsources."ns.col" = { path = "p" }\n',
            ),
            (
                '[project]\nsources = { "a.b" = { index = "x" } }\n',
                '[project]\nsources = { "a.b" = { index = "x" }, "ns.col" = { path = "p" } }\n',
            ),
            # A TOML 1.1 entry spread over lines stays as it is.
            (
                '[project.sources]\n"a.b" = {\n    git = "https://x",\n    rev = "v1",\n}\n',
                '[project.sources]\n"a.b" = {\n    git = "https://x",\n    rev = "v1",\n}\n"ns.col" = { path = "p" }\n',
            ),
            (
                "[project]\r\ncollections = []\r\n",
                '[project]\r\ncollections = []\r\n\r\n[project.sources]\r\n"ns.col" = { path = "p" }\r\n',
            ),
        ],
    )
    def test_set(self, text: str, expected: str) -> None:
        assert edited(text, "set_table", S, {"path": "p"}) == expected

    def test_order_and_boolean(self) -> None:
        assert edited("", "set_table", S, {"path": "p", "editable": True}).endswith('{ path = "p", editable = true }\n')


class TestRemoveKey:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                '[project.sources]\n# about\n"ns.col" = { path = "p" }  # why\n"a.b" = { index = "x" }\n',
                '[project.sources]\n"a.b" = { index = "x" }\n',
            ),
            # The emptied table stays.
            ('[project.sources]\n"ns.col" = { path = "p" }  # why\n\n[tool.x]\n', "[project.sources]\n\n[tool.x]\n"),
            ('[project.sources]\n"ns.col" = { path = "p" }', "[project.sources]\n"),
            ('[project]\nsources = { "ns.col" = { path = "p" } }\n', "[project]\nsources = {}\n"),
        ],
    )
    def test_removed(self, text: str, expected: str) -> None:
        assert edited(text, "remove_key", S) == expected

    def test_missing(self) -> None:
        with pytest.raises(TomlEditError, match=r'there\'s no project.sources."ns.col"'):
            _ = edited("[project]\n", "remove_key", S)


class TestDocument:
    def test_untouched_until_edited(self) -> None:
        text = "[project]  # c\r\ncollections = [ 'a.a' ]\n[a . c]\nx=1"

        assert str(TomlDocument(text)) == text

    def test_crlf(self) -> None:
        text = '[project]\r\ncollections = [\r\n    "a.a",  # one\r\n]\r\n'

        assert edited(text, "insert", P, 1, "b.b") == (
            '[project]\r\ncollections = [\r\n    "a.a",  # one\r\n    "b.b",\r\n]\r\n'
        )

    def test_mixed_newlines_become_lf(self) -> None:
        assert edited('[project]\r\ncollections = ["a.a"]\n', "insert", P, 1, "b.b") == (
            '[project]\ncollections = [\n    "a.a",\n    "b.b",\n]\n'
        )

    def test_everything_else_untouched(self) -> None:
        # The things a re-serialising editor gets wrong: a sub-table of an array of tables written out of order
        # (tomlkit moves it), a three-level one (tomlkit can't read it), a datetime with a space, strings ending in
        # quotes, odd spacing, comments and a spaced header.
        rest = (
            "[[fruit]]\nname = 'apple'\n\n[other]  # note\nwhen = 1979-05-27 07:32:00Z\n"
            + 's = """a "quoted"\nline"""""\nl = \'\'\'it\'s\'\'\'\'\'\nnested = [[1, 2], {a = "]"}]   # ]\n'
            + "\n[fruit.texture]\nsmooth = true\n[[tool.x.item]]\n[tool.y]\n[tool.x.item.sub]\n[a . c]\nx=1\n"
        )

        assert edited(f'[project]\ncollections = ["a.a"]\n\n{rest}', "insert", P, 1, "b.b") == (
            f'[project]\ncollections = [\n    "a.a",\n    "b.b",\n]\n\n{rest}'
        )

    def test_inline_table(self) -> None:
        assert edited('project = { collections = ["a.a"] }\n', "insert", P, 1, "b.b") == (
            'project = { collections = [\n    "a.a",\n    "b.b",\n] }\n'
        )

    def test_data(self) -> None:
        doc = TomlDocument('[project]\ncollections = ["a.a"]\n')

        doc.insert(P, 1, "b.b")

        assert doc.data() == {"project": {"collections": ["a.a", "b.b"]}}

    def test_invalid(self) -> None:
        with pytest.raises(TomlError, match=r"\(at line 1, column 11\)"):
            _ = TomlDocument("[project] x\n")

    @pytest.mark.parametrize(
        ("text", "method", "args", "message"),
        [
            ("[project]\ncollections = [1]\n", "values", (P,), "project.collections isn't an array of strings"),
            ('[project]\ncollections = "x"\n', "insert", (P, 0, "a"), "project.collections isn't an array"),
            ("[project]\n", "insert", (P, 0, "a"), "there's no project.collections"),
            (
                "[project]\ngroups = 1\n",
                "create",
                (("project", "groups", "t", "c"), []),
                "project.groups isn't a table",
            ),
            ("[[project]]\n", "create", (P, []), "project isn't a table"),
        ],
    )
    def test_refused(self, text: str, method: str, args: tuple[object, ...], message: str) -> None:
        doc = TomlDocument(text)

        with pytest.raises(TomlEditError, match=f"^{message}$"):
            getattr(doc, method)(*args)

        assert str(doc) == text

    def test_missing(self) -> None:
        assert TomlDocument("[project]\n").values(P) is None
        assert TomlDocument("").values(("project", "groups", "x", "collections")) is None


LAYOUTS = [
    '[project]\ncollections = ["a.a", "b.b"]\n',
    '[project]\ncollections = [\n    "a.a",\n    "b.b",\n]\n',
    '[project]\ncollections = [\n  "a.a",  # one\n  # between\n  "b.b"  # two\n]  # end\n',
    'project.collections = [ "a.a" , "b.b" , ]\n',
    "[project]\ncollections = [\n\t'a.a',\n\t'b.b'\n]\n[tool.x]\ny = [1, {z = 2}]\n",
    '[project]\r\ncollections = [\r\n    "a.a",  # one\r\n    "b.b",\r\n]\r\n',
    'project = { collections = ["a.a", "b.b"] }\n',
]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_random_edits(layout: str) -> None:
    # Many edits in a row, each checked against the intended list: the text stays valid TOML with only the array
    # changed, whatever the layout, and every comment stays.
    rng = random.Random(layout)  # noqa: S311  # repeatable test data, not secrets
    doc = TomlDocument(layout)
    expected = doc.values(P)
    assert expected is not None
    other = {k: v for k, v in toml_loads(layout).items() if k != "project"}
    comments = layout.count("#")
    for step in range(60):
        choice = rng.random()
        if choice < 0.45 or not expected:
            index = rng.randint(0, len(expected))
            name = f"n{step}.c"
            doc.insert(P, index, name)
            expected.insert(index, name)
        elif choice < 0.8:
            index = rng.randrange(len(expected))
            doc.remove(P, index)
            del expected[index]
        else:
            index = rng.randrange(len(expected))
            doc.replace(P, index, f"r{step}.c>=1")
            expected[index] = f"r{step}.c>=1"
        text = str(doc)
        assert TomlDocument(text).values(P) == expected
        assert text.count("#") == comments
        if "\r\n" in layout:
            assert text.count("\n") == text.count("\r\n")
    assert {k: v for k, v in toml_loads(str(doc)).items() if k != "project"} == other
