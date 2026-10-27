# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import edit, edited, installed_versions, lines

from alai._cli import main
from alai._common import EXIT_ERROR

# The index (conftest's COLLECTIONS): a.a 1.0.0 and 2.0.0 (which needs b.b>=1.0.0), b.b 1.0.0 and 1.1.0, c.c and d.d.


def install(*args: str) -> int:
    return main(["collection", "install", *args])


def uninstall(*args: str) -> int:
    return main(["collection", "uninstall", *args])


@pytest.fixture
def cdir(project: Path) -> Path:
    """A project with an empty alai.toml, so collections go to its collections/."""
    _ = (project / "alai.toml").write_text("")
    return project / "collections"


class TestWhere:
    def test_needs_a_project_or_a_directory(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("c.c") == EXIT_ERROR

        assert "no alai.toml found in" in capsys.readouterr().err
        assert not (project / "collections").exists()

    def test_error_shortens_home(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        assert install("c.c") == EXIT_ERROR

        assert "no alai.toml found in ~/project or any directory above it" in capsys.readouterr().err

    def test_explicit_directory(self, project: Path, tmp_path: Path) -> None:
        assert install("--collections-dir", str(tmp_path / "out"), "c.c") == 0

        assert installed_versions(tmp_path / "out") == {"c.c": "1.0.0"}

    def test_default_directory_not_named(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # collections/ in the current directory is the one a user would assume, as uv pip names no ./.venv.
        assert install("c.c") == 0

        assert "Using collections directory" not in capsys.readouterr().err

    def test_other_directory_named(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("--collections-dir", "out", "c.c") == 0

        assert lines(capsys.readouterr().err)[0] == "Using collections directory at: out"

    def test_directory_found_above_named(
        self, cdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        (cdir.parent / "playbooks").mkdir()
        monkeypatch.chdir(cdir.parent / "playbooks")

        assert install("c.c") == 0

        assert installed_versions(cdir) == {"c.c": "1.0.0"}
        assert lines(capsys.readouterr().err)[0] == "Using collections directory at: ~/project/collections"

    def test_named_when_already_installed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("--collections-dir", "out", "c.c")
        _ = capsys.readouterr()

        assert install("--collections-dir", "out", "c.c") == 0

        assert lines(capsys.readouterr().err) == ["Using collections directory at: out", "Checked 1 collection"]

    def test_quiet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("-q", "--collections-dir", "out", "c.c") == 0

        assert capsys.readouterr().err == ""

    def test_environment_variable(self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAI_COLLECTIONS_DIR", str(tmp_path / "env"))

        assert install("c.c") == 0

        assert installed_versions(tmp_path / "env") == {"c.c": "1.0.0"}

    def test_alai_project_files_untouched(self, project: Path) -> None:
        _ = (project / "alai.toml").write_text('[project]\ncollections = ["d.d"]\n')

        assert install("c.c") == 0

        assert installed_versions(project / "collections") == {"c.c": "1.0.0"}
        assert (project / "alai.toml").read_text() == '[project]\ncollections = ["d.d"]\n'
        assert not (project / "alai.lock").exists()

    def test_ansible_cfg_not_a_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "ansible.cfg").write_text("[defaults]\n")

        assert install("c.c") == EXIT_ERROR

        assert (
            f"hint: {project} has an ansible.cfg. If it's the project's root, create an empty alai.toml there"
            in capsys.readouterr().err
        )
        assert not (project / "collections").exists()


class TestInstall:
    def test_with_dependencies(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("a.a") == 0

        assert installed_versions(cdir) == {"a.a": "2.0.0", "b.b": "1.1.0"}
        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "Prepared 2 collections",
            "Installed 2 collections",
            " + a.a==2.0.0",
            " + b.b==1.1.0",
        ]

    def test_already_there_resolves_nothing(
        self, cdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _ = install("a.a")
        edit(cdir, "a.a", "b.b")
        (tmp_path / "index" / "config.json").unlink()
        _ = capsys.readouterr()

        assert install("a.a") == 0

        assert lines(capsys.readouterr().err) == ["Checked 1 collection"]
        assert edited(cdir) == {"a.a", "b.b"}

    def test_installed_versions_kept(self, cdir: Path) -> None:
        _ = install("a.a", "b.b<1.1")

        assert install("a.a", "c.c") == 0

        # b.b 1.0.0 still fits a.a's b.b>=1.0.0, so it stays, as uv pip keeps what's installed.
        assert installed_versions(cdir) == {"a.a": "2.0.0", "b.b": "1.0.0", "c.c": "1.0.0"}

    def test_requirement_replaces_an_installed_version(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("b.b")
        _ = capsys.readouterr()

        assert install("b.b<1.1") == 0

        assert installed_versions(cdir) == {"b.b": "1.0.0"}
        assert " - b.b==1.1.0" in capsys.readouterr().err

    @pytest.mark.parametrize("args", [["-U"], ["-P", "b.b"]])
    def test_upgrade(self, cdir: Path, args: list[str]) -> None:
        _ = install("a.a", "b.b<1.1")

        assert install(*args, "a.a") == 0

        assert installed_versions(cdir)["b.b"] == "1.1.0"

    def test_nothing_removed(self, cdir: Path) -> None:
        _ = install("c.c")

        assert install("d.d") == 0

        assert installed_versions(cdir) == {"c.c": "1.0.0", "d.d": "1.0.0"}

    def test_reinstall(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("a.a", "c.c")
        edit(cdir, "a.a", "b.b", "c.c")
        _ = capsys.readouterr()

        assert install("--reinstall", "a.a") == 0

        # Resolved, not just checked, and what a.a needs is reinstalled too, as uv pip install --reinstall does.
        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "Uninstalled 2 collections",
            "Installed 2 collections",
            " ~ a.a==2.0.0",
            " ~ b.b==1.1.0",
        ]
        # c.c wasn't asked for, so it's left as it is.
        assert edited(cdir) == {"c.c"}
        assert installed_versions(cdir) == {"a.a": "2.0.0", "b.b": "1.1.0", "c.c": "1.0.0"}

    def test_reinstall_collection(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("a.a", "c.c")
        edit(cdir, "a.a", "b.b", "c.c")
        _ = capsys.readouterr()

        assert install("--reinstall-collection", "b.b", "a.a") == 0

        assert lines(capsys.readouterr().err)[-2:] == ["Installed 1 collection", " ~ b.b==1.1.0"]
        assert edited(cdir) == {"a.a", "c.c"}

    def test_upgrade_group_not_taken(self, cdir: Path) -> None:
        # It never reads the project's groups, so there's nothing for it to name.
        with pytest.raises(SystemExit) as e:
            _ = install("--upgrade-group", "dev", "c.c")

        assert e.value.code == 2

    def test_no_version_fits(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("a.a>=3") == EXIT_ERROR

        assert (
            "you require a.a>=3.0.0, we can conclude that your requirements are unsatisfiable"
            in capsys.readouterr().err
        )
        assert not cdir.exists()

    def test_invalid(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("nope") == EXIT_ERROR

        assert "invalid collection name 'nope'" in capsys.readouterr().err


def checkout(cdir: Path, name: str) -> Path:
    """A source checkout at ``name``'s place in ``cdir``: a galaxy.yml and no MANIFEST.json, so no version alai reads."""
    namespace, short = name.split(".")
    path = cdir / "ansible_collections" / namespace / short
    path.mkdir(parents=True)
    _ = (path / "galaxy.yml").write_text(f"namespace: {namespace}\nname: {short}\nversion: 1.0.0\n")
    return path


class TestUnversioned:
    def test_replaced_by_default(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = checkout(cdir, "c.c")

        assert install("c.c") == 0

        assert installed_versions(cdir) == {"c.c": "1.0.0"}
        assert not (path / "galaxy.yml").exists()
        assert lines(capsys.readouterr().err)[-2:] == [" - c.c (unknown version)", " + c.c==1.0.0"]

    def test_kept_when_asked_for(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = checkout(cdir, "c.c")

        assert install("--keep-unversioned", "c.c>=9") == 0

        assert (path / "galaxy.yml").exists()
        assert lines(capsys.readouterr().err) == ["Checked 1 collection"]

    def test_kept_as_a_dependency(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = checkout(cdir, "b.b")

        assert install("--keep-unversioned", "a.a") == 0

        assert installed_versions(cdir) == {"a.a": "2.0.0"}
        assert (path / "galaxy.yml").exists()
        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "Prepared 1 collection",
            "Installed 1 collection",
            " + a.a==2.0.0",
        ]

    def test_others_still_installed(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # x.x is on no index: a kept collection isn't looked for.
        path = checkout(cdir, "x.x")

        assert install("--keep-unversioned", "x.x", "d.d") == 0

        assert installed_versions(cdir) == {"d.d": "1.0.0"}
        assert (path / "galaxy.yml").exists()
        assert lines(capsys.readouterr().err)[-1] == " + d.d==1.0.0"

    @pytest.mark.parametrize("flag", [["--reinstall"], ["--reinstall-collection", "c.c"]])
    def test_not_with_reinstall(self, cdir: Path, capsys: pytest.CaptureFixture[str], flag: list[str]) -> None:
        path = checkout(cdir, "c.c")

        with pytest.raises(SystemExit) as e:
            _ = install("--keep-unversioned", *flag, "c.c")

        assert e.value.code == EXIT_ERROR
        assert "--keep-unversioned can't be used with --reinstall" in capsys.readouterr().err
        assert (path / "galaxy.yml").exists()


class TestUninstall:
    def test_only_what_is_named(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("a.a")
        _ = capsys.readouterr()

        assert uninstall("a.a") == 0

        assert installed_versions(cdir) == {"b.b": "1.1.0"}
        assert lines(capsys.readouterr().err) == ["Uninstalled 1 collection", " - a.a==2.0.0"]

    def test_not_installed(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("c.c")
        _ = capsys.readouterr()

        assert uninstall("d.d", "c.c") == 0

        err = capsys.readouterr().err
        assert "warning: skipping d.d, which isn't installed in collections" in err
        assert " - c.c==1.0.0" in err
        assert installed_versions(cdir) == {}

    def test_other_directory_named(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("--collections-dir", "out", "c.c")
        _ = capsys.readouterr()

        assert uninstall("--collections-dir", "out", "c.c") == 0

        assert lines(capsys.readouterr().err) == [
            "Using collections directory at: out",
            "Uninstalled 1 collection",
            " - c.c==1.0.0",
        ]
        assert installed_versions(project / "out") == {}

    def test_nothing_to_uninstall(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert uninstall("d.d") == 0

        assert "warning: no collections to uninstall" in capsys.readouterr().err

    def test_invalid(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert uninstall("c.c>=1") == EXIT_ERROR

        assert "invalid collection name 'c.c>=1'" in capsys.readouterr().err

    def test_needs_a_project_or_a_directory(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert uninstall("c.c") == EXIT_ERROR

        assert "no alai.toml found in" in capsys.readouterr().err

    def test_install_flags_not_taken(self, cdir: Path) -> None:
        with pytest.raises(SystemExit) as e:
            _ = uninstall("-U", "c.c")
        assert e.value.code == 2
