# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import edit, edited, installed_versions, lines

from alai import _edit
from alai._cli import main
from alai._common import EXIT_ERROR, EXIT_STALE
from alai._native import TomlDocument, TomlEditError

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
        assert installed_versions(project / "collections") == {"a.a": "2.0.0", "b.b": "1.1.0", "c.c": "1.0.0"}
        assert 'name = "a.a"' in (project / "alai.lock").read_text()
        assert lines(capsys.readouterr().err)[0] == "Resolved 3 collections"

    def test_requirement_written_as_given(self, project: Path) -> None:
        write(project)

        assert main(["add", "b.b < 1.1"]) == 0

        assert '    "b.b<1.1",\n' in toml(project)
        assert installed_versions(project / "collections")["b.b"] == "1.0.0"

    def test_already_there(self, project: Path) -> None:
        write(project)
        _ = main(["sync"])

        assert main(["add", "c.c"]) == 0

        assert toml(project) == TOML

    def test_replaces_a_requirement(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["b.b>=1"]  # pinned\n')

        assert main(["add", "b.b<1.1"]) == 0

        # Laid out one per line, as uv lays out an array it changes, the comment kept after it.
        assert toml(project) == '[project]\ncollections = [\n    "b.b<1.1",\n]  # pinned\n'

    def test_appended_when_unsorted(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["d.d", "c.c"]\n')

        assert main(["add", "a.a"]) == 0

        assert toml(project) == '[project]\ncollections = [\n    "d.d",\n    "c.c",\n    "a.a>=2.0.0",\n]\n'

    def test_group(self, project: Path) -> None:
        write(project)

        assert main(["add", "--group", "docs", "d.d"]) == 0

        assert toml(project) == TOML + '\n[project.groups.docs]\ncollections = [\n    "d.d>=1.0.0",\n]\n'
        # The group added to is installed, though it isn't a default one.
        assert set(installed_versions(project / "collections")) == {"c.c", "d.d"}

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
        assert not (project / "collections").exists()

    def test_failure_writes_nothing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project)

        assert main(["add", "a.a>=3"]) == EXIT_ERROR

        assert "no solution found when resolving collections" in capsys.readouterr().err
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
        assert set(installed_versions(project / "collections")) == {"d.d"}
        assert " - c.c==1.0.0" in capsys.readouterr().err

    def test_several(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["c.c", "d.d", "a.a"]\n')

        assert main(["remove", "c.c", "a.a"]) == 0

        # Four spaces, where uv indents by the one after the bracket once the first item is gone.
        assert toml(project) == '[project]\ncollections = [\n    "d.d",\n]\n'

    def test_from_a_group(self, project: Path) -> None:
        write(project, TOML + '\n[project.groups.dev]\ncollections = ["d.d"]\n')

        assert main(["remove", "--dev", "d.d"]) == 0

        assert toml(project) == TOML + "\n[project.groups.dev]\ncollections = []\n"

    def test_from_a_group_that_isnt_default(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Synced with the default groups alone afterwards, as uv remove --group syncs: the rest of the group goes too.
        write(project)
        assert main(["add", "--group", "test", "a.a", "d.d"]) == 0
        assert set(installed_versions(project / "collections")) == {"a.a", "b.b", "c.c", "d.d"}
        _ = capsys.readouterr()

        assert main(["remove", "--group", "test", "d.d"]) == 0

        assert '[project.groups.test]\ncollections = [\n    "a.a>=2.0.0",\n]\n' in toml(project)
        assert set(installed_versions(project / "collections")) == {"c.c"}
        assert lines(capsys.readouterr().err)[-3:] == [" - a.a==2.0.0", " - b.b==1.1.0", " - d.d==1.0.0"]

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

        assert installed_versions(project / "collections") == {"b.b": "1.1.0", "c.c": "1.0.0"}

    def test_remove_upgrades(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["b.b<1.1", "c.c"]\n')
        _ = main(["sync"])
        write(project, '[project]\ncollections = ["b.b", "c.c"]\n')

        assert main(["remove", "-U", "c.c"]) == 0

        assert installed_versions(project / "collections") == {"b.b": "1.1.0"}

    def test_add_upgrades_a_group(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["c.c"]\n[project.groups.test]\ncollections = ["b.b<1.1"]\n')
        _ = main(["sync"])
        write(project, '[project]\ncollections = ["c.c"]\n[project.groups.test]\ncollections = ["b.b"]\n')

        # The group is upgraded in the lock; add installs only the default groups and its own, so test isn't.
        assert main(["add", "--upgrade-group", "test", "d.d"]) == 0

        assert 'name = "b.b"\nversion = "1.1.0"' in (project / "alai.lock").read_text()
        assert installed_versions(project / "collections") == {"c.c": "1.0.0", "d.d": "1.0.0"}

    def test_remove_upgrades_a_group(self, project: Path) -> None:
        write(project, '[project]\ncollections = ["c.c", "d.d"]\n[project.groups.dev]\ncollections = ["b.b<1.1"]\n')
        _ = main(["sync"])
        write(project, '[project]\ncollections = ["c.c", "d.d"]\n[project.groups.dev]\ncollections = ["b.b"]\n')

        assert main(["remove", "--upgrade-group", "dev", "d.d"]) == 0

        assert installed_versions(project / "collections") == {"b.b": "1.1.0", "c.c": "1.0.0"}

    def test_add_reinstalls(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["b.b", "c.c"]\n')
        _ = main(["sync"])
        cdir = project / "collections"
        edit(cdir, "b.b", "c.c")
        _ = capsys.readouterr()

        assert main(["add", "--reinstall-collection", "c.c", "d.d"]) == 0

        assert lines(capsys.readouterr().err)[-2:] == [" ~ c.c==1.0.0", " + d.d==1.0.0"]
        assert edited(cdir) == {"b.b"}
        assert installed_versions(project / "collections") == {"b.b": "1.1.0", "c.c": "1.0.0", "d.d": "1.0.0"}

    def test_remove_reinstalls(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["c.c", "d.d"]\n')
        _ = main(["sync"])
        cdir = project / "collections"
        edit(cdir, "c.c", "d.d")
        _ = capsys.readouterr()

        assert main(["remove", "--reinstall", "d.d"]) == 0

        assert lines(capsys.readouterr().err)[-2:] == [" ~ c.c==1.0.0", " - d.d==1.0.0"]
        assert edited(cdir) == set()
        assert installed_versions(project / "collections") == {"c.c": "1.0.0"}

    @pytest.mark.parametrize(
        "argv",
        [
            ["add", "--frozen", "-U", "c.c"],
            ["remove", "--frozen", "-P", "c.c", "c.c"],
            ["add", "--frozen", "--upgrade-group", "test", "c.c"],
        ],
    )
    def test_frozen_refuses(self, project: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
        write(project)

        with pytest.raises(SystemExit) as e:
            _ = main(argv)

        assert e.value.code == EXIT_ERROR
        assert "--frozen doesn't" in capsys.readouterr().err
        assert toml(project) == TOML


class TestLocked:
    def synced(self, project: Path, text: str = TOML) -> bytes:
        write(project, text)
        assert main(["sync"]) == 0
        return (project / "alai.lock").read_bytes()

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (
                ["add", "--locked", "d.d"],
                [
                    "Resolved 2 collections",
                    "Added d.d v1.0.0",
                    "alai.toml has changed since alai.lock was made:",
                    "  project.collections: added d.d>=1.0.0",
                ],
            ),
            (
                ["remove", "--locked", "c.c"],
                [
                    "Resolved 0 collections",
                    "Removed c.c v1.0.0",
                    "alai.toml has changed since alai.lock was made:",
                    "  project.collections: removed c.c",
                ],
            ),
        ],
    )
    def test_refuses_a_change(
        self, project: Path, capsys: pytest.CaptureFixture[str], argv: list[str], expected: list[str]
    ) -> None:
        before = self.synced(project)
        _ = capsys.readouterr()

        assert main(argv) == EXIT_STALE

        assert lines(capsys.readouterr().err) == [*expected, "error: alai.lock would change, and --locked was given"]
        assert toml(project) == TOML
        assert (project / "alai.lock").read_bytes() == before
        assert installed_versions(project / "collections") == {"c.c": "1.0.0"}

    def test_already_there(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        before = self.synced(project)
        (project / "collections").joinpath("ansible_collections", "c", "c", "MANIFEST.json").unlink()
        _ = capsys.readouterr()

        # Nothing to change, so it goes on to sync, as uv add --locked does.
        assert main(["add", "--locked", "c.c"]) == 0

        assert toml(project) == TOML
        assert (project / "alai.lock").read_bytes() == before
        assert installed_versions(project / "collections") == {"c.c": "1.0.0"}

    def test_spelt_differently(self, project: Path) -> None:
        before = self.synced(project, '[project]\ncollections = ["c.c>=1"]\n')

        assert main(["add", "--locked", "c.c>=1.0.0"]) == 0

        # alai.toml takes the requirement as given, and alai.lock keeps its own text, as uv keeps uv.lock's.
        assert toml(project) == '[project]\ncollections = [\n    "c.c>=1.0.0",\n]\n'
        assert (project / "alai.lock").read_bytes() == before

    def test_stale_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = self.synced(project)
        stale = '[project]\ncollections = ["c.c", "d.d"]\n'
        write(project, stale)
        _ = capsys.readouterr()

        # The edit changes nothing, but the lock is already out of date.
        assert main(["add", "--locked", "c.c"]) == EXIT_STALE

        # Only alai.toml says why: d.d was already added by hand, and nothing was locked since.
        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "Added d.d v1.0.0",
            "alai.toml has changed since alai.lock was made:",
            "  project.collections: added d.d",
            "error: alai.lock would change, and --locked was given",
        ]
        assert toml(project) == stale

    @pytest.mark.parametrize("argv", [["add", "--locked", "d.d"], ["remove", "--locked", "c.c"]])
    def test_needs_a_lock(self, project: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
        write(project)

        assert main(argv) == EXIT_STALE

        # Checked before resolving, as uv checks first.
        assert lines(capsys.readouterr().err) == ["error: there's no alai.lock, and --locked was given; run alai lock"]
        assert toml(project) == TOML
        assert not (project / "alai.lock").exists()
        assert not (project / "collections").exists()

    def test_upgrade(self, project: Path) -> None:
        # As sync --locked -U: allowed, failing only if the upgrade would change alai.lock.
        before = self.synced(project, '[project]\ncollections = ["b.b<1.1", "c.c"]\n')

        assert main(["add", "--locked", "-U", "c.c"]) == 0
        assert (project / "alai.lock").read_bytes() == before

        write(project, '[project]\ncollections = ["b.b", "c.c"]\n')
        assert main(["lock"]) == 0
        before = (project / "alai.lock").read_bytes()

        assert main(["add", "--locked", "-U", "c.c"]) == EXIT_STALE
        assert (project / "alai.lock").read_bytes() == before
        assert installed_versions(project / "collections") == {"b.b": "1.0.0", "c.c": "1.0.0"}

    def test_with_no_sync(self, project: Path) -> None:
        _ = self.synced(project)
        (project / "collections").joinpath("ansible_collections", "c", "c", "MANIFEST.json").unlink()

        assert main(["add", "--locked", "--no-sync", "c.c"]) == 0

        assert installed_versions(project / "collections") == {}

    @pytest.mark.parametrize("command", ["add", "remove"])
    def test_not_with_frozen(self, project: Path, capsys: pytest.CaptureFixture[str], command: str) -> None:
        write(project)

        with pytest.raises(SystemExit) as e:
            _ = main([command, "--locked", "--frozen", "c.c"])

        assert e.value.code == 2
        assert "argument --locked: not allowed with argument --frozen" in capsys.readouterr().err
        assert toml(project) == TOML


class TestErrors:
    def test_no_project_file(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["add", "c.c"]) == EXIT_ERROR

        assert "no alai.toml found in" in capsys.readouterr().err

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

    def test_inline_table(self, project: Path) -> None:
        write(project, 'project = { collections = ["c.c"] }\n')

        assert main(["add", "d.d"]) == 0

        assert toml(project) == 'project = { collections = [\n    "c.c",\n    "d.d>=1.0.0",\n] }\n'

    def test_edit_refused(
        self, project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An edit the editor won't make, such as one whose text wouldn't read back as intended, names what to edit
        # by hand.
        class Refusing:
            def __init__(self, text: str) -> None:
                self.doc = TomlDocument(text)

            def __getattr__(self, name: str) -> object:
                return getattr(self.doc, name)

            def insert(self, path: tuple[str, ...], index: int, value: str) -> None:
                raise TomlEditError("editing project.collections would change more than it should")

        monkeypatch.setattr(_edit, "read_toml_document", lambda path: Refusing(Path(path).read_text()))
        write(project, '[project]\ncollections = ["c.c"]\n')

        assert main(["add", "d.d"]) == EXIT_ERROR

        err = capsys.readouterr().err
        assert "would change more than it should); add the collections in project.collections by hand" in err
        assert toml(project) == '[project]\ncollections = ["c.c"]\n'

    @pytest.mark.parametrize("argv", [["add", "--group", "x", "--dev", "c.c"], ["add", "--no-sync", "--frozen", "c.c"]])
    def test_exclusive(self, project: Path, argv: list[str]) -> None:
        with pytest.raises(SystemExit) as e:
            _ = main(argv)
        assert e.value.code == 2
