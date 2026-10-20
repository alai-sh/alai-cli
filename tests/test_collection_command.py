# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_sync_command import lines

from alai._cli import EXIT_ERROR, main

# The index (conftest's COLLECTIONS): a.a 1.0.0 and 2.0.0 (which needs b.b>=1.0.0), b.b 1.0.0 and 1.1.0, c.c and d.d.


def installed(cdir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for manifest in sorted((cdir / "ansible_collections").glob("*/*/MANIFEST.json")):
        info = json.loads(manifest.read_text())["collection_info"]
        out[f"{info['namespace']}.{info['name']}"] = info["version"]
    return out


def install(*args: str) -> int:
    return main(["collection", "install", *args])


def uninstall(*args: str) -> int:
    return main(["collection", "uninstall", *args])


@pytest.fixture
def cdir(project: Path) -> Path:
    """A project marked by an ansible.cfg, with no alai.toml, so collections go to its collections/."""
    _ = (project / "ansible.cfg").write_text("[defaults]\n")
    return project / "collections"


class TestWhere:
    def test_needs_a_project_or_a_directory(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("c.c") == EXIT_ERROR

        assert "no project found from" in capsys.readouterr().err
        assert not (project / "collections").exists()

    def test_explicit_directory(self, project: Path, tmp_path: Path) -> None:
        assert install("--collections-dir", str(tmp_path / "out"), "c.c") == 0

        assert installed(tmp_path / "out") == {"c.c": "1.0.0"}

    def test_environment_variable(self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAI_COLLECTIONS_DIR", str(tmp_path / "env"))

        assert install("c.c") == 0

        assert installed(tmp_path / "env") == {"c.c": "1.0.0"}

    def test_alai_project_files_untouched(self, project: Path) -> None:
        _ = (project / "alai.toml").write_text('[project]\ncollections = ["d.d"]\n')

        assert install("c.c") == 0

        assert installed(project / "collections") == {"c.c": "1.0.0"}
        assert (project / "alai.toml").read_text() == '[project]\ncollections = ["d.d"]\n'
        assert not (project / "alai.lock").exists()

    def test_ansible_cfg_project(self, cdir: Path) -> None:
        assert install("c.c") == 0

        assert installed(cdir) == {"c.c": "1.0.0"}


class TestInstall:
    def test_with_dependencies(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("a.a") == 0

        assert installed(cdir) == {"a.a": "2.0.0", "b.b": "1.1.0"}
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
        (tmp_path / "index" / "config.json").unlink()
        _ = capsys.readouterr()

        assert install("a.a") == 0

        assert lines(capsys.readouterr().err) == ["Checked 1 collection"]

    def test_installed_versions_kept(self, cdir: Path) -> None:
        _ = install("a.a", "b.b<1.1")

        assert install("a.a", "c.c") == 0

        # b.b 1.0.0 still fits a.a's b.b>=1.0.0, so it stays, as uv pip keeps what's installed.
        assert installed(cdir) == {"a.a": "2.0.0", "b.b": "1.0.0", "c.c": "1.0.0"}

    def test_requirement_replaces_an_installed_version(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("b.b")
        _ = capsys.readouterr()

        assert install("b.b<1.1") == 0

        assert installed(cdir) == {"b.b": "1.0.0"}
        assert " - b.b==1.1.0" in capsys.readouterr().err

    @pytest.mark.parametrize("args", [["-U"], ["-P", "b.b"]])
    def test_upgrade(self, cdir: Path, args: list[str]) -> None:
        _ = install("a.a", "b.b<1.1")

        assert install(*args, "a.a") == 0

        assert installed(cdir)["b.b"] == "1.1.0"

    def test_nothing_removed(self, cdir: Path) -> None:
        _ = install("c.c")

        assert install("d.d") == 0

        assert installed(cdir) == {"c.c": "1.0.0", "d.d": "1.0.0"}

    def test_no_version_fits(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("a.a>=3") == EXIT_ERROR

        assert "a.a>=3 (from the command line)" in capsys.readouterr().err

    def test_invalid(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert install("nope") == EXIT_ERROR

        assert "invalid collection name 'nope'" in capsys.readouterr().err


class TestUninstall:
    def test_only_what_is_named(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("a.a")
        _ = capsys.readouterr()

        assert uninstall("a.a") == 0

        assert installed(cdir) == {"b.b": "1.1.0"}
        assert lines(capsys.readouterr().err) == ["Uninstalled 1 collection", " - a.a==2.0.0"]

    def test_not_installed(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = install("c.c")
        _ = capsys.readouterr()

        assert uninstall("d.d", "c.c") == 0

        err = capsys.readouterr().err
        assert "warning: skipping d.d, which isn't installed in" in err
        assert " - c.c==1.0.0" in err

    def test_nothing_to_uninstall(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert uninstall("d.d") == 0

        assert "warning: no collections to uninstall" in capsys.readouterr().err

    def test_invalid(self, cdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert uninstall("c.c>=1") == EXIT_ERROR

        assert "invalid collection name 'c.c>=1'" in capsys.readouterr().err

    def test_needs_a_project_or_a_directory(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert uninstall("c.c") == EXIT_ERROR

        assert "no project found from" in capsys.readouterr().err

    def test_install_flags_not_taken(self, cdir: Path) -> None:
        with pytest.raises(SystemExit) as e:
            _ = uninstall("-U", "c.c")
        assert e.value.code == 2
