# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``alai collection path``: where a collection's files are, for tools that read them."""

from __future__ import annotations

import collections.abc as _c
import json
import typing as _t
from pathlib import Path

import pytest
from helpers import write_project
from index_data import plugin
from index_server import IndexServer
from python_index import make_python_index

from alai._cli import main
from alai._common import EXIT_ERROR, EXIT_NOT_FOUND

# The index (conftest's COLLECTIONS): a.a 1.0.0 and 2.0.0 (which needs b.b>=1.0.0), b.b 1.0.0 and 1.1.0, c.c and d.d.

BUILTIN_LINES = [
    plugin("ansible.builtin.copy", short_description="Copy files"),
    plugin("eda.builtin.range", "event_source", short_description="Generate events"),
]


@pytest.fixture
def project(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _c.Iterator[Path]:
    """conftest's project, with a Python index whose ansible-core and ansible-rulebook hold ``BUILTIN_LINES``."""
    make_python_index(tmp_path / "pypi", BUILTIN_LINES)
    with IndexServer(tmp_path / "pypi") as pypi:
        monkeypatch.setenv("ALAI_PYTHON_INDEX", pypi.url)
        yield project


def path_json(capsys: pytest.CaptureFixture[str], *args: str, code: int = 0) -> dict[str, _t.Any]:
    _ = capsys.readouterr()
    assert main(["collection", "path", "--output-format", "json", *args]) == code
    return json.loads(capsys.readouterr().out)


def entry(capsys: pytest.CaptureFixture[str], name: str, *args: str, code: int = 0) -> dict[str, _t.Any] | None:
    return path_json(capsys, name, *args, code=code)["collections"][name]


class TestWhere:
    def test_downloaded_then_cached(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        first = entry(capsys, "a.a")
        assert first is not None
        assert (first["version"], first["origin"], first["latest"]) == ("2.0.0", "downloaded", None)
        assert (Path(first["path"]) / "MANIFEST.json").is_file()

        second = entry(capsys, "a.a")
        assert second == {**first, "origin": "cached"}

    def test_cached_with_a_newer_version(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["collection", "install", "--collections-dir", "out", "a.a==1.0.0"]) == 0

        found = entry(capsys, "a.a")

        # Outside a project, out/ isn't the collections directory, so the cache has it.
        assert found is not None
        assert (found["version"], found["origin"], found["latest"]) == ("1.0.0", "cached", "2.0.0")

    def test_installed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["c.c"])
        assert main(["sync"]) == 0

        found = entry(capsys, "c.c")

        assert found == {
            "version": "1.0.0",
            "path": str(project / "collections" / "ansible_collections" / "c" / "c"),
            "origin": "installed",
            "latest": None,
        }

    def test_locked(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a==1.0.0"])
        assert main(["lock"]) == 0

        # The locked version, not the latest, fetched as alai sync would.
        first = entry(capsys, "a.a")
        assert first is not None
        assert (first["version"], first["origin"]) == ("1.0.0", "downloaded")
        second = entry(capsys, "a.a")
        assert second is not None
        assert second["origin"] == "locked"

    def test_collection_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "alai.toml").write_text("")
        _ = (project / "galaxy.yml").write_text(
            "namespace: ns\nname: col\nversion: 1.2.0\nreadme: README.md\nauthors: [me]\n"
        )

        result = path_json(capsys, "ns.col")

        assert result["project"] == "ns.col"
        assert result["collections"]["ns.col"] == {
            "version": "1.2.0",
            "path": str(project),
            "origin": "project",
            "latest": None,
        }

    def test_project_alone(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert path_json(capsys) == {"v": 1, "project": None, "collections": {}}

    def test_builtins(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        result = path_json(capsys, "ansible.builtin", "eda.builtin")["collections"]

        core, rulebook = result["ansible.builtin"], result["eda.builtin"]
        assert (core["version"], core["origin"]) == ("2.20.0", "downloaded")
        assert (rulebook["version"], rulebook["origin"]) == ("1.2.0", "downloaded")
        # Kept as the extractor reads them.
        assert (Path(core["path"]) / "lib" / "ansible" / "release.py").is_file()
        assert (Path(rulebook["path"]) / "ansible_rulebook" / "__init__.py").is_file()

        again = entry(capsys, "ansible.builtin")
        assert again == {**core, "origin": "cached"}

    def test_builtins_offline_from_the_cache(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = entry(capsys, "ansible.builtin")

        found = entry(capsys, "ansible.builtin", "--offline")

        assert found is not None
        assert found["version"] == "2.20.0"

    def test_find_keeps_the_source(self, project: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        # alai find reads the same sdists, so what it fetched is found without another download.
        _ = main(["find", "copy", "--no-index"])

        found = entry(capsys, "ansible.builtin", "--no-download")

        assert found is not None
        assert found["origin"] == "cached"


class TestNotFound:
    def test_no_index_has_it(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert entry(capsys, "nope.nope", code=EXIT_NOT_FOUND) is None

    def test_text(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["collection", "path", "c.c", "nope.nope"]) == EXIT_NOT_FOUND

        out, err = capsys.readouterr()
        assert out.endswith("\n") and len(out.splitlines()) == 1
        assert "no index has nope.nope" in err

    def test_no_download(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        result = path_json(capsys, "--no-download", "a.a", "ansible.builtin", code=EXIT_NOT_FOUND)

        assert result["collections"] == {"a.a": None, "ansible.builtin": None}
        assert not (project.parent / "cache" / "collections-v1").exists()

    def test_bad_name(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        found = entry(capsys, "Not-A-Name", code=EXIT_ERROR)

        assert found is not None
        assert "error" in found

    def test_text_needs_a_name(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            _ = main(["collection", "path"])

        assert "the following arguments are required: NAME" in capsys.readouterr().err
