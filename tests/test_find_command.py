# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import json
import re
import sys
from pathlib import Path

import pytest
from index_data import make_index, plugin
from index_server import IndexServer
from python_index import make_python_index

from alai import _find
from alai._cli import main
from alai._common import EXIT_ERROR, EXIT_NOT_FOUND
from alai._find import as_sentence, markup, plain

PLUGINS = [
    plugin("ansible.builtin.copy", short_description="Copy files to remote locations"),
    plugin("ansible.builtin.docker_container", routing={"redirect": "community.docker.docker_container"}),
    plugin("community.docker.docker_container", since="1.0.0", short_description="manage Docker containers"),
    plugin(
        "community.general.docker_container",
        since="0.1.0",
        until="2.0.0",
        short_description="manage docker containers",
        routing={"redirect": "community.docker.docker_container"},
    ),
    plugin("community.general.copy_thing", since="1.0.0", short_description="Copies things"),
    plugin("community.general.to_seconds", "filter", since="1.0.0", short_description="Duration to seconds"),
    plugin("community.general.new_thing", since="3.0.0", short_description="Something new"),
    plugin(
        "community.general.old_thing",
        since="1.0.0",
        until="2.0.0",
        routing={"tombstone": {"removal_version": "2.0.0", "warning_text": "Use community.general.new_thing."}},
    ),
    plugin("community.general.going", since="1.0.0", routing={"deprecation": {"removal_version": "4.0.0"}}),
    plugin("community.general.loop", routing={"redirect": "community.general.loop"}),
    *[plugin(f"zz{i}.copy.copy", short_description=f"copy {i}") for i in range(12)],
    plugin("fork.general.to_seconds", "filter", since="1.0.0", short_description="Duration to seconds"),
    plugin("fork.general.copy_thing", since="1.0.0"),
    plugin("fork.general.new_thing", since="1.0.0"),
    plugin("eda.builtin.range", "event_source", short_description="Generate events with an increasing index"),
]


@pytest.fixture
def project(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _c.Iterator[Path]:
    """conftest's project, with a plugin index in its index, a Python index whose ansible-core and ansible-rulebook
    hold ``PLUGINS``' builtin lines, and no alai.toml but its collections/ given as the collections directory, with
    community.general 2.5.0 installed. The terminal is 80 columns wide."""
    monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "plugins", {"a.a": []}, plugins=PLUGINS))
    make_python_index(tmp_path / "pypi", PLUGINS)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("ALAI_COLLECTIONS_DIR", "collections")
    install(project, "community.general", "2.5.0")
    with IndexServer(tmp_path / "pypi") as pypi:
        monkeypatch.setenv("ALAI_PYTHON_INDEX", pypi.url)
        yield project


def install(project: Path, name: str, version: str) -> None:
    namespace, short = name.split(".")
    path = project / "collections" / "ansible_collections" / namespace / short
    path.mkdir(parents=True)
    info = {"namespace": namespace, "name": short, "version": version}
    _ = (path / "MANIFEST.json").write_text(json.dumps({"collection_info": info}))


def general(project: Path) -> Path:
    """Where the fixture's community.general is installed, as alai finds it: through the project's real path."""
    return project.resolve() / "collections" / "ansible_collections" / "community" / "general"


def find(*args: str) -> int:
    return main(["find", *args])


def out(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line.rstrip() for line in capsys.readouterr().out.splitlines()]


def section(title: str) -> str:
    """A section's heading, as wide as the 80-column terminal."""
    return f"── {title} ".ljust(80, "─")


class TestListing:
    def test_installed_first(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("docker_container") == 0

        assert out(capsys) == [
            section("Installed (2)"),
            " ↪ module  ansible.builtin.docker_container    → community.docker.docker_container; community.docker isn't "
            + "installed",
            " ↪ module  community.general.docker_container  removed in 2.0.0; → community.docker.docker_container; "
            + "community.docker isn't installed",
            "",
            section("Not installed (1)"),
            " · module  community.docker.docker_container   manage Docker containers",
        ]

    def test_redirect_target_installed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        install(project, "community.docker", "1.0.0")

        assert find("docker_container", "--installed") == 0

        assert out(capsys)[1:3] == [
            " ✓ module  community.docker.docker_container   manage Docker containers",
            " ↪ module  ansible.builtin.docker_container    → community.docker.docker_container",
        ]

    def test_legend_tells_redirects_apart(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("COLUMNS", "200")

        assert find("--color", "never", "docker_container") == 0

        assert out(capsys)[-1] == "· not installed  ↪ redirects to a collection that isn't installed"

    def test_partial_match(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "--limit", "5") == 0

        lines = out(capsys)
        assert lines[:3] == [
            section("Installed (2)"),
            " ✓ module  ansible.builtin.copy          Copy files to remote locations",
            " ✓ module  community.general.copy_thing  Copies things",
        ]
        # The exact matches first; the partial match in a probable copy is among those left out.
        assert lines[5:] == [
            " · module  zz0.copy.copy                 copy 0",
            " · module  zz1.copy.copy                 copy 1",
            " · module  zz10.copy.copy                copy 10",
            "   … 10 more, --limit 0 to show all",
        ]
        assert lines[4] == section("Not installed (13)")

    def test_limit_counts_installed_first(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "--limit", "1") == 0

        assert out(capsys) == [
            section("Installed (2)"),
            " ✓ module  ansible.builtin.copy  Copy files to remote locations",
            "   … 1 more",
            "",
            section("Not installed (13)"),
            "   … 13 more, --limit 0 to show all",
        ]

    def test_limit_with_installed_only(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "--installed", "--limit", "1") == 0

        assert out(capsys)[1:] == [
            " ✓ module  ansible.builtin.copy  Copy files to remote locations",
            "   … 1 more, --limit 0 to show all",
        ]

    def test_default_limit_on_a_terminal(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        # Lower than the 13 matches, so some are left out.
        monkeypatch.setattr(_find, "DEFAULT_LIMIT", 10)

        assert find("-q", "--color", "never", "copy", "-x") == 0

        lines = out(capsys)
        # ansible.builtin.copy is installed, and one of the 10.
        assert sum(1 for line in lines if line.startswith(" · ")) == 9
        assert lines[-1] == "   … 3 more, --limit 0 to show all"

    def test_no_default_limit_when_piped(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "-x") == 0

        lines = out(capsys)
        assert sum(1 for line in lines if line.startswith(" · ")) == 12
        assert not any("more, --limit" in line for line in lines)

    def test_limit_zero_shows_all(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "-x", "--limit", "0") == 0

        assert sum(1 for line in out(capsys) if line.startswith(" · ")) == 12

    def test_exact(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "-x", "--installed") == 0

        assert out(capsys)[1:] == [" ✓ module  ansible.builtin.copy  Copy files to remote locations"]

    def test_type(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("seconds", "-t", "filter") == 0

        assert out(capsys) == [
            section("Installed (1)"),
            " ✓ filter  community.general.to_seconds  Duration to seconds",
            "",
            section("Not installed (1)"),
            " · filter  fork.general.to_seconds       Duration to seconds",
        ]

    def test_glob(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.*_thing") == 0

        assert out(capsys) == [
            section("Installed (2)"),
            " ✓ module  community.general.copy_thing  Copies things",
            " ↑ module  community.general.new_thing   added in 3.0.0, installed 2.5.0",
        ]

    def test_partial_match_leaves_out_removed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("old_thin") == EXIT_NOT_FOUND
        assert find("old_thin", "--include-removed") == 0

        assert out(capsys)[1] == " ✗ module  community.general.old_thing  removed in 2.0.0"

    def test_exact_name_keeps_removed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("old_thing") == 0

        assert out(capsys)[1] == " ✗ module  community.general.old_thing  removed in 2.0.0"

    def test_states(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.*", "--include-removed") == 0

        lines = [" ".join(line.split()) for line in out(capsys)]
        assert "⚠ module community.general.going deprecated, removal in 4.0.0" in lines
        assert "✗ module community.general.loop broken redirect: redirects to itself" in lines

    def test_no_project(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.delenv("ALAI_COLLECTIONS_DIR")

        assert find("copy_thing") == 0

        captured = capsys.readouterr()
        assert captured.err == "No project found, so only ansible.builtin and eda.builtin count as installed\n"
        assert captured.out.splitlines()[0].startswith("── Not installed (2) ")

    def test_no_project_installed_with(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The builtin collections are installed with their tools, which the section names.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.delenv("ALAI_COLLECTIONS_DIR")

        assert find("-q", "range") == 0
        assert out(capsys)[0] == section("Installed with ansible-rulebook (1)")
        assert find("-q", "copy") == 0
        assert out(capsys)[0] == section("Installed with ansible-core (1)")
        assert find("-q", "*") == 0
        assert out(capsys)[0] == section("Installed with ansible-core and ansible-rulebook (3)")

    def test_legend_on_a_terminal(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("COLUMNS", "200")

        assert find("--color", "never", "to_seconds") == 0

        assert out(capsys)[-1] == "✓ installed  · not installed  gray probably a copy of another collection"

    def test_no_legend_with_quiet(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert find("-q", "--color", "never", "to_seconds") == 0

        assert "installed" not in out(capsys)[-1]

    def test_terminal_cuts_long_lines(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("COLUMNS", "60")

        assert find("--color", "never", "community.docker.*") == 0

        assert out(capsys)[1] == " · module  community.docker.docker_container  manage Dock…"

    def test_long_name_pads_no_one_else(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        long = "a" * 60
        lines = [plugin("b.b.thing", short_description="short"), plugin(f"c.c.{long}_thing", short_description="long")]
        monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "long", {"a.a": []}, plugins=lines))

        assert find("thing") == 0

        assert out(capsys)[1:] == [
            " · module  b.b.thing" + " " * 51 + "  short",
            f" · module  c.c.{long}_thing  long",
        ]

    def test_colour(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("--color", "always", "seconds", "--installed") == 0

        line = out(capsys)[1]
        assert "\x1b[34mcommunity.general.\x1b[0m" in line
        assert "\x1b[1mto_\x1b[0m\x1b[1;4mseconds\x1b[0m" in line


class TestDetail:
    def test_redirect(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.docker_container") == 0

        assert out(capsys) == [
            "╭─ community.general.docker_container " + "─" * 32 + " module ─╮",
            "  Manage docker containers.",
            "",
            "  Installed  ↪ no: community.docker isn't installed",
            "  Versions   0.1.0 to 2.0.0, removed in 2.0.0",
            f"  Route      ✓ community.general.docker_container  removed in 2.0.0, 2.5.0 in {general(project)}",
            "             ↪ community.docker.docker_container   manage Docker containers, community.docker isn't installed",
            "  Hint       install it with alai collection install community.docker",
            "╰" + "─" * 78 + "╯",
        ]

    def test_path_under_home(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path.resolve()))

        assert find("community.general.copy_thing") == 0

        assert (
            "  Route      ✓ community.general.copy_thing  2.5.0 in ~/project/collections/ansible_collections/"
            + "community/general"
        ) in out(capsys)

    def test_hint_in_an_alai_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "alai.toml").write_text("[project]\n")

        assert find("community.docker.docker_container") == 0
        assert "  Hint       install it with alai add community.docker" in out(capsys)

        assert find("community.general.new_thing") == 0
        assert "  Hint       upgrade it with alai sync -P community.general" in out(capsys)

    def test_no_hint_when_the_target_is_installed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        install(project, "community.docker", "1.0.0")

        assert find("community.general.docker_container") == 0

        assert not any("Hint" in line for line in out(capsys))

    def test_tombstone(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.old_thing") == 0

        lines = out(capsys)
        assert lines[2:6] == [
            "  Installed  ✗ no: removed in 2.0.0; 2.5.0 is installed",
            "             Use community.general.new_thing.",
            "  Versions   1.0.0 to 2.0.0, removed in 2.0.0",
            f"  Route      ✗ community.general.old_thing  removed in 2.0.0, 2.5.0 in {general(project)}",
        ]
        assert not any("Hint" in line for line in lines)

    def test_broken(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.loop") == 0

        lines = out(capsys)
        assert "  Versions   none: it's only in the collection's routing" in lines
        assert "  Installed  ✗ no: redirects to itself" in lines

    def test_probable_copy(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("fork.general.to_seconds") == 0

        assert "  Note       probably a copy of community.general: 3 of its 3 plugins have the same names" in out(
            capsys
        )

    def test_legacy(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("ansible.legacy.docker_container") == 0

        captured = capsys.readouterr()
        assert "ansible.legacy is ansible.builtin, unless a plugin in library/" in captured.err
        lines = captured.out.splitlines()
        assert lines[0].startswith("╭─ ansible.builtin.docker_container ")
        assert "  Versions   none: it's only in ansible-core's routing" in lines
        assert (
            "             ↪ community.docker.docker_container  manage Docker containers, community.docker isn't installed"
            in lines
        )

    @pytest.mark.parametrize(
        ("name", "verdict"),
        [
            ("ansible.builtin.copy", "✓ yes, if ansible-core is in your Ansible environment, which alai doesn't check"),
            (
                "eda.builtin.range",
                "✓ yes, if ansible-rulebook is in your Ansible environment, which alai doesn't check",
            ),
            ("ansible.legacy.copy", "✓ yes, if ansible-core is in your Ansible environment, which alai doesn't check"),
            ("community.general.copy_thing", "✓ yes"),
            ("community.general.going", "⚠ yes, but it's deprecated, removal in 4.0.0"),
            ("community.general.new_thing", "↑ no: community.general 2.5.0 doesn't have it, it was added in 3.0.0"),
            ("community.docker.docker_container", "· no: community.docker isn't installed"),
        ],
    )
    def test_usable(self, name: str, verdict: str, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find(name) == 0

        assert f"  Installed  {verdict}" in out(capsys)

    def test_alias(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        install(project, "community.docker", "1.0.0")

        assert find("ansible.builtin.docker_container") == 0

        lines = out(capsys)
        assert lines[1:4] == [
            "  Another name for community.docker.docker_container.",
            "",
            "  Installed  ✓ yes, as community.docker.docker_container",
        ]
        assert not any("Hint" in line for line in lines)

    def test_route_into_an_old_version(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = [
            plugin("ansible.builtin.disk_info", routing={"redirect": "community.general.disk_info"}),
            plugin("community.general.disk_info", since="3.0.0", short_description="Disk info"),
        ]
        monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "old", {"a.a": []}, plugins=lines))
        make_python_index(tmp_path / "pypi", lines)

        assert find("ansible.builtin.disk_info") == 0
        assert find("disk_info") == 0

        lines = out(capsys)
        assert "  Installed  ↑ no: community.general 2.5.0 doesn't have it, it was added in 3.0.0" in lines
        assert "  Hint       upgrade it with alai collection install -P community.general" in lines
        assert [line for line in lines if line.startswith(" ↑ ")] == [
            " ↑ module  community.general.disk_info  added in 3.0.0, installed 2.5.0",
            " ↑ module  ansible.builtin.disk_info    → community.general.disk_info; added to community.general in "
            + "3.0.0, installed 2.5.0",
        ]

    def test_same_name(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = [
            plugin("ansible.builtin.ipmath", "filter", routing={"redirect": "old.net.ipmath"}),
            plugin("old.net.ipmath", "filter", since="1.0.0", until="2.0.0"),
            plugin("community.general.ipmath", "filter", since="1.0.0", short_description="IP maths"),
            plugin("zz.zz.ipmath", "filter", since="1.0.0"),
            plugin("gone.gone.ipmath", "filter", since="1.0.0", until="1.1.0"),
            plugin("alias.alias.ipmath", "filter", routing={"redirect": "zz.zz.ipmath"}),
        ]
        monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "net", {"a.a": []}, plugins=lines))
        make_python_index(tmp_path / "pypi", lines)

        assert find("ansible.builtin.ipmath") == 0

        details = out(capsys)
        start = details.index("  Same name  ✓ community.general.ipmath  IP maths")
        # Installed first; removed ones and other names for one already listed are left out.
        assert details[start : start + 3] == [
            "  Same name  ✓ community.general.ipmath  IP maths",
            "             · zz.zz.ipmath",
            "             not redirects: only the name is the same, so check one does the job",
        ]

    def test_no_same_name_with_warning_text(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.old_thing") == 0

        assert not any("Same name" in line for line in out(capsys))

    def test_use_the_new_name(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        general_dir = general(project)
        (general_dir / "MANIFEST.json").unlink()
        general_dir.rmdir()
        install(project, "community.docker", "1.0.0")

        assert find("community.general.docker_container") == 0

        assert "  Hint       use community.docker.docker_container instead, which is installed" in out(capsys)

    def test_route_steps(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("ansible.builtin.docker_container") == 0

        assert [line for line in out(capsys) if line.startswith(("  Route", "             "))] == [
            "  Route      ✓ ansible.builtin.docker_container   with ansible-core",
            "             ↪ community.docker.docker_container  manage Docker containers, community.docker isn't installed",
        ]

    def test_route_step_too_old(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.new_thing") == 0

        assert f"  Route      ↑ community.general.new_thing  needs 3.0.0, 2.5.0 in {general(project)}" in out(capsys)

    def test_builtin_versions(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("ansible.builtin.copy") == 0

        assert "  Versions   in ansible-core" in out(capsys)

    def test_legacy_not_found(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("ansible.legacy.nothing") == EXIT_NOT_FOUND

        assert "warning: no plugin matches 'ansible.legacy.nothing'" in capsys.readouterr().err


class TestJson:
    def test_output(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("docker_container", "--output-format", "json") == 0

        assert json.loads(capsys.readouterr().out) == {
            "v": 1,
            "plugins": [
                {
                    "type": "module",
                    "name": "docker_container",
                    "collection": "ansible.builtin",
                    "routing": {"redirect": "community.docker.docker_container"},
                    "fqcn": "ansible.builtin.docker_container",
                    "index": "ansible-core 2.20.0",
                    "matched": True,
                },
                {
                    "type": "module",
                    "name": "docker_container",
                    "collection": "community.general",
                    "since": "0.1.0",
                    "until": "2.0.0",
                    "short_description": "manage docker containers",
                    "routing": {"redirect": "community.docker.docker_container"},
                    "fqcn": "community.general.docker_container",
                    "index": "alai",
                    "matched": True,
                },
                {
                    "type": "module",
                    "name": "docker_container",
                    "collection": "community.docker",
                    "since": "1.0.0",
                    "short_description": "manage Docker containers",
                    "fqcn": "community.docker.docker_container",
                    "index": "alai",
                    "matched": True,
                },
            ],
            "collections": {
                "ansible.builtin": {"installed": True, "version": None, "path": None},
                "community.docker": {"installed": False, "version": None, "path": None},
                "community.general": {"installed": True, "version": "2.5.0", "path": str(general(project))},
            },
            "omitted": 0,
        }

    def test_redirects_reached_are_listed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("ansible.builtin.docker_container", "--output-format", "json") == 0

        result = json.loads(capsys.readouterr().out)
        assert [(p["fqcn"], p["matched"]) for p in result["plugins"]] == [
            ("ansible.builtin.docker_container", True),
            ("community.docker.docker_container", False),
        ]
        assert sorted(result["collections"]) == ["ansible.builtin", "community.docker"]

    def test_redirect_target_not_in_the_index(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("community.general.loop", "--output-format", "json") == 0

        result = json.loads(capsys.readouterr().out)
        # A route that loops back lists each plugin once.
        assert [p["fqcn"] for p in result["plugins"]] == ["community.general.loop"]

    def test_unknown_fields_passed_on(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lines = [plugin("a.a.thing", since="1.0.0", future={"x": 1})]
        monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "new", {"a.a": []}, plugins=lines))

        assert find("thing", "--output-format", "json") == 0

        assert json.loads(capsys.readouterr().out)["plugins"][0]["future"] == {"x": 1}

    def test_no_default_limit(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "-x", "--output-format", "json") == 0

        assert len(json.loads(capsys.readouterr().out)["plugins"]) == 13

    def test_limit(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("copy", "-x", "--output-format", "json", "--limit", "3") == 0

        result = json.loads(capsys.readouterr().out)
        assert (len(result["plugins"]), result["omitted"]) == (3, 10)


class TestNotFound:
    def test_suggestion(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("dockr_container") == EXIT_NOT_FOUND

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "warning: no plugin matches 'dockr_container'; did you mean 'docker_container'?\n"

    def test_with_type(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("docker_container", "-t", "filter", "-t", "lookup") == EXIT_NOT_FOUND

        assert "no plugin matches 'docker_container' of type 'filter' or 'lookup'" in capsys.readouterr().err

    def test_no_plugin_index(
        self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "bare", {"a.a": []}))

        assert find("copy") == EXIT_ERROR

        assert "none of the configured indexes has a plugin index" in capsys.readouterr().err

    def test_exact_glob(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            _ = find("-x", "*copy")

        assert "-x/--exact can't be used with a glob" in capsys.readouterr().err


def test_indexes_without_plugins_skipped(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALAI_INDEX", f"bare={make_index(tmp_path / 'bare', {'a.a': []})}")

    assert find("to_seconds", "--installed") == 0

    assert out(capsys)[1] == " ✓ filter  community.general.to_seconds  Duration to seconds"


@pytest.mark.parametrize(("value", "message"), [("x", "'x' isn't a whole number"), ("-1", "can't be negative")])
def test_invalid_limit(value: str, message: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _ = find("copy", "--limit", value)

    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("manage docker containers", "Manage docker containers."),
        ("Manage Docker containers.", "Manage Docker containers."),
        ("create, update or delete", "Create, update or delete."),
        ("add/remove users", "Add/remove users."),
        ("event-driven thing (deprecated)", "Event-driven thing (deprecated)."),
        ("Is it ready?", "Is it ready?"),
        ("oVirt cluster facts", "oVirt cluster facts."),
        ("floating_ips management", "floating_ips management."),
        ("tasks/main.yml for the role", "tasks/main.yml for the role."),
        ("  padded  ", "Padded."),
    ],
)
def test_sentence(text: str, expected: str) -> None:
    assert "".join(piece for piece, _, _ in as_sentence(markup(text))) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Returns a path with C(~) translation.", "Returns a path with ~ translation."),
        ("Manages packages with the I(dnf) package manager", "Manages packages with the dnf package manager"),
        ("Installs the L(Minio,https://min.io) client.", "Installs the Minio client."),
        ("Send notifications via U(https://pushover.net)", "Send notifications via https://pushover.net"),
        (
            "Compare with M(paloaltonetworks.panos.panos_state_snapshot)",
            "Compare with paloaltonetworks.panos.panos_state_snapshot",
        ),
        ("Apply P(ansible.builtin.comment#filter) filter", "Apply ansible.builtin.comment filter"),
        ("Return V(pong) on success", "Return pong on success"),
        ("Close a O(connection_name)", "Close a connection_name"),
        ("Set O(ns.coll.mod#module:state=present)", "Set state=present"),
        ("Set O(main:path) outside a role", "Set O(main:path) outside a role"),
        ("Once O(ignore:old_option) was used", "Once old_option was used"),
        ("Returns RV(ansible.builtin.stat#module:stat.exists=true)", "Returns stat.exists=true"),
        ("See R(the guide,platform_options) first", "See the guide first"),
        ("Value V(a=b:c) kept whole", "Value a=b:c kept whole"),
        ("Value V(a\\)b) escaped", "Value a)b escaped"),
        ("B(Bold) and E(HOME)", "Bold and HOME"),
        ("Before HORIZONTALLINE after", "Before after"),
        ("Unclosed C(oops", "Unclosed C(oops"),
        ("Not markup: ABC(x) or c(x)", "Not markup: ABC(x) or c(x)"),
    ],
)
def test_plain(text: str, expected: str) -> None:
    assert plain(text) == expected


def test_markup_pieces() -> None:
    assert markup("Use C(lxc) with O(ns.c.p#module:name=x), see L(docs,https://e.example) or R(guide,anchor)") == [
        ("Use ", None, None),
        ("lxc", "code", None),
        (" with ", None, None),
        ("name=x", "option_name", None),
        (", see ", None, None),
        ("docs", "link", "https://e.example"),
        (" or ", None, None),
        ("guide", "rst_ref", None),
    ]


def test_markup_in_a_role() -> None:
    assert plain("Set O(main:path) for the role", role=True) == "Set path for the role"


def test_markup_links_only_to_the_web() -> None:
    assert markup("U(file:///etc/passwd) L(x,javascript:alert(1)") == [
        ("file:///etc/passwd", "url", None),
        (" ", None, None),
        ("x", "link", None),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("installs the L(Bind9,https://isc.org) DNS server", "Installs the Bind9 DNS server."),
        ("C(lxc) runs tasks", "lxc runs tasks."),
        ("  Returns V(pong)  ", "Returns pong."),
    ],
)
def test_as_sentence(text: str, expected: str) -> None:
    assert "".join(piece for piece, _, _ in as_sentence(markup(text))) == expected


class TestMarkupOutput:
    LINES = [
        plugin("a.a.ping", short_description="Try to connect and return V(pong) on success"),
        plugin("a.a.bind", "role", short_description="Installs the L(Bind9,https://isc.example/) DNS server"),
    ]

    @pytest.fixture(autouse=True)
    def index(self, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAI_DEFAULT_INDEX", make_index(tmp_path / "markup", {"a.a": []}, plugins=self.LINES))

    def test_plain_without_colour(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("ping") == 0

        assert out(capsys)[1] == " · module  a.a.ping  Try to connect and return pong on success"

    def test_styled_with_colour(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("--color", "always", "ping") == 0

        assert "return \x1b[36mpong\x1b[0m on success" in out(capsys)[1]

    def test_link_only_on_a_terminal(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        assert find("--color", "always", "a.a.bind") == 0
        assert "\x1b]8;;" not in capsys.readouterr().out

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        assert find("--color", "always", "a.a.bind") == 0

        assert "the \x1b]8;;https://isc.example/\x1b\\Bind9\x1b]8;;\x1b\\ DNS server." in out(capsys)[1]

    def test_cut_counts_visible_text(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setenv("COLUMNS", "52")

        assert find("-q", "--color", "always", "ping") == 0

        line = out(capsys)[1]
        visible = re.sub(r"\x1b\[[0-9;]*m", "", line)
        assert visible == " · module  a.a.ping  Try to connect and return po…"
        assert line.endswith("\x1b[36mpo\x1b[0m…")
