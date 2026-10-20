# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest
from test_sync_command import installed, lines

from alai._cli import EXIT_ERROR, main

# The index (from test_sync_command): a.a 1.0.0 and 2.0.0 (which needs b.b>=1.0.0), b.b 1.0.0 and 1.1.0, c.c and d.d.

TOML = """\
[project]
# What the project needs.
collections = [
    "c.c",  # why
]
"""


def write(project: Path, text: str = TOML) -> None:
    _ = (project / "alai.toml").write_text(text)


def toml(project: Path) -> str:
    return (project / "alai.toml").read_text()


class TestAdd:
    def test_writes_the_locked_lower_bound(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project)

        assert main(["add", "a.a"]) == 0

        assert toml(project) == TOML.replace('    "c.c",  # why\n', '    "a.a>=2.0.0",\n    "c.c",  # why\n')
        assert installed(project) == {"a.a": "2.0.0", "b.b": "1.1.0", "c.c": "1.0.0"}
        assert 'name = "a.a"' in (project / "alai.lock").read_text()
        assert lines(capsys.readouterr().err)[0] == "Resolved 3 collections"

    def test_requirement_written_as_given(self, project: Path) -> None:
        write(project)

        assert main(["add", "b.b < 1.1"]) == 0

        assert '    "b.b<1.1",\n' in toml(project)
        assert installed(project)["b.b"] == "1.0.0"

    def test_already_there(self, project: Path) -> None:
        write(project)
        _ = main(["sync"])

        assert main(["add", "c.c"]) == 0

        assert toml(project) == TOML

    def test_replaces_a_requirement(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["b.b>=1"]  # pinned\n')

        assert main(["add", "b.b<1.1"]) == 0

        assert toml(project) == '[project]\ncollections = ["b.b<1.1"]  # pinned\n'

    def test_appended_when_unsorted(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["d.d", "c.c"]\n')

        assert main(["add", "a.a"]) == 0

        assert toml(project) == '[project]\ncollections = ["d.d", "c.c", "a.a>=2.0.0"]\n'

    def test_group(self, project: Path) -> None:
        write(project)

        assert main(["add", "--group", "docs", "d.d"]) == 0

        assert toml(project) == TOML + '\n[project.groups.docs]\ncollections = ["d.d>=1.0.0"]\n'
        # The group added to is installed, though it isn't a default one.
        assert set(installed(project)) == {"c.c", "d.d"}

    def test_dev(self, project: Path) -> None:
        write(project)

        assert main(["add", "--dev", "d.d"]) == 0

        assert "[project.groups.dev]" in toml(project)

    def test_no_sync(self, project: Path) -> None:
        write(project)

        assert main(["add", "--no-sync", "d.d"]) == 0

        assert 'name = "d.d"' in (project / "alai.lock").read_text()
        assert not (project / "collections").exists()

    def test_frozen(self, project: Path) -> None:
        write(project)

        assert main(["add", "--frozen", "d.d"]) == 0

        assert '    "d.d",\n' in toml(project)
        assert not (project / "alai.lock").exists()

    def test_failure_writes_nothing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project)

        assert main(["add", "a.a>=3"]) == EXIT_ERROR

        assert "no version of a.a meets every requirement" in capsys.readouterr().err
        assert toml(project) == TOML
        assert not (project / "alai.lock").exists()

    def test_keeps_crlf(self, project: Path) -> None:
        text = TOML.replace("\n", "\r\n")
        _ = (project / "alai.toml").write_bytes(text.encode())

        assert main(["add", "d.d"]) == 0

        assert (project / "alai.toml").read_bytes() == text.replace(
            '    "c.c",  # why\r\n', '    "c.c",  # why\r\n    "d.d>=1.0.0",\r\n'
        ).encode()

    def test_keeps_the_file_mode(self, project: Path) -> None:
        write(project)
        (project / "alai.toml").chmod(0o640)

        assert main(["add", "--frozen", "d.d"]) == 0

        assert (project / "alai.toml").stat().st_mode & 0o777 == 0o640


class TestRemove:
    def test_removes_and_uninstalls(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = [\n    "c.c",  # why\n    "d.d",\n]\n')
        _ = main(["sync"])
        _ = capsys.readouterr()

        assert main(["remove", "c.c"]) == 0

        assert toml(project) == '[project]\ncollections = [\n    # why\n    "d.d",\n]\n'
        assert set(installed(project)) == {"d.d"}
        assert " - c.c==1.0.0" in capsys.readouterr().err

    def test_several(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["c.c", "d.d", "a.a"]\n')

        assert main(["remove", "c.c", "a.a"]) == 0

        assert toml(project) == '[project]\ncollections = ["d.d"]\n'

    def test_from_a_group(self, project: Path) -> None:
        write(project, TOML + '\n[project.groups.dev]\ncollections = ["d.d"]\n')

        assert main(["remove", "--dev", "d.d"]) == 0

        assert toml(project) == TOML + "\n[project.groups.dev]\ncollections = []\n"

    def test_not_there(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, TOML + '\n[project.groups.test]\ncollections = ["d.d"]\n')

        assert main(["remove", "d.d"]) == EXIT_ERROR

        assert capsys.readouterr().err == (
            "error: d.d isn't in project.collections; it's in group 'test' (try: alai remove d.d --group test)\n"
        )

    def test_not_in_the_group(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project)

        assert main(["remove", "--group", "test", "c.c"]) == EXIT_ERROR

        assert "it's in project.collections (try: alai remove c.c)" in capsys.readouterr().err

    def test_frozen(self, project: Path) -> None:
        write(project)

        assert main(["remove", "--frozen", "c.c"]) == 0

        assert toml(project) == "[project]\n# What the project needs.\ncollections = [\n    # why\n]\n"
        assert not (project / "alai.lock").exists()


class TestUpgrade:
    def test_add_upgrades_another(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["b.b<1.1"]\n')
        _ = main(["sync"])
        write(project, '[project]\ncollections = ["b.b"]\n')

        assert main(["add", "-P", "b.b", "c.c"]) == 0

        assert installed(project) == {"b.b": "1.1.0", "c.c": "1.0.0"}

    def test_remove_upgrades(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["b.b<1.1", "c.c"]\n')
        _ = main(["sync"])
        write(project, '[project]\ncollections = ["b.b", "c.c"]\n')

        assert main(["remove", "-U", "c.c"]) == 0

        assert installed(project) == {"b.b": "1.1.0"}

    @pytest.mark.parametrize("argv", [["add", "--frozen", "-U", "c.c"], ["remove", "--frozen", "-P", "c.c", "c.c"]])
    def test_frozen_refuses(self, project: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
        write(project)

        assert main(argv) == EXIT_ERROR

        assert "--frozen doesn't" in capsys.readouterr().err
        assert toml(project) == TOML


class TestErrors:
    def test_no_project_file(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["add", "c.c"]) == EXIT_ERROR

        assert "there's no alai.toml in" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (["add", "nope"], "invalid collection name 'nope', expected namespace.name"),
            (["add", "--group", "Bad", "c.c"], "--group: invalid group name 'Bad'"),
            (["remove", "c.c>=1"], "invalid collection name 'c.c>=1'"),
        ],
    )
    def test_invalid(self, project: Path, capsys: pytest.CaptureFixture[str], argv: list[str], message: str) -> None:
        write(project)

        assert main(argv) == EXIT_ERROR

        assert message in capsys.readouterr().err
        assert toml(project) == TOML

    def test_edit_refused(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, 'project = { collections = ["c.c"] }\n')

        assert main(["add", "d.d"]) == EXIT_ERROR

        assert "add the collections in project.collections by hand" in capsys.readouterr().err

    @pytest.mark.parametrize("argv", [["add", "--group", "x", "--dev", "c.c"], ["add", "--no-sync", "--frozen", "c.c"]])
    def test_exclusive(self, project: Path, argv: list[str]) -> None:
        with pytest.raises(SystemExit) as e:
            _ = main(argv)
        assert e.value.code == 2
