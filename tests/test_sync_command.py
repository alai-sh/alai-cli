# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from index_data import make_index, v

from alai import _output
from alai._cli import EXIT_ERROR, main
from alai._commands.lock import EXIT_STALE

COLLECTIONS: dict[str, list[dict[str, object]]] = {
    "a.a": [v("1.0.0"), v("2.0.0", {"b.b": ">=1.0.0"})],
    "b.b": [v("1.0.0"), v("1.1.0")],
    "c.c": [v("1.0.0")],
    "d.d": [v("1.0.0")],
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project directory as the working directory, with only a local index of real artifacts configured."""
    for name in [n for n in os.environ if n.startswith(("ALAI_", "XDG_"))]:
        monkeypatch.delenv(name)
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "xdg").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg"))
    monkeypatch.setenv("ALAI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "index", COLLECTIONS, artifacts=True))
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(_output, "verbosity", 0)
    return root


def write(project: Path, collections: list[str], groups: dict[str, list[str]] | None = None) -> None:
    lines = ["[project]", f"collections = {json.dumps(collections)}"]
    for name, items in (groups or {}).items():
        lines += [f"[project.groups.{name}]", f"collections = {json.dumps(items)}"]
    _ = (project / "alai.toml").write_text("\n".join(lines) + "\n")


def sync(*args: str) -> int:
    return main(["sync", *args])


def lines(err: str) -> list[str]:
    """The output with its timings dropped, which vary."""
    return [line.rsplit(" in ", 1)[0] if not line.startswith(" ") else line for line in err.splitlines()]


def installed(project: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for manifest in sorted((project / "collections" / "ansible_collections").glob("*/*/MANIFEST.json")):
        info = json.loads(manifest.read_text())["collection_info"]
        out[f"{info['namespace']}.{info['name']}"] = info["version"]
    return out


class TestSync:
    def test_locks_and_installs(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a", "c.c"])

        assert sync() == 0

        out, err = capsys.readouterr()
        assert out == ""
        assert lines(err) == [
            "Resolved 3 collections",
            "Prepared 3 collections",
            "Installed 3 collections",
            " + a.a==2.0.0",
            " + b.b==1.1.0",
            " + c.c==1.0.0",
        ]
        assert installed(project) == {"a.a": "2.0.0", "b.b": "1.1.0", "c.c": "1.0.0"}
        assert (project / "alai.lock").is_file()

    def test_nothing_to_do(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])
        _ = sync()
        lock = (project / "alai.lock").stat()
        _ = capsys.readouterr()

        assert sync() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Checked 2 collections"]
        assert (project / "alai.lock").stat().st_mtime_ns == lock.st_mtime_ns

    def test_replaces_a_version(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a", "b.b"])
        _ = sync()
        write(project, ["a.a", "b.b<1.1"])
        _ = capsys.readouterr()

        assert sync() == 0

        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "Prepared 1 collection",
            "Uninstalled 1 collection",
            "Installed 1 collection",
            " - b.b==1.1.0",
            " + b.b==1.0.0",
        ]
        assert installed(project)["b.b"] == "1.0.0"

    def test_removes_extras(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a", "c.c"])
        _ = sync()
        write(project, ["a.a"])
        _ = capsys.readouterr()

        assert sync() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Uninstalled 1 collection", " - c.c==1.0.0"]
        assert "c.c" not in installed(project)

    def test_inexact_keeps_extras(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a", "c.c"])
        _ = sync()
        write(project, ["a.a"])
        _ = capsys.readouterr()

        assert sync("--inexact") == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Checked 2 collections"]
        assert "c.c" in installed(project)

    def test_offline_from_the_cache(self, project: Path, tmp_path: Path) -> None:
        write(project, ["a.a"])
        _ = sync()
        (project / "collections").rename(tmp_path / "moved")

        assert sync("--offline") == 0
        assert installed(project) == {"a.a": "2.0.0", "b.b": "1.1.0"}

    def test_installs_to_collections_dir(self, project: Path, tmp_path: Path) -> None:
        write(project, ["c.c"])

        assert sync("--collections-dir", str(tmp_path / "elsewhere")) == 0

        assert (tmp_path / "elsewhere" / "ansible_collections" / "c" / "c" / "MANIFEST.json").is_file()

    def test_quiet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])

        assert main(["-q", "sync"]) == 0

        assert capsys.readouterr() == ("", "")


class TestGroups:
    GROUPS: dict[str, list[str]] = {"dev": ["c.c"], "docs": ["d.d"]}

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            ([], {"a.a", "b.b", "c.c"}),
            (["--no-dev"], {"a.a", "b.b"}),
            (["--no-default-groups"], {"a.a", "b.b"}),
            (["--group", "docs"], {"a.a", "b.b", "c.c", "d.d"}),
            (["--all-groups"], {"a.a", "b.b", "c.c", "d.d"}),
            (["--all-groups", "--no-group", "dev"], {"a.a", "b.b", "d.d"}),
            (["--only-group", "docs"], {"d.d"}),
            (["--only-dev"], {"c.c"}),
            (["--only-group", "docs", "--no-group", "docs"], set()),
        ],
    )
    def test_selection(self, project: Path, args: list[str], expected: set[str]) -> None:
        write(project, ["a.a"], self.GROUPS)

        assert sync(*args) == 0

        assert set(installed(project)) == expected

    def test_without_a_dev_group(self, project: Path) -> None:
        # --no-dev and --no-group dev are fine with no dev group, so a CI job can always pass them.
        write(project, ["a.a"], {"docs": ["d.d"]})

        assert sync("--no-dev", "--no-group", "dev") == 0
        assert set(installed(project)) == {"a.a", "b.b"}

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--group", "nope"], "--group: alai.toml has no group 'nope' (it has 'dev', 'docs')"),
            (["--only-group", "nope"], "--only-group: alai.toml has no group 'nope'"),
            (["--no-group", "nope"], "--no-group: alai.toml has no group 'nope'"),
            (["--only-group", "docs", "--group", "dev"], "--only-group and --only-dev can't be used with --group"),
        ],
    )
    def test_errors(self, project: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str) -> None:
        write(project, ["a.a"], self.GROUPS)

        assert sync(*args) == EXIT_ERROR

        assert message in capsys.readouterr().err
        assert not (project / "collections").exists()

    def test_only_dev_without_one(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        assert sync("--only-dev") == EXIT_ERROR
        assert "--only-dev: alai.toml has no group 'dev'" in capsys.readouterr().err


class TestLockFlags:
    def test_stale_lock_is_rewritten(self, project: Path) -> None:
        write(project, ["c.c"])
        _ = sync()
        write(project, ["c.c", "d.d"])

        assert sync() == 0

        assert 'name = "d.d"' in (project / "alai.lock").read_text()
        assert set(installed(project)) == {"c.c", "d.d"}

    def test_locked_refuses_a_stale_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])
        _ = sync()
        before = (project / "alai.lock").read_bytes()
        write(project, ["c.c", "d.d"])
        _ = capsys.readouterr()

        assert sync("--locked") == EXIT_STALE

        err = capsys.readouterr().err
        assert "error: alai.lock is out of date, and --locked was given; run alai lock" in err
        assert (project / "alai.lock").read_bytes() == before
        assert set(installed(project)) == {"c.c"}

    def test_locked_current(self, project: Path) -> None:
        write(project, ["c.c"])
        assert main(["lock"]) == 0

        assert sync("--locked") == 0
        assert set(installed(project)) == {"c.c"}

    def test_locked_never_writes(self, project: Path) -> None:
        # Spelt differently but asking for the same, so the lock is current and keeps its own text.
        write(project, ["c.c>=1"])
        _ = sync()
        before = (project / "alai.lock").read_bytes()
        write(project, ["c.c >= 1.0.0"])

        assert sync("--locked") == 0
        assert (project / "alai.lock").read_bytes() == before

    @pytest.mark.parametrize("flag", ["--locked", "--frozen"])
    def test_needs_a_lock(self, project: Path, capsys: pytest.CaptureFixture[str], flag: str) -> None:
        write(project, ["c.c"])

        assert sync(flag) == EXIT_STALE

        assert f"error: there's no alai.lock, and {flag} was given; run alai lock" in capsys.readouterr().err

    def test_frozen_ignores_alai_toml(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])
        assert main(["lock"]) == 0
        write(project, ["c.c", "d.d"])
        _ = capsys.readouterr()

        assert sync("--frozen") == 0

        assert lines(capsys.readouterr().err)[0] == "Prepared 1 collection"
        assert set(installed(project)) == {"c.c"}

    def test_frozen_downloads_from_the_locked_index(self, project: Path, tmp_path: Path) -> None:
        # The lock's index isn't configured any more: --frozen still fetches from the URL it records.
        write(project, ["c.c"])
        assert main(["lock"]) == 0
        other = make_index(tmp_path / "other", {"x.x": [v("1.0.0")]})

        assert sync("--frozen", "--default-index", other) == 0
        assert set(installed(project)) == {"c.c"}

    def test_locked_and_frozen_exclusive(self, project: Path) -> None:
        with pytest.raises(SystemExit) as e:
            _ = sync("--locked", "--frozen")
        assert e.value.code == 2


class TestErrors:
    def test_no_project_file(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert sync() == EXIT_ERROR

        assert "there's no alai.toml in" in capsys.readouterr().err

    def test_offline_miss(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Locked from a file index, then installed offline: file artifacts are read directly, so use an http one.
        write(project, ["c.c"])
        assert main(["lock"]) == 0
        lock = project / "alai.lock"
        _ = lock.write_text(lock.read_text().replace((Path.cwd().parent / "index").as_uri(), "https://index.example"))
        _ = capsys.readouterr()

        assert sync("--frozen", "--offline") == EXIT_ERROR

        assert "alai is offline" in capsys.readouterr().err

    @pytest.mark.parametrize("flag", ["--check", "-U"])
    def test_lock_flags_not_taken(self, project: Path, flag: str) -> None:
        with pytest.raises(SystemExit) as e:
            _ = sync(flag)
        assert e.value.code == 2
