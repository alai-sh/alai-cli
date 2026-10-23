# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection list``: what's in the collections directory, compared with alai.lock."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from helpers import Repo, collection_source

from alai import _output
from alai._cli import main
from alai._common import EXIT_ERROR
from alai._list import Row, _Renderer

# The index (conftest's COLLECTIONS): a.a 1.0.0 and 2.0.0 (which needs b.b>=1.0.0), b.b 1.0.0 and 1.1.0, c.c and d.d.


def listing(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[list[str], list[str]]:
    """The list's stdout and stderr lines, after clearing what came before."""
    _ = capsys.readouterr()
    assert main(["collection", "list", *args]) == 0
    out, err = capsys.readouterr()
    return out.splitlines(), err.splitlines()


def write(project: Path, text: str) -> None:
    _ = (project / "alai.toml").write_text(text)


def sync() -> None:
    assert main(["sync"]) == 0


def cdir(project: Path) -> Path:
    return project / "collections" / "ansible_collections"


class TestWhere:
    def test_needs_a_project_or_a_directory(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["collection", "list"]) == EXIT_ERROR

        assert "no project found from" in capsys.readouterr().err

    def test_explicit_directory(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["collection", "install", "--collections-dir", str(tmp_path / "out"), "c.c"]) == 0

        out, err = listing(capsys, "--collections-dir", str(tmp_path / "out"))

        assert out == ["c.c  1.0.0"]
        assert err == [f"Using collections directory at: {tmp_path / 'out'}", "No alai.lock to compare with"]

    def test_nothing_installed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "ansible.cfg").write_text("[defaults]\n")

        out, err = listing(capsys)

        assert out == []
        assert err == [
            "Using collections directory at: collections",
            "No alai.lock to compare with",
            "No collections installed",
        ]

    def test_quiet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "ansible.cfg").write_text("[defaults]\n")
        assert main(["collection", "install", "a.a"]) == 0

        out, err = listing(capsys, "-q")

        assert out == ["a.a  2.0.0", "b.b  1.1.0"]
        assert err == []


class TestWithoutLock:
    def test_no_markers(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "ansible.cfg").write_text("[defaults]\n")
        assert main(["collection", "install", "a.a", "c.c"]) == 0

        out, err = listing(capsys)

        assert out == ["a.a  2.0.0", "b.b  1.1.0", "c.c  1.0.0"]
        # Named even when it's collections/ here, as collection install doesn't: a listing of the wrong directory
        # would look like an empty or partial one.
        assert err == ["Using collections directory at: collections", "No alai.lock to compare with"]

    def test_unknown_version(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "ansible.cfg").write_text("[defaults]\n")
        (cdir(project) / "ns" / "made").mkdir(parents=True)

        out, _ = listing(capsys)

        assert out == ["ns.made  unknown"]


class TestCompared:
    def test_all_locked(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["a.a"]\n')
        sync()

        out, err = listing(capsys)

        assert out == [" ✓ a.a  2.0.0", " ✓ b.b  1.1.0"]
        assert err == ["Using collections directory at: collections"]

    def test_not_in_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["c.c"]\n')
        sync()
        assert main(["collection", "install", "d.d"]) == 0

        out, err = listing(capsys)

        assert out == [" ✓ c.c  1.0.0", " ? d.d  1.0.0  not in alai.lock"]
        assert err == [
            "Using collections directory at: collections",
            "hint: alai sync installs what alai.lock lists and removes the rest",
        ]

    def test_other_version(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["a.a"]\n')
        sync()
        assert main(["collection", "install", "a.a==1.0.0"]) == 0

        out, _ = listing(capsys)

        assert out == [" ≠ a.a  1.0.0  alai.lock has 2.0.0", " ✓ b.b  1.1.0"]

    def test_missing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["a.a"]\n')
        sync()
        assert main(["collection", "uninstall", "b.b"]) == 0

        out, _ = listing(capsys)

        assert out == [" ✓ a.a  2.0.0", " ✗ b.b         alai.lock has 1.1.0, not installed"]

    def test_other_group_not_missing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # A plain alai sync doesn't install a group that isn't a default one, so it isn't missing when it's not there.
        write(project, '[project]\ncollections = ["c.c"]\n\n[project.groups.test]\ncollections = ["d.d"]\n')
        sync()

        out, _ = listing(capsys)

        assert out == [" ✓ c.c  1.0.0"]

    def test_other_group_installed_is_locked(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["c.c"]\n\n[project.groups.test]\ncollections = ["d.d"]\n')
        assert main(["sync", "--group", "test"]) == 0

        out, _ = listing(capsys)

        assert out == [" ✓ c.c  1.0.0", " ✓ d.d  1.0.0"]

    def test_namespace_link(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, '[project]\ncollections = ["c.c"]\n')
        sync()
        (tmp_path / "elsewhere").mkdir()
        (cdir(project) / "ns").symlink_to(tmp_path / "elsewhere")

        out, _ = listing(capsys)

        assert out == [" ✓ c.c   1.0.0", f" ? ns.*         not in alai.lock; link to {tmp_path / 'elsewhere'}"]


class TestSources:
    def test_directory(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(tmp_path / "src")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}" }}\n',
        )
        sync()

        out, _ = listing(capsys)

        assert out == [f" ✓ ns.src  1.0.0  {tmp_path / 'src'}"]

    def test_directory_changed_since(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(tmp_path / "src")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}" }}\n',
        )
        sync()
        _ = collection_source(tmp_path / "src", extra="description: changed\n")

        out, _ = listing(capsys)

        assert out == [f" ≠ ns.src  1.0.0  its source has changed since it was installed; {tmp_path / 'src'}"]

    def test_other_directory(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(tmp_path / "src")
        _ = collection_source(tmp_path / "other")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}" }}\n',
        )
        sync()
        assert main(["collection", "install", str(tmp_path / "other")]) == 0

        out, _ = listing(capsys)

        assert out == [f" ≠ ns.src  1.0.0  alai.lock has it from {tmp_path / 'src'}; {tmp_path / 'other'}"]

    def test_index_in_place_of_directory(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _ = collection_source(tmp_path / "src", name="c.c", version="2.0.0")
        write(
            project,
            f'[project]\ncollections = ["c.c"]\n\n[project.sources]\n"c.c" = {{ path = "{tmp_path / "src"}" }}\n',
        )
        sync()
        assert main(["collection", "install", "c.c==1.0.0"]) == 0

        out, _ = listing(capsys)

        assert out == [f" ≠ c.c  1.0.0  alai.lock has 2.0.0 from {tmp_path / 'src'}"]

    def test_directory_in_place_of_index(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write(project, '[project]\ncollections = ["c.c"]\n')
        sync()
        _ = collection_source(tmp_path / "src", name="c.c")
        assert main(["collection", "install", str(tmp_path / "src")]) == 0

        out, _ = listing(capsys)

        assert out == [f" ≠ c.c  1.0.0  alai.lock has it from index 'alai'; {tmp_path / 'src'}"]

    def test_editable(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(tmp_path / "src")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}", editable = true }}\n',
        )
        sync()

        out, _ = listing(capsys)

        assert out == [f" ✓ ns.src  1.0.0  {tmp_path / 'src'} editable"]

    def test_editable_version_from_its_source(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _ = collection_source(tmp_path / "src")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}", editable = true }}\n',
        )
        sync()
        _ = collection_source(tmp_path / "src", version="1.1.0")

        out, _ = listing(capsys)

        assert out == [f" ≠ ns.src  1.1.0  alai.lock has 1.0.0; {tmp_path / 'src'} editable"]

    def test_copy_in_place_of_editable(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(tmp_path / "src")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}", editable = true }}\n',
        )
        sync()
        assert main(["collection", "install", str(tmp_path / "src")]) == 0

        out, _ = listing(capsys)

        expected = f" ≠ ns.src  1.0.0  alai.lock has it from {tmp_path / 'src'}, editable; {tmp_path / 'src'}"
        assert out == [expected]

    def test_shortens_home(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        home = Path(os.environ["HOME"])
        _ = collection_source(home / "src")
        write(
            project,
            f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{home / "src"}" }}\n',
        )
        sync()

        out, _ = listing(capsys)

        assert out == [" ✓ ns.src  1.0.0  ~/src"]

    def test_git_other_commit(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo = Repo(tmp_path / "repo")
        repo.collection()
        first = repo.commit()
        write(
            project, f'[project]\ncollections = ["ns.src"]\n\n[project.sources]\n"ns.src" = {{ git = "{repo.url}" }}\n'
        )
        sync()
        repo.collection(readme="changed\n")
        second = repo.commit()
        assert main(["collection", "install", f"git+{repo.url}@{second}"]) == 0

        out, _ = listing(capsys)

        assert out == [f" ≠ ns.src  1.0.0  alai.lock has it at {first[:7]}; git+{repo.url}@{second[:7]}"]

    def test_project_collection(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(project, name="my.own", dependencies={"c.c": "*"})
        write(project, "")
        sync()

        out, _ = listing(capsys)

        assert out == [" ✓ c.c     1.0.0", f" ✓ my.own  1.0.0  {project} editable"]


class TestJson:
    def test_compared(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(tmp_path / "src")
        write(
            project,
            f'[project]\ncollections = ["a.a", "ns.src"]\n\n[project.sources]\n"ns.src" = {{ path = "{tmp_path / "src"}", editable = true }}\n',
        )
        sync()
        assert main(["collection", "uninstall", "b.b"]) == 0
        assert main(["collection", "install", "c.c"]) == 0

        out, err = listing(capsys, "--output-format", "json")

        assert err == []
        result = json.loads("\n".join(out))
        index = os.environ["ALAI_DEFAULT_INDEX"]
        root = cdir(project)
        assert result == {
            "v": 1,
            "collections_dir": str(project / "collections"),
            "lock": str(project / "alai.lock"),
            "collections": [
                {
                    "name": "a.a",
                    "version": "2.0.0",
                    "path": str(root / "a" / "a"),
                    "source": None,
                    "editable": False,
                    "lock": {"status": "locked", "version": "2.0.0", "source": {"index": index}},
                },
                {
                    "name": "b.b",
                    "version": None,
                    "path": None,
                    "source": None,
                    "editable": False,
                    "lock": {"status": "missing", "version": "1.1.0", "source": {"index": index}},
                },
                {
                    "name": "c.c",
                    "version": "1.0.0",
                    "path": str(root / "c" / "c"),
                    "source": None,
                    "editable": False,
                    "lock": {"status": "unlocked", "version": None, "source": None},
                },
                {
                    "name": "ns.src",
                    "version": "1.0.0",
                    "path": str(root / "ns" / "src"),
                    "source": {"directory": str(tmp_path / "src")},
                    "editable": True,
                    "lock": {"status": "locked", "version": "1.0.0", "source": {"editable": str(tmp_path / "src")}},
                },
            ],
            "namespace_links": [],
        }

    def test_without_lock(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "ansible.cfg").write_text("[defaults]\n")
        assert main(["collection", "install", "c.c"]) == 0
        (tmp_path / "elsewhere").mkdir()
        (cdir(project) / "ns").symlink_to(tmp_path / "elsewhere")

        out, _ = listing(capsys, "--output-format", "json")

        result = json.loads("\n".join(out))
        assert result["lock"] is None
        assert result["collections"][0]["lock"] is None
        assert result["namespace_links"] == [
            {"namespace": "ns", "path": str(cdir(project) / "ns"), "target": str(tmp_path / "elsewhere")}
        ]


def terminal(monkeypatch: pytest.MonkeyPatch, columns: int = 80) -> None:
    """Make capsys's stdout a terminal ``columns`` wide; called in the test, once capsys has replaced stdout."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", str(columns))


class TestTerminal:
    ROWS = (
        Row("ansible.posix", None, "p", status="locked"),
        Row(
            "corp.tools",
            None,
            "p",
            source={"git": "https://github.com/corp/tools.git", "commit": "3f2a9c1" + "0" * 33},
            status="different",
            note="alai.lock has it at 9b1e0d4",
        ),
        Row(
            "corp.dev",
            None,
            "p",
            source={"directory": "/src/corp.dev"},
            editable=True,
            status="unlocked",
            note="not in alai.lock",
        ),
        Row("microsoft.ad", None, None, status="missing", note="alai.lock has 1.9.0, not installed"),
    )

    def test_wraps_the_source(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        terminal(monkeypatch)
        _Renderer(compared=True).listing(list(self.ROWS))

        lines = capsys.readouterr().out.splitlines()
        assert lines[:5] == [
            " ✓ ansible.posix  unknown",
            " ≠ corp.tools     unknown  alai.lock has it at 9b1e0d4",
            "                           git+https://github.com/corp/tools.git@3f2a9c1",
            " ? corp.dev       unknown  not in alai.lock; /src/corp.dev editable",
            " ✗ microsoft.ad            alai.lock has 1.9.0, not installed",
        ]

    def test_cuts_what_still_doesnt_fit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        terminal(monkeypatch, 60)

        _Renderer(compared=True).listing([self.ROWS[1]])

        lines = capsys.readouterr().out.splitlines()
        assert lines[:2] == [
            " ≠ corp.tools  unknown  alai.lock has it at 9b1e0d4",
            "                        git+https://github.com/corp/tools.…",
        ]
        assert all(len(line) < 60 for line in lines)

    def test_legend_of_markers_shown(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        terminal(monkeypatch)
        _Renderer(compared=True).listing([self.ROWS[0], self.ROWS[3]])

        assert capsys.readouterr().out.splitlines()[-2:] == ["", "✓ as locked  ✗ locked, not installed"]

    def test_no_legend_when_all_locked(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        terminal(monkeypatch)
        _Renderer(compared=True).listing([self.ROWS[0]])

        assert capsys.readouterr().out.splitlines() == [" ✓ ansible.posix  unknown"]

    def test_colour(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        terminal(monkeypatch)
        monkeypatch.setattr(_output, "color", "always")

        _Renderer(compared=True).listing([self.ROWS[2]])

        assert capsys.readouterr().out.splitlines()[0] == (
            " \x1b[33m?\x1b[0m \x1b[34mcorp.\x1b[0m\x1b[1mdev\x1b[0m  \x1b[2munknown\x1b[0m  "
            + "\x1b[33mnot in alai.lock\x1b[0m\x1b[2m; \x1b[0m\x1b[2m/src/corp.dev\x1b[0m\x1b[36m editable\x1b[0m"
        )

    def test_piped_is_whole(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("COLUMNS", "40")

        _Renderer(compared=True).listing([self.ROWS[1]])

        assert capsys.readouterr().out.splitlines() == [
            " ≠ corp.tools  unknown  alai.lock has it at 9b1e0d4; git+https://github.com/corp/tools.git@3f2a9c1"
        ]
