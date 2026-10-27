# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from helpers import write_project

from alai._cli import main
from alai._common import EXIT_ERROR
from alai._environ import render


class TestEnv:
    def test_prints_exports(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SHELL", "/bin/bash")
        write_project(project, ["c.c"])
        assert main(["sync", "-q"]) == 0

        assert main(["env"]) == 0

        out, err = capsys.readouterr()
        assert out == (
            f"export ANSIBLE_COLLECTIONS_PATH={project / 'collections'}\nexport ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False\n"
        )
        assert err == ""

    def test_fish_from_shell(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        write_project(project, [])

        assert main(["env", "-q"]) == 0

        assert capsys.readouterr().out.splitlines()[1] == "set -gx ANSIBLE_COLLECTIONS_SCAN_SYS_PATH False"

    def test_output_format(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        write_project(project, [])

        assert main(["env", "-q", "--output-format", "dotenv"]) == 0

        assert capsys.readouterr().out.splitlines()[1] == "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False"

    def test_no_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["c.c"])

        assert main(["env"]) == 0

        out, err = capsys.readouterr()
        assert "ANSIBLE_COLLECTIONS_PATH" in out
        assert err == "warning: there's no alai.lock, so nothing is installed for Ansible to load; run alai sync\n"
        assert not (project / "collections").exists()

    def test_stale_lock(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["c.c"])
        assert main(["lock", "-q"]) == 0
        write_project(project, ["c.c", "d.d"])

        assert main(["env"]) == 0

        assert capsys.readouterr().err == (
            "warning: alai.lock is out of date (alai.toml asks for something else than when it was locked); run alai "
            + "sync\n"
        )

    def test_not_installed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, ["a.a"])
        assert main(["sync", "-q"]) == 0
        shutil.rmtree(project / "collections/ansible_collections/b")

        assert main(["env"]) == 0

        assert capsys.readouterr().err == "warning: 1 collection from alai.lock not installed; run alai sync\n"

    def test_unlisted(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_project(project, [])
        assert main(["sync", "-q"]) == 0
        (project / "collections/ansible_collections/my/work").mkdir(parents=True)

        assert main(["env"]) == 0

        assert capsys.readouterr().err.splitlines() == [
            "warning: Ansible can load collections alai.lock doesn't list here, from collections: my.work",
            "warning: alai sync removes them",
        ]

    def test_needs_a_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["env"]) == EXIT_ERROR

        assert "no alai.toml found in" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("output_format", "expected"),
    [
        ("sh", "export A='/a b/it'\"'\"'s'\n"),
        ("fish", "set -gx A '/a b/it\\'s'\n"),
        ("pwsh", "$env:A = '/a b/it''s'\n"),
        ("dotenv", 'A="/a b/it\'s"\n'),
    ],
)
def test_quoting(output_format: str, expected: str) -> None:
    assert render({"A": "/a b/it's"}, output_format) == expected


def test_powershell_unicode_quotes() -> None:
    # PowerShell ends a single-quoted string at any of these, so each is doubled, as it escapes them itself.
    value = "a'b\u2018c\u2019d\u201ae\u201bf"

    assert render({"A": value}, "pwsh") == "$env:A = 'a''b\u2018\u2018c\u2019\u2019d\u201a\u201ae\u201b\u201bf'\n"


@pytest.mark.parametrize(
    ("output_format", "expected"),
    [
        ("sh", "export A=/a/b-c_d.e\n"),
        ("fish", "set -gx A /a/b-c_d.e\n"),
        ("pwsh", "$env:A = '/a/b-c_d.e'\n"),
        ("dotenv", "A=/a/b-c_d.e\n"),
    ],
)
def test_plain_values_unquoted(output_format: str, expected: str) -> None:
    assert render({"A": "/a/b-c_d.e"}, output_format) == expected
