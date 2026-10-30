# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""ansible.builtin and eda.builtin read from ansible-core's and ansible-rulebook's sdists on a Python index."""

from __future__ import annotations

import collections.abc as _c
import json
from pathlib import Path

import pytest
from index_data import plugin
from index_server import IndexServer
from python_index import ansible_core_files, ansible_rulebook_files, make_python_index, page, sdist

from alai._builtin import load_builtin
from alai._config import Overrides, load_settings
from alai._fetch import Fetcher
from alai._plugins import PluginIndex, Query
from alai._progress import Task

LINES = [
    plugin("ansible.builtin.copy", short_description="Copy files", since="historical"),
    plugin("ansible.builtin.iptables", short_description="Modify iptables rules", since="2.0"),
    plugin("ansible.builtin.docker_container", routing={"redirect": "community.docker.docker_container"}),
    plugin("ansible.builtin.ipmath", "filter", routing={"redirect": "ansible.netcommon.ipmath"}),
    plugin("eda.builtin.range", "event_source", short_description="Generate events"),
]


@pytest.fixture
def pypi(tmp_path: Path) -> _c.Iterator[IndexServer]:
    make_python_index(tmp_path / "pypi", LINES)
    with IndexServer(tmp_path / "pypi") as server:
        yield server


def load(tmp_path: Path, environ: dict[str, str], url: str, *, offline: bool = False) -> tuple[list[PluginIndex], Task]:
    settings = load_settings(
        Overrides(offline=offline),
        cwd=tmp_path,
        environ={**environ, "ALAI_PYTHON_INDEX": url, "ALAI_CACHE_DIR": str(tmp_path / "cache")},
    )
    task = Task("Fetching plugin indexes...", total=2)
    with Fetcher(user_agent="alai tests", offline=offline) as fetcher:
        return load_builtin(settings, fetcher, task), task


def released(indexes: list[PluginIndex]) -> list[str]:
    return [f"{i.source['package']} {i.source['version']}" for i in indexes]


def lines(indexes: list[PluginIndex], collection: str) -> dict[str, dict[str, object]]:
    """Each line of ``collection`` as JSON, by ``type name``."""
    found: dict[str, dict[str, object]] = {}
    for index in indexes:
        for found_plugin in index.search(Query(f"{collection}.*")):
            found[f"{found_plugin.type} {found_plugin.name}"] = found_plugin.raw
    return found


class TestLines:
    def test_from_the_sdists(self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer) -> None:
        indexes, _ = load(tmp_path, environ, pypi.url)

        assert released(indexes) == ["ansible-core 2.20.0", "ansible-rulebook 1.2.0"]
        # since is version_added, left out when it's historical; routing with no plugin is a line of its own.
        assert lines(indexes, "ansible.builtin") == {
            "module copy": {
                "v": 1,
                "type": "module",
                "name": "copy",
                "collection": "ansible.builtin",
                "short_description": "Copy files",
            },
            "module iptables": {
                "v": 1,
                "type": "module",
                "name": "iptables",
                "collection": "ansible.builtin",
                "since": "2.0",
                "short_description": "Modify iptables rules",
            },
            "module docker_container": {
                "v": 1,
                "type": "module",
                "name": "docker_container",
                "collection": "ansible.builtin",
                "routing": {"redirect": "community.docker.docker_container"},
            },
            "filter ipmath": {
                "v": 1,
                "type": "filter",
                "name": "ipmath",
                "collection": "ansible.builtin",
                "routing": {"redirect": "ansible.netcommon.ipmath"},
            },
        }
        assert lines(indexes, "eda.builtin") == {
            "event_source range": {
                "v": 1,
                "type": "event_source",
                "name": "range",
                "collection": "eda.builtin",
                "short_description": "Generate events",
            }
        }
        assert (tmp_path / "cache" / "builtin-v1" / "ansible-core" / "2.20.0.jsonl").is_file()

    def test_action_routing_left_out(self, tmp_path: Path, environ: dict[str, str]) -> None:
        # ansible-core's action routing mirrors its modules': an action plugin isn't named in a play.
        files = ansible_core_files(LINES)
        runtime = json.loads(files["lib/ansible/config/ansible_builtin_runtime.yml"])
        runtime["plugin_routing"]["action"] = {"docker_container": {"redirect": "community.docker.docker_container"}}
        files["lib/ansible/config/ansible_builtin_runtime.yml"] = json.dumps(runtime)
        _ = sdist(tmp_path / "pypi", "ansible-core", "2.20.0", files)
        page(tmp_path / "pypi", "ansible-core", ["2.20.0"])
        _ = sdist(tmp_path / "pypi", "ansible-rulebook", "1.2.0", ansible_rulebook_files(LINES))
        page(tmp_path / "pypi", "ansible-rulebook", ["1.2.0"])

        with IndexServer(tmp_path / "pypi") as server:
            indexes, _ = load(tmp_path, environ, server.url)

        assert {t for t in lines(indexes, "ansible.builtin") if t.startswith("action")} == set()


class TestRelease:
    def test_latest_stable(self, tmp_path: Path, environ: dict[str, str]) -> None:
        # The highest release that isn't a pre-release or yanked, from a page listing them in any order.
        root = tmp_path / "pypi"
        for version in ("2.20.0", "2.21.0", "2.21.1", "2.22.0rc1"):
            _ = sdist(root, "ansible-core", version, ansible_core_files(LINES))
        page(root, "ansible-core", ["2.22.0rc1", "2.20.0", "2.21.1", "2.21.0"], yanked=("2.21.1",))
        _ = sdist(root, "ansible-rulebook", "1.2.0", ansible_rulebook_files(LINES))
        page(root, "ansible-rulebook", ["1.2.0"])

        with IndexServer(root) as server:
            indexes, _ = load(tmp_path, environ, server.url)

        assert released(indexes)[0] == "ansible-core 2.21.0"

    def test_cached_lines_reused(self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer) -> None:
        _ = load(tmp_path, environ, pypi.url)
        before = len(pypi.requests)

        indexes, _ = load(tmp_path, environ, pypi.url)

        # The pages are revalidated; the sdists aren't downloaded again.
        assert released(indexes) == ["ansible-core 2.20.0", "ansible-rulebook 1.2.0"]
        assert [r.path for r in pypi.requests[before:]] == ["/ansible-core/", "/ansible-rulebook/"]

    def test_offline_from_the_cache(self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer) -> None:
        _ = load(tmp_path, environ, pypi.url)

        indexes, _ = load(tmp_path, environ, pypi.url, offline=True)

        assert released(indexes) == ["ansible-core 2.20.0", "ansible-rulebook 1.2.0"]

    def test_newest_cached_when_unreachable(
        self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _ = load(tmp_path, environ, pypi.url)
        # An older run's lines, which the newest cached beats.
        _ = (tmp_path / "cache" / "builtin-v1" / "ansible-core" / "2.19.0.jsonl").write_bytes(b"")
        # Gone from the index (a 5xx is retried, so it would be served again).
        pypi.responses["/ansible-core/"] = [(404, {}, b"gone")]
        pypi.responses["/ansible-rulebook/"] = [(404, {}, b"gone")]

        indexes, _ = load(tmp_path, environ, pypi.url)

        assert released(indexes) == ["ansible-core 2.20.0", "ansible-rulebook 1.2.0"]
        err = capsys.readouterr().err
        assert f"warning: {pypi.url} has no ansible-core; using ansible-core 2.20.0 from the cache\n" in err
        assert "using ansible-rulebook 1.2.0 from the cache" in err

    def test_offline_with_nothing_cached(
        self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        indexes, task = load(tmp_path, environ, pypi.url, offline=True)

        assert indexes == []
        assert task.done == 2
        err = capsys.readouterr().err
        assert "warning: ansible.builtin isn't searchable: " in err
        assert "isn't in the cache, and alai is offline" in err
        assert "warning: eda.builtin isn't searchable: " in err


class TestProblems:
    def test_index_without_the_project(
        self, tmp_path: Path, environ: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = tmp_path / "pypi"
        _ = sdist(root, "ansible-core", "2.20.0", ansible_core_files(LINES))
        page(root, "ansible-core", ["2.20.0"])

        with IndexServer(root) as server:
            indexes, _ = load(tmp_path, environ, server.url)

        assert released(indexes) == ["ansible-core 2.20.0"]
        assert (
            f"warning: eda.builtin isn't searchable: {server.url} has no ansible-rulebook\n" in capsys.readouterr().err
        )

    def test_not_a_simple_index(
        self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pypi.responses["/ansible-core/"] = [(200, {"Content-Type": "text/html"}, b"<html>not json</html>")]

        indexes, _ = load(tmp_path, environ, pypi.url)

        assert released(indexes) == ["ansible-rulebook 1.2.0"]
        assert "isn't a PEP 691 JSON page, as a Python simple index serves" in capsys.readouterr().err

    def test_only_wheels(self, tmp_path: Path, environ: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
        root = tmp_path / "pypi"
        (root / "ansible-core").mkdir(parents=True)
        files: list[dict[str, object]] = [
            {"filename": "ansible_core-2.20.0-py3-none-any.whl", "url": "../files/x.whl", "hashes": {}}
        ]
        _ = (root / "ansible-core" / "index.json").write_text(json.dumps({"files": files}))

        with IndexServer(root) as server:
            indexes, _ = load(tmp_path, environ, server.url)

        assert indexes == []
        assert "has no release of ansible-core with an sdist" in capsys.readouterr().err

    def test_sdist_not_as_the_index_says(
        self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        name = "ansible_core-2.20.0.tar.gz"
        _ = sdist(tmp_path / "pypi", "ansible-core", "2.20.0", {"lib/ansible/release.py": "changed"})

        indexes, _ = load(tmp_path, environ, pypi.url)

        assert released(indexes) == ["ansible-rulebook 1.2.0"]
        assert f"/files/{name} doesn't match the index: its SHA-256 is " in capsys.readouterr().err

    def test_without_a_checksum(self, tmp_path: Path, environ: dict[str, str]) -> None:
        # An index that gives no sha256 is taken at its word.
        root = tmp_path / "pypi"
        _ = sdist(root, "ansible-core", "2.20.0", ansible_core_files(LINES))
        page(root, "ansible-core", ["2.20.0"], sha256=False)
        _ = sdist(root, "ansible-rulebook", "1.2.0", ansible_rulebook_files(LINES))
        page(root, "ansible-rulebook", ["1.2.0"])

        with IndexServer(root) as server:
            indexes, _ = load(tmp_path, environ, server.url)

        assert released(indexes) == ["ansible-core 2.20.0", "ansible-rulebook 1.2.0"]

    def test_not_ansible_core(
        self, tmp_path: Path, environ: dict[str, str], pypi: IndexServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _ = sdist(tmp_path / "pypi", "ansible-core", "2.20.0", {"README": "not ansible-core"})
        page(tmp_path / "pypi", "ansible-core", ["2.20.0"])

        indexes, _ = load(tmp_path, environ, pypi.url)

        assert released(indexes) == ["ansible-rulebook 1.2.0"]
        assert (
            "warning: ansible.builtin isn't searchable: can't extract ansible-core 2.20.0: " in capsys.readouterr().err
        )
