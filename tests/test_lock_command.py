# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import COLLECTIONS
from helpers import lines, write_project
from index_data import make_index, v

from alai._cli import main
from alai._common import EXIT_ERROR, EXIT_STALE
from alai._index import IndexClient
from alai._native import toml_loads


def lock(*args: str) -> int:
    return main(["lock", *args])


class TestLock:
    def test_writes_the_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])

        assert lock() == 0

        out, err = capsys.readouterr()
        assert out == ""
        assert lines(err) == ["Resolved 2 collections"]
        text = (project / "alai.lock").read_text()
        assert 'name = "a.a"\nversion = "2.0.0"' in text
        assert '[project]\ncollections = ["a.a"]' in text

    def test_doesnt_connect_to_download_host(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Nothing is downloaded, so no connection is made for it.
        calls: list[str] = []
        monkeypatch.setattr(IndexClient, "preconnect_downloads", lambda self: calls.append(self.config().root))
        write_project(project, ["a.a"])

        assert lock() == 0
        assert calls == []

    def test_unchanged(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])
        _ = lock()
        before = (project / "alai.lock").stat()
        _ = capsys.readouterr()

        assert lock() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections"]
        # A rewrite renames a new file over it, which would change the inode.
        assert (project / "alai.lock").stat().st_ino == before.st_ino

    def test_changes_reported(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a<2", "b.b<1.1"])
        _ = lock()
        _ = capsys.readouterr()
        # a.a 1.0.0 no longer fits; b.b 1.0.0 still does (a.a 2.0.0 needs >=1.0.0), so it's kept.
        write_project(project, ["a.a>=2", "c.c"])

        assert lock() == 0

        assert lines(capsys.readouterr().err) == [
            "Resolved 3 collections",
            "Updated a.a v1.0.0 -> v2.0.0",
            "Added c.c v1.0.0",
        ]

    def test_locked_versions_kept(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a", "b.b<1.1"])
        _ = lock()
        write_project(project, ["a.a", "b.b"])
        _ = capsys.readouterr()

        assert lock() == 0

        # b.b 1.0.0 still fits, so it stays; only the requirement changed.
        assert lines(capsys.readouterr().err) == ["Resolved 2 collections"]
        assert 'name = "b.b"\nversion = "1.0.0"' in (project / "alai.lock").read_text()

    @pytest.mark.parametrize(
        ("args", "updated"), [(["-U"], True), (["--upgrade-collection", "b.b"], True), ([], False)]
    )
    def test_upgrade(self, project: Path, capsys: pytest.CaptureFixture[str], args: list[str], updated: bool) -> None:
        write_project(project, ["a.a", "b.b<1.1"])
        _ = lock()
        write_project(project, ["a.a", "b.b"])
        _ = capsys.readouterr()

        assert lock(*args) == 0

        assert ("Updated b.b v1.0.0 -> v1.1.0" in capsys.readouterr().err) is updated

    def test_reasons_with_verbose(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a<2"])
        _ = lock()
        write_project(project, ["a.a>=2"])
        _ = capsys.readouterr()

        assert main(["-v", "lock"]) == 0

        # -v shows debug lines too; each change's reason is indented on the line after it.
        err = capsys.readouterr().err.splitlines()
        assert [(line, err[i + 1]) for i, line in enumerate(err) if line.startswith(("Updated ", "Added "))] == [
            ("Updated a.a v1.0.0 -> v2.0.0", "  a.a 1.0.0 doesn't meet a.a>=2 (from the project)"),
            ("Added b.b v1.1.0", "  b.b is needed by a.a 2.0.0"),
        ]

    def test_removed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a", "c.c"])
        _ = lock()
        write_project(project, ["a.a"])
        _ = capsys.readouterr()

        assert lock() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Removed c.c v1.0.0"]

    def test_warnings(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        index = make_index(tmp_path / "yanked", {"a.a": [v("1.0.0", yanked=True)]})
        write_project(project, ["a.a==1.0.0"])

        assert lock("--default-index", index) == 0

        assert "warning: a.a 1.0.0 is yanked\n" in capsys.readouterr().err

    def test_index_without_listing(self, project: Path, tmp_path: Path) -> None:
        # An index of index files alone, with no collections.json.
        index = make_index(
            tmp_path / "plain", {"a.a": [v("1.0.0", {"b.b": "*"})], "b.b": [v("1.0.0")]}, hashed=False, listing=False
        )
        write_project(project, ["a.a"])

        assert lock("--default-index", index) == 0

        text = (project / "alai.lock").read_text()
        assert 'name = "a.a"\nversion = "1.0.0"' in text
        assert 'name = "b.b"\nversion = "1.0.0"' in text

    def test_missing_from_index_without_listing(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        index = make_index(tmp_path / "plain", {"a.a": [v("1.0.0")]}, hashed=False, listing=False)
        write_project(project, ["z.z"])

        assert lock("--default-index", index) == EXIT_ERROR

        assert "no configured index ('alai') has z.z" in capsys.readouterr().err

    def test_quiet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])

        assert main(["-q", "lock"]) == 0

        assert capsys.readouterr() == ("", "")

    def test_offline_uses_the_cache(self, project: Path, tmp_path: Path) -> None:
        # A file:// index isn't cached, so offline needs an http one; without a cached copy it fails.
        write_project(project, ["a.a"])

        assert lock("--offline", "--default-index", "https://index.example/") == EXIT_ERROR


class TestCheck:
    def test_missing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])

        assert lock("--check") == EXIT_STALE

        assert capsys.readouterr().err == "error: there's no alai.lock; run alai lock\n"

    def test_current(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])
        _ = lock()
        _ = capsys.readouterr()

        assert lock("--check") == 0

        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "alai.lock is up to date"]

    def test_upgrade(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # -U is allowed, unlike uv's: it passes while the lock already has the newest versions that fit.
        write_project(project, ["a.a", "b.b<1.1"])
        _ = lock()

        assert lock("--check", "-U") == 0

        write_project(project, ["a.a", "b.b"])
        _ = lock()
        before = (project / "alai.lock").read_bytes()
        _ = capsys.readouterr()

        assert lock("--check", "-U") == EXIT_STALE
        assert (project / "alai.lock").read_bytes() == before
        # The command it suggests is the one that writes what was found: plain alai lock would keep b.b.
        assert lines(capsys.readouterr().err)[-1] == "error: alai.lock is out of date; run alai lock --upgrade"

        assert lock("--check", "-P", "b.b") == EXIT_STALE
        assert lines(capsys.readouterr().err)[-1] == (
            "error: alai.lock is out of date; run alai lock --upgrade-collection b.b"
        )

    def test_manifest_changed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])
        _ = lock()
        write_project(project, ["a.a", "c.c"])
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
        write_project(project, ["a.a"])
        _ = lock()
        write_project(project, ["a.a", "b.b"])
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
        write_project(project, ["b.b"])
        _ = lock()
        _ = make_index(tmp_path / "index", {**COLLECTIONS, "b.b": [v("1.0.0")]}, artifacts=True)
        _ = capsys.readouterr()

        assert lock("--check") == EXIT_STALE

        assert lines(capsys.readouterr().err) == [
            "Resolved 1 collection",
            "Updated b.b v1.1.0 -> v1.0.0",
            "error: alai.lock is out of date; run alai lock",
        ]

    def test_plain_lock_says_nothing_of_alai_toml(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Only a failing check explains itself; alai lock reports what it changed in the lock, as before.
        write_project(project, ["a.a"])
        _ = lock()
        write_project(project, ["a.a", "c.c"])
        _ = capsys.readouterr()

        assert lock() == 0

        assert lines(capsys.readouterr().err) == ["Resolved 3 collections", "Added c.c v1.0.0"]

    def test_index_changed(self, project: Path, tmp_path: Path) -> None:
        # A new release doesn't make the lock stale, since locked versions are kept, but a removed one does.
        write_project(project, ["b.b"])
        _ = lock()
        _ = make_index(tmp_path / "index", {**COLLECTIONS, "b.b": [v("1.0.0"), v("1.1.0"), v("1.2.0")]}, artifacts=True)
        assert lock("--check") == 0
        _ = make_index(tmp_path / "index", {**COLLECTIONS, "b.b": [v("1.0.0")]}, artifacts=True)

        assert lock("--check") == EXIT_STALE

    def test_whitespace_isnt_a_change(self, project: Path) -> None:
        write_project(project, ["a.a>=1"])
        _ = lock()
        write_project(project, ["a.a >= 1.0.0"])

        assert lock("--check") == 0

    def test_index_no_longer_configured(
        self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_project(project, ["a.a"])
        _ = lock()
        other = make_index(tmp_path / "other", COLLECTIONS)
        _ = capsys.readouterr()

        assert lock("--check", "--default-index", other) == EXIT_STALE

        assert "Updated a.a v2.0.0, now from file://" in capsys.readouterr().err

    def test_resolves(self, project: Path, tmp_path: Path) -> None:
        write_project(project, ["a.a"])
        _ = lock()
        (tmp_path / "index" / "config.json").unlink()

        assert lock("--check") == EXIT_ERROR


def versions(project: Path) -> dict[str, str]:
    """What alai.lock locks, by name."""
    table = toml_loads((project / "alai.lock").read_text())
    return {c["name"]: c["version"] for c in table["collection"]}


class TestUpgradeGroup:
    def locked_old(self, project: Path) -> None:
        """a.a 2.0.0 in group test, with its dependency b.b at 1.0.0, and b.b in group lint too, both locked old
        while alai.toml lets b.b have 1.1.0, so only an upgrade moves it."""
        write_project(
            project,
            [],
            extra='[project.groups.test]\ncollections = ["a.a"]\n[project.groups.lint]\ncollections = ["b.b<1.1"]\n',
        )
        assert lock() == 0
        write_project(
            project,
            [],
            extra='[project.groups.test]\ncollections = ["a.a"]\n[project.groups.lint]\ncollections = ["b.b"]\n',
        )
        assert lock() == 0
        assert versions(project) == {"a.a": "2.0.0", "b.b": "1.0.0"}

    def test_upgrades_what_the_group_lists(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.locked_old(project)
        _ = capsys.readouterr()

        assert lock("--upgrade-group", "lint") == 0

        assert versions(project) == {"a.a": "2.0.0", "b.b": "1.1.0"}
        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Updated b.b v1.0.0 -> v1.1.0"]

    def test_not_what_they_depend_on(self, project: Path) -> None:
        # As uv's --upgrade-group: test lists a.a, which needs b.b, and b.b isn't upgraded for it.
        self.locked_old(project)

        assert lock("--upgrade-group", "test") == 0

        assert versions(project)["b.b"] == "1.0.0"

    def test_with_upgrade_collection(self, project: Path) -> None:
        write_project(project, ["c.c"], extra='[project.groups.test]\ncollections = ["a.a<2"]\n')
        _ = lock()
        write_project(project, ["c.c"], extra='[project.groups.test]\ncollections = ["a.a"]\n')

        assert lock("--upgrade-group", "test", "-P", "c.c") == 0

        assert versions(project) == {"a.a": "2.0.0", "b.b": "1.1.0", "c.c": "1.0.0"}

    def test_check(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.locked_old(project)

        assert lock("--check", "--upgrade-group", "test") == 0
        _ = capsys.readouterr()
        assert lock("--check", "--upgrade-group", "lint") == EXIT_STALE
        assert (
            lines(capsys.readouterr().err)[-1] == "error: alai.lock is out of date; run alai lock --upgrade-group lint"
        )

    @pytest.mark.parametrize(
        ("groups", "message"),
        [
            (
                "[project.groups.test]\ncollections = []\n",
                "--upgrade-group: alai.toml has no group 'nope' (it has 'test')",
            ),
            ("", "--upgrade-group: alai.toml has no group 'nope'"),
        ],
    )
    def test_unknown_group(self, project: Path, capsys: pytest.CaptureFixture[str], groups: str, message: str) -> None:
        write_project(project, ["c.c"], extra=groups)

        assert lock("--upgrade-group", "nope") == EXIT_ERROR

        assert capsys.readouterr().err == f"error: {message}\n"
        assert not (project / "alai.lock").exists()


class TestErrors:
    def test_no_project_file(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert lock() == EXIT_ERROR

        assert "no alai.toml found in" in capsys.readouterr().err

    def test_resolution_failure(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a>=3"])

        assert lock() == EXIT_ERROR

        assert (
            capsys.readouterr().err == "error: no solution found when resolving collections:\n"
            "  Because only a.a==1.0.0 and a.a==2.0.0 are available and the project depends on a.a>=3.0.0, we can "
            "conclude that the project's requirements are unsatisfiable.\n"
        )
        assert not (project / "alai.lock").exists()

    def test_invalid_upgrade_collection(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])

        assert lock("-P", "nope") == EXIT_ERROR

        assert "--upgrade-collection: invalid collection name 'nope'" in capsys.readouterr().err

    def test_invalid_requirement(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a 1.0"])

        assert lock() == EXIT_ERROR

        assert "needs an operator" in capsys.readouterr().err

    @pytest.mark.parametrize("flag", ["--link-mode=copy", "--collections-dir=x"])
    def test_install_flags_not_taken(self, project: Path, flag: str) -> None:
        with pytest.raises(SystemExit) as e:
            _ = lock(flag)
        assert e.value.code == 2
