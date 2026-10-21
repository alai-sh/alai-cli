# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from alai._cli import EXIT_ERROR, main
from alai._commands.lock import EXIT_STALE


class Exec(Exception):
    """Raised in place of os.execve, which would replace the test run."""

    def __init__(self, path: str, argv: list[str], env: dict[str, str]) -> None:
        super().__init__(path)
        self.path = path
        self.argv = argv
        self.env = env


@pytest.fixture(autouse=True)
def no_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    def execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        raise Exec(path, argv, env)

    monkeypatch.setattr(os, "execve", execve)


def write(project: Path, collections: list[str]) -> None:
    _ = (project / "alai.toml").write_text(f"[project]\ncollections = {json.dumps(collections)}\n")


def run(*args: str) -> Exec:
    with pytest.raises(Exec) as e:
        _ = main(["run", *args])
    return e.value


def lines(err: str) -> list[str]:
    """The output with its timings dropped, which vary."""
    return [line.rsplit(" in ", 1)[0] if not line.startswith(" ") else line for line in err.splitlines()]


def installed(project: Path) -> list[str]:
    return sorted(f"{p.parent.name}.{p.name}" for p in (project / "collections/ansible_collections").glob("*/*"))


class TestRun:
    def test_syncs_then_runs(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        ran = run("sh", "-c", "true")

        assert ran.path == shutil.which("sh")
        assert ran.argv == ["sh", "-c", "true"]
        assert ran.env["ANSIBLE_COLLECTIONS_PATH"] == str(project / "collections")
        assert ran.env["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "False"
        assert ran.env["HOME"] == os.environ["HOME"]
        assert installed(project) == ["a.a", "b.b"]
        assert (project / "alai.lock").is_file()
        # As uv run syncs: only what changed, without each collection's line.
        assert lines(capsys.readouterr().err) == ["Prepared 2 collections", "Installed 2 collections"]

    def test_nothing_to_do_is_silent(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])
        _ = run("true")
        _ = capsys.readouterr()

        _ = run("true")

        assert capsys.readouterr().err == ""

    def test_verbose_lists_collections(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])

        _ = run("-v", "true")

        assert " + c.c==1.0.0" in lines(capsys.readouterr().err)

    def test_arguments_go_to_the_command(self, project: Path) -> None:
        write(project, [])

        ran = run("true", "--exact", "-v", "--", "--no-sync")

        assert ran.argv == ["true", "--exact", "-v", "--", "--no-sync"]

    def test_double_dash_before_the_command(self, project: Path) -> None:
        write(project, [])

        assert run("--", "true", "-h").argv == ["true", "-h"]

    def test_command_by_path(self, project: Path, tmp_path: Path) -> None:
        write(project, [])
        script = tmp_path / "script"
        _ = script.write_text("#!/bin/sh\n")
        script.chmod(0o755)

        assert run(str(script)).path == str(script)

    def test_warns_of_unlisted_collections(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])
        (project / "collections/ansible_collections/my/work").mkdir(parents=True)

        ran = run("true")

        assert installed(project) == ["c.c", "my.work"]
        assert ran.argv == ["true"]
        assert capsys.readouterr().err.splitlines()[-2:] == [
            "warning: Ansible can load collections alai.lock doesn't list here, from collections: my.work",
            "warning: alai run --exact or alai sync removes them",
        ]

    def test_warns_of_namespace_links(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, [])
        (tmp_path / "elsewhere/one").mkdir(parents=True)
        (project / "collections/ansible_collections").mkdir(parents=True)
        (project / "collections/ansible_collections/linked").symlink_to(tmp_path / "elsewhere")

        _ = run("true")

        assert "from collections: linked.* (a link)" in capsys.readouterr().err

    def test_inexact_keeps_them_quietly(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])
        (project / "collections/ansible_collections/my/work").mkdir(parents=True)

        _ = run("--inexact", "true")

        assert installed(project) == ["c.c", "my.work"]
        assert "warning" not in capsys.readouterr().err

    def test_exact_and_inexact(self, project: Path) -> None:
        write(project, [])

        with pytest.raises(SystemExit):
            _ = main(["run", "--exact", "--inexact", "true"])

    def test_exact_removes_unlisted_collections(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["c.c"])
        (project / "collections/ansible_collections/my/work").mkdir(parents=True)

        _ = run("--exact", "true")

        assert installed(project) == ["c.c"]
        assert "warning" not in capsys.readouterr().err

    def test_no_sync(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        ran = run("--no-sync", "true")

        assert ran.env["ANSIBLE_COLLECTIONS_PATH"] == str(project / "collections")
        assert not (project / "alai.lock").exists()
        assert not (project / "collections").exists()
        assert capsys.readouterr().err == ""

    def test_locked_without_a_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, ["a.a"])

        assert main(["run", "--locked", "true"]) == EXIT_STALE

        assert "there's no alai.lock, and --locked was given" in capsys.readouterr().err

    def test_no_sync_with_locked(self, project: Path) -> None:
        write(project, [])

        with pytest.raises(SystemExit):
            _ = main(["run", "--no-sync", "--locked", "true"])

    def test_needs_a_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["run", "true"]) == EXIT_ERROR

        assert "there's no alai.toml" in capsys.readouterr().err

    def test_needs_a_command(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            _ = main(["run"])

        assert "COMMAND" in capsys.readouterr().err

    def test_command_not_found(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, [])

        assert main(["run", "no-such-command-here"]) == EXIT_ERROR

        assert capsys.readouterr().err.splitlines()[-1] == "error: can't run no-such-command-here: it isn't on PATH"

    def test_no_such_file(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write(project, [])

        assert main(["run", "./missing"]) == EXIT_ERROR

        assert capsys.readouterr().err.splitlines()[-1] == "error: can't run ./missing: there's no such executable file"

    def test_exec_fails(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write(project, [])

        def execve(path: str, argv: list[str], env: dict[str, str]) -> None:
            raise OSError(8, "Exec format error")

        monkeypatch.setattr(os, "execve", execve)

        assert main(["run", "true"]) == EXIT_ERROR

        assert (
            capsys.readouterr().err.splitlines()[-1]
            == f"error: can't run true ({shutil.which('true')}): Exec format error"
        )


def test_replaces_alai(project: Path) -> None:
    """A real exec, in a process of its own: the command's output, exit code and environment are its own."""
    write(project, [])
    code = "import os, sys; print(os.environ['ANSIBLE_COLLECTIONS_PATH']); sys.exit(3)"

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alai", "run", sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert (result.returncode, result.stdout) == (3, f"{project / 'collections'}\n")
