# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import random
import sys

import pytest

from alai._toml_edit import TomlEditError, create, insert, remove, remove_key, replace, set_table, values

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

P = ("project", "collections")


class TestInsert:
    @pytest.mark.parametrize(
        ("text", "index", "expected"),
        [
            # Multi-line: a new line, indented as the last item, with the comma style kept.
            ('collections = [\n    "a.a",\n]\n', 1, 'collections = [\n    "a.a",\n    "b.b",\n]\n'),
            ('collections = [\n    "a.a"\n]\n', 1, 'collections = [\n    "a.a",\n    "b.b",\n]\n'),
            ('collections = [\n  "a.a",  # why\n]\n', 1, 'collections = [\n  "a.a",  # why\n  "b.b",\n]\n'),
            (
                'collections = [\n    "a.a",\n    "c.c",\n]\n',
                1,
                'collections = [\n    "a.a",\n    "b.b",\n    "c.c",\n]\n',
            ),
            ('collections = [\n    "a.a",\n]\n', 0, 'collections = [\n    "b.b",\n    "a.a",\n]\n'),
            # The closing bracket on the last item's line: added inline.
            ('collections = [\n    "a.a"]\n', 1, 'collections = [\n    "a.a", "b.b"]\n'),
            # One line.
            ('collections = ["a.a"]\n', 1, 'collections = ["a.a", "b.b"]\n'),
            ('collections = ["a.a",]\n', 1, 'collections = ["a.a", "b.b",]\n'),
            ('collections = ["a.a", "c.c"]\n', 1, 'collections = ["a.a", "b.b", "c.c"]\n'),
            ('collections = ["c.c"]\n', 0, 'collections = ["b.b", "c.c"]\n'),
            # Empty.
            ("collections = []\n", 0, 'collections = ["b.b"]\n'),
            ("collections = [\n]\n", 0, 'collections = [\n    "b.b",\n]\n'),
        ],
    )
    def test_layouts(self, text: str, index: int, expected: str) -> None:
        text = "[project]\n" + text

        assert insert(text, P, index, "b.b") == "[project]\n" + expected

    def test_quotes(self) -> None:
        text = "[project]\ncollections = []\n"

        edited = insert(text, P, 0, 'a.a "odd" \\ value')

        assert values(edited, P) == ['a.a "odd" \\ value']


class TestRemove:
    @pytest.mark.parametrize(
        ("text", "index", "expected"),
        [
            ('collections = [\n    "a.a",\n    "b.b",\n]\n', 0, 'collections = [\n    "b.b",\n]\n'),
            ('collections = [\n    "a.a",\n    "b.b",\n]\n', 1, 'collections = [\n    "a.a",\n]\n'),
            # A comment on the removed line stays, on its own, as uv keeps it.
            ('collections = [\n    "a.a",  # why\n    "b.b",\n]\n', 0, 'collections = [\n    # why\n    "b.b",\n]\n'),
            ('collections = ["a.a", "b.b", "c.c"]\n', 1, 'collections = ["a.a", "c.c"]\n'),
            ('collections = ["a.a", "b.b"]\n', 0, 'collections = ["b.b"]\n'),
            ('collections = ["a.a", "b.b"]\n', 1, 'collections = ["a.a"]\n'),
            ('collections = ["a.a", "b.b",]\n', 1, 'collections = ["a.a",]\n'),
            ('collections = ["a.a"]\n', 0, "collections = []\n"),
            ('collections = [\n    "a.a"]\n', 0, "collections = [\n    ]\n"),
        ],
    )
    def test_layouts(self, text: str, index: int, expected: str) -> None:
        text = "[project]\n" + text

        assert remove(text, P, index) == "[project]\n" + expected


def test_replace_keeps_the_rest_of_the_line() -> None:
    text = "[project]\ncollections = [\n    'a.a>=1',  # pinned\n]\n"

    assert replace(text, P, 0, "a.a>=2") == '[project]\ncollections = [\n    "a.a>=2",  # pinned\n]\n'


class TestCreate:
    def test_under_its_header(self) -> None:
        text = '[project]\nrequires_ansible = ">=2.17"  # note\n\n[other]\nx = 1\n'

        assert create(text, P, ["a.a"]) == (
            '[project]\nrequires_ansible = ">=2.17"  # note\ncollections = ["a.a"]\n\n[other]\nx = 1\n'
        )

    def test_header_with_no_keys(self) -> None:
        assert create("[project]\n\n[other]\n", P, ["a.a"]) == '[project]\ncollections = ["a.a"]\n\n[other]\n'

    def test_new_table_at_the_end(self) -> None:
        text = '[project]\ncollections = ["a.a"]\n'

        edited = create(text, ("project", "groups", "dev", "collections"), ["b.b"])

        assert edited == text + '\n[project.groups.dev]\ncollections = ["b.b"]\n'

    def test_no_final_newline(self) -> None:
        edited = create('[project]\ncollections = ["a.a"]', ("project", "groups", "dev", "collections"), ["b.b"])

        assert edited == '[project]\ncollections = ["a.a"]\n\n[project.groups.dev]\ncollections = ["b.b"]\n'

    def test_quoted_group_name(self) -> None:
        edited = create("[project]\n", ("project", "groups", "my.group", "collections"), ["b.b"])

        assert '[project.groups."my.group"]' in edited


S = ("project", "sources", "ns.col")


class TestSetTable:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # A new table at the end, after a blank line.
            (
                "[project]\ncollections = []\n",
                '[project]\ncollections = []\n\n[project.sources]\n"ns.col" = { path = "p" }\n',
            ),
            ("", '[project.sources]\n"ns.col" = { path = "p" }\n'),
            # After the table's last key, before what follows.
            (
                '[project.sources]\n"a.b" = { index = "x" }  # pin\n\n[tool.x]\n',
                '[project.sources]\n"a.b" = { index = "x" }  # pin\n"ns.col" = { path = "p" }\n\n[tool.x]\n',
            ),
            # In place, keeping a comment after it.
            (
                '[project.sources]\n"ns.col" = { index = "x" }  # why\n',
                '[project.sources]\n"ns.col" = { path = "p" }  # why\n',
            ),
            (
                "[project]\r\ncollections = []\r\n",
                '[project]\r\ncollections = []\r\n\r\n[project.sources]\r\n"ns.col" = { path = "p" }\r\n',
            ),
        ],
    )
    def test_set(self, text: str, expected: str) -> None:
        assert set_table(text, S, {"path": "p"}) == expected

    def test_boolean(self) -> None:
        assert set_table("", S, {"path": "p", "editable": True}).endswith('{ path = "p", editable = true }\n')

    def test_dotted_table_refused(self) -> None:
        with pytest.raises(TomlEditError, match="would leave the file unreadable"):
            _ = set_table('[project]\nsources."a.b" = { index = "x" }\n', S, {"path": "p"})


class TestRemoveKey:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                '[project.sources]\n"ns.col" = { path = "p" }\n"a.b" = { index = "x" }\n',
                '[project.sources]\n"a.b" = { index = "x" }\n',
            ),
            ('[project.sources]\n"ns.col" = { path = "p" }  # why\r\n', "[project.sources]\n"),
            ('[project.sources]\n"ns.col" = { path = "p" }', "[project.sources]\n"),
        ],
    )
    def test_removed(self, text: str, expected: str) -> None:
        assert remove_key(text, S) == expected

    def test_inline_refused(self) -> None:
        with pytest.raises(TomlEditError, match="isn't written as a key of its own"):
            _ = remove_key('[project]\nsources = { "ns.col" = { path = "p" } }\n', S)


class TestWhereTheArrayIs:
    def test_dotted_key_at_the_root(self) -> None:
        text = 'cache_dir = "x"\nproject.collections = ["a.a"]\n'

        assert insert(text, P, 1, "b.b") == 'cache_dir = "x"\nproject.collections = ["a.a", "b.b"]\n'

    def test_dotted_key_in_a_table(self) -> None:
        text = '[project]\ngroups.test.collections = ["a.a"]\n'
        path = ("project", "groups", "test", "collections")

        assert insert(text, path, 1, "b.b") == '[project]\ngroups.test.collections = ["a.a", "b.b"]\n'

    def test_quoted_keys(self) -> None:
        text = '["project"]\n"collections" = ["a.a"]\n'

        assert values(insert(text, P, 1, "b.b"), P) == ["a.a", "b.b"]

    def test_crlf_remove(self) -> None:
        text = '[project]\r\ncollections = [\r\n    "a.a",\r\n    "b.b",\r\n]\r\n'

        assert remove(text, P, 0) == '[project]\r\ncollections = [\r\n    "b.b",\r\n]\r\n'

    def test_crlf(self) -> None:
        text = '[project]\r\ncollections = [\r\n    "a.a",\r\n]\r\n'

        assert insert(text, P, 1, "b.b") == '[project]\r\ncollections = [\r\n    "a.a",\r\n    "b.b",\r\n]\r\n'

    def test_everything_else_untouched(self) -> None:
        # The things a re-serialising editor gets wrong: a sub-table of an array of tables written out of order
        # (tomlkit moves it), a datetime with a space, strings ending in quotes, odd spacing and comments.
        rest = (
            "[[fruit]]\nname = 'apple'\n\n[other]  # note\nwhen = 1979-05-27 07:32:00Z\n"
            's = """a "quoted"\nline"""""\nl = \'\'\'it\'s\'\'\'\'\'\nnested = [[1, 2], {a = "]"}]   # ]\n'
            "\n[fruit.texture]\nsmooth = true\n"
        )
        text = f'[project]\ncollections = ["a.a"]\n\n{rest}'

        edited = insert(text, P, 1, "b.b")

        assert edited == f'[project]\ncollections = ["a.a", "b.b"]\n\n{rest}'


class TestRefused:
    def test_inline_table(self) -> None:
        with pytest.raises(TomlEditError, match="isn't written as a key of its own"):
            _ = insert('project = { collections = ["a.a"] }\n', P, 1, "b.b")

    def test_not_strings(self) -> None:
        with pytest.raises(TomlEditError, match="isn't an array of strings"):
            _ = values("[project]\ncollections = [1]\n", P)

    def test_table_defined_by_dotted_keys(self) -> None:
        # A new [project.groups.test] header would define a table dotted keys already have: refused, not written.
        text = "[project]\ngroups.test.other = 1\n"

        with pytest.raises(TomlEditError, match="would leave the file unreadable"):
            _ = create(text, ("project", "groups", "test", "collections"), ["b.b"])

    def test_shared_line_is_removed_inline(self) -> None:
        text = '[project]\ncollections = [\n    "a.a", "b.b",\n    "c.c",\n]\n'

        # Not on a line of its own, but not the whole array's line either: removed inline.
        assert values(remove(text, P, 0), P) == ["b.b", "c.c"]


def test_missing() -> None:
    assert values("[project]\n", P) is None
    assert values("", ("project", "groups", "x", "collections")) is None


LAYOUTS = [
    '[project]\ncollections = ["a.a", "b.b"]\n',
    '[project]\ncollections = [\n    "a.a",\n    "b.b",\n]\n',
    '[project]\ncollections = [\n  "a.a",  # one\n  # between\n  "b.b"  # two\n]  # end\n',
    'project.collections = [ "a.a" , "b.b" , ]\n',
    "[project]\ncollections = [\n\t'a.a',\n\t'b.b'\n]\n[tool.x]\ny = [1, {z = 2}]\n",
    '[project]\r\ncollections = [\r\n    "a.a",  # one\r\n    "b.b",\r\n]\r\n',
]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_random_edits(layout: str) -> None:
    # Many edits in a row, each checked against the intended list: the text stays valid TOML with only the array
    # changed, whatever the layout.
    rng = random.Random(layout)  # noqa: S311  # repeatable test data, not secrets
    text = layout
    expected = values(text, P)
    assert expected is not None
    other = {k: v for k, v in tomllib.loads(text).items() if k != "project"}
    for step in range(60):
        choice = rng.random()
        if choice < 0.45 or not expected:
            index = rng.randint(0, len(expected))
            name = f"n{step}.c"
            text = insert(text, P, index, name)
            expected.insert(index, name)
        elif choice < 0.8:
            index = rng.randrange(len(expected))
            text = remove(text, P, index)
            del expected[index]
        else:
            index = rng.randrange(len(expected))
            text = replace(text, P, index, f"r{step}.c>=1")
            expected[index] = f"r{step}.c>=1"
        assert values(text, P) == expected
        if "\r\n" in layout:
            assert text.count("\n") == text.count("\r\n")
    assert {k: v for k, v in tomllib.loads(text).items() if k != "project"} == other
