# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

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
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project directory as the working directory, with only a local index configured."""
    for name in [n for n in os.environ if n.startswith(("ALAI_", "XDG_"))]:
        monkeypatch.delenv(name)
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "xdg").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "xdg"))
    monkeypatch.setenv("ALAI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "index", COLLECTIONS))
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(_output, "verbosity", 0)
    return root


def write(project: Path, collections: list[str], extra: str = "") -> None:
    listed = ", ".join(f'"{c}"' for c in collections)
    _ = (project / "alai.toml").write_text(f"[project]\ncollections = [{listed}]\n{extra}")


def lock(*args: str) -> int:
    return main(["lock", *args])


def lines(err: str) -> list[str]:
    # The first line's timing varies; everything after it doesn't.
    first, *rest = err.splitlines()
    assert first.startswith("Resolved ")
    return [first.rsplit(" in ", 1)[0], *rest]


class TestLock:
    def test_writes_the_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        assert lock() == 0

        out, err = capsys.readouterr()
        assert out == ""
        assert lines(err) == ["Resolved 2 collections"]
        text = (project / "alai.lock").read_text()
        assert 'name = "a.a"\nversion = "2.0.0"' in text
        assert '[project]\nrequirements = ["a.a"]' in text

    def test_unchanged(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])
        _ = lock()
        before = (project / "alai.lock").stat()
        _ = capsys.readouterr()

        assert lock() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections"]
        # A rewrite renames a new file over it, which would change the inode.
        assert (project / "alai.lock").stat().st_ino == before.st_ino

    def test_changes_reported(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a<2", "b.b<1.1"])
        _ = lock()
        _ = capsys.readouterr()
        # a.a 1.0.0 no longer fits; b.b 1.0.0 still does (a.a 2.0.0 needs >=1.0.0), so it's kept.
        write(project, ["a.a>=2", "c.c"])

        assert lock() == 0

        assert lines(capsys.readouterr().err) == [
            "Resolved 3 collections",
            "Updated a.a v1.0.0 -> v2.0.0",
            "Added c.c v1.0.0",
        ]

    def test_locked_versions_kept(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a", "b.b<1.1"])
        _ = lock()
        write(project, ["a.a", "b.b"])
        _ = capsys.readouterr()

        assert lock() == 0

        # b.b 1.0.0 still fits, so it stays; only the requirement changed.
        assert lines(capsys.readouterr().err) == ["Resolved 2 collections"]
        assert 'name = "b.b"\nversion = "1.0.0"' in (project / "alai.lock").read_text()

    @pytest.mark.parametrize(
        ("args", "updated"), [(["-U"], True), (["--upgrade-collection", "b.b"], True), ([], False)]
    )
    def test_upgrade(self, project: Path, capsys: pytest.CaptureFixture[str], args: list[str], updated: bool) -> None:
        write(project, ["a.a", "b.b<1.1"])
        _ = lock()
        write(project, ["a.a", "b.b"])
        _ = capsys.readouterr()

        assert lock(*args) == 0

        assert ("Updated b.b v1.0.0 -> v1.1.0" in capsys.readouterr().err) is updated

    def test_reasons_with_verbose(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a<2"])
        _ = lock()
        write(project, ["a.a>=2"])
        _ = capsys.readouterr()

        assert main(["-v", "lock"]) == 0

        # -v shows debug lines too; each change's reason is indented on the line after it.
        err = capsys.readouterr().err.splitlines()
        assert [(line, err[i + 1]) for i, line in enumerate(err) if line.startswith(("Updated ", "Added "))] == [
            ("Updated a.a v1.0.0 -> v2.0.0", "  a.a 1.0.0 doesn't meet a.a>=2 (from the project)"),
            ("Added b.b v1.1.0", "  b.b is needed by a.a 2.0.0"),
        ]

    def test_removed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a", "c.c"])
        _ = lock()
        write(project, ["a.a"])
        _ = capsys.readouterr()

        assert lock() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Removed c.c v1.0.0"]

    def test_warnings(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        index = make_index(tmp_path / "yanked", {"a.a": [v("1.0.0", yanked=True)]})
        write(project, ["a.a==1.0.0"])

        assert lock("--default-index", index) == 0

        assert "warning: a.a 1.0.0 is yanked\n" in capsys.readouterr().err

    def test_quiet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        assert main(["-q", "lock"]) == 0

        assert capsys.readouterr() == ("", "")

    def test_offline_uses_the_cache(self, project: Path, tmp_path: Path) -> None:
        # A file:// index isn't cached, so offline needs an http one; without a cached copy it fails.
        write(project, ["a.a"])

        assert lock("--offline", "--default-index", "https://index.example/") == EXIT_ERROR


class TestCheck:
    def test_missing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        assert lock("--check") == EXIT_STALE

        assert capsys.readouterr().err == "error: there's no alai.lock; run alai lock\n"

    def test_current(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])
        _ = lock()
        _ = capsys.readouterr()

        assert lock("--check") == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "alai.lock is up to date"]

    def test_upgrade(self, project: Path) -> None:
        # -U is allowed, unlike uv's: it passes while the lock already has the newest versions that fit.
        write(project, ["a.a", "b.b<1.1"])
        _ = lock()

        assert lock("--check", "-U") == 0

        write(project, ["a.a", "b.b"])
        _ = lock()
        before = (project / "alai.lock").read_bytes()

        assert lock("--check", "-U") == EXIT_STALE
        assert (project / "alai.lock").read_bytes() == before

    def test_manifest_changed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])
        _ = lock()
        write(project, ["a.a", "c.c"])
        before = (project / "alai.lock").read_bytes()
        _ = capsys.readouterr()

        assert lock("--check") == EXIT_STALE

        assert lines(capsys.readouterr().err) == [
            "Resolved 3 collections",
            "Added c.c v1.0.0",
            "alai.toml has changed since alai.lock was made:",
            "  project.collections: added c.c",
            "error: alai.lock is out of date; run alai lock",
        ]
        assert (project / "alai.lock").read_bytes() == before

    def test_inputs_changed_with_the_same_result(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # b.b is already locked through a.a, so listing it changes only the inputs, and only they say why.
        write(project, ["a.a"])
        _ = lock()
        write(project, ["a.a", "b.b"])
        _ = capsys.readouterr()

        assert lock("--check") == EXIT_STALE

        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "alai.toml has changed since alai.lock was made:",
            "  project.collections: added b.b",
            "error: alai.lock is out of date; run alai lock",
        ]

    def test_index_change_says_nothing_of_alai_toml(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write(project, ["b.b"])
        _ = lock()
        _ = make_index(tmp_path / "index", {**COLLECTIONS, "b.b": [v("1.0.0")]})
        _ = capsys.readouterr()

        assert lock("--check") == EXIT_STALE

        assert lines(capsys.readouterr().err) == [
            "Resolved 1 collection",
            "Updated b.b v1.1.0 -> v1.0.0",
            "error: alai.lock is out of date; run alai lock",
        ]

    def test_plain_lock_says_nothing_of_alai_toml(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Only a failing check explains itself; alai lock reports what it changed in the lock, as before.
        write(project, ["a.a"])
        _ = lock()
        write(project, ["a.a", "c.c"])
        _ = capsys.readouterr()

        assert lock() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 3 collections", "Added c.c v1.0.0"]

    def test_index_changed(self, project: Path, tmp_path: Path) -> None:
        # A new release doesn't make the lock stale, since locked versions are kept, but a removed one does.
        write(project, ["b.b"])
        _ = lock()
        _ = make_index(tmp_path / "index", {**COLLECTIONS, "b.b": [v("1.0.0"), v("1.1.0"), v("1.2.0")]})
        assert lock("--check") == 0
        _ = make_index(tmp_path / "index", {**COLLECTIONS, "b.b": [v("1.0.0")]})

        assert lock("--check") == EXIT_STALE

    def test_whitespace_isnt_a_change(self, project: Path) -> None:
        write(project, ["a.a>=1"])
        _ = lock()
        write(project, ["a.a >= 1.0.0"])

        assert lock("--check") == 0

    def test_index_no_longer_configured(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write(project, ["a.a"])
        _ = lock()
        other = make_index(tmp_path / "other", COLLECTIONS)
        _ = capsys.readouterr()

        assert lock("--check", "--default-index", other) == EXIT_STALE

        assert "Updated a.a v2.0.0, now from file://" in capsys.readouterr().err

    def test_resolves(self, project: Path, tmp_path: Path) -> None:
        write(project, ["a.a"])
        _ = lock()
        (tmp_path / "index" / "config.json").unlink()

        assert lock("--check") == EXIT_ERROR


class TestErrors:
    def test_no_project_file(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert lock() == EXIT_ERROR

        assert "there's no alai.toml in" in capsys.readouterr().err

    def test_resolution_failure(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a>=3"])

        assert lock() == EXIT_ERROR

        assert (
            capsys.readouterr().err
            == "error: no version of a.a meets every requirement on it:\n  a.a>=3 (from the project)\n"
        )
        assert not (project / "alai.lock").exists()

    def test_invalid_upgrade_collection(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        assert lock("-P", "nope") == EXIT_ERROR

        assert "--upgrade-collection: invalid collection name 'nope'" in capsys.readouterr().err

    def test_invalid_requirement(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a 1.0"])

        assert lock() == EXIT_ERROR

        assert "needs an operator" in capsys.readouterr().err

    @pytest.mark.parametrize("flag", ["--link-mode=copy", "--collections-dir=x"])
    def test_install_flags_not_taken(self, project: Path, flag: str) -> None:
        with pytest.raises(SystemExit) as e:
            _ = lock(flag)
        assert e.value.code == 2
