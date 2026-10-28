# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Tarball sources: a built collection's tarball, by path or URL, locked by its checksum and installed as an index's
artifact is."""

from __future__ import annotations

import collections.abc as _c
import os
import shutil
from pathlib import Path

import pytest
from alai_core.toml import loads
from artifact_data import Link, build_artifact
from helpers import install_record, installed_at, lines, write_sources
from index_server import IndexServer, serve

from alai._cli import main
from alai._common import EXIT_ERROR
from alai._lock import ArchiveSource, ArchiveUrlSource, read_lock, render_lock


def tarball(
    path: Path,
    name: str = "ns.src",
    version: str = "1.0.0",
    dependencies: dict[str, str] | None = None,
    requires_ansible: str | None = None,
    readme: bytes = b"readme\n",
) -> tuple[str, int]:
    """A built collection's tarball at ``path``, with its sha256 and size."""
    namespace, short = name.split(".")
    files: dict[str, bytes | Link] = {"README.md": readme}
    if requires_ansible is not None:
        files["meta/runtime.yml"] = f"requires_ansible: '{requires_ansible}'\n".encode()
    info = {"namespace": namespace, "name": short, "version": version, "dependencies": dependencies or {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_artifact(path, name, version, files, manifest={"collection_info": info})


@pytest.fixture
def server(tmp_path: Path) -> _c.Iterator[IndexServer]:
    (tmp_path / "site").mkdir()
    yield from serve(tmp_path / "site")


def downloads(server: IndexServer, name: str = "/ns-src-1.0.0.tar.gz") -> int:
    return server.paths().count(name)


class TestPath:
    def test_lock(self, project: Path) -> None:
        cksum, size = tarball(
            project.parent / "dist" / "ns-src-1.0.0.tar.gz", dependencies={"a.a": ">=2.0.0"}, requires_ansible=">=2.16"
        )
        write_sources(project, '"ns.src" = { path = "../dist/ns-src-1.0.0.tar.gz" }')

        assert main(["lock", "-q"]) == 0

        data = loads((project / "alai.lock").read_text())
        assert data["sources"] == {"ns.src": {"path": "../dist/ns-src-1.0.0.tar.gz"}}
        entries = {c["name"]: c for c in data["collection"]}
        assert sorted(entries) == ["a.a", "b.b", "ns.src"]
        assert entries["ns.src"] == {
            "name": "ns.src",
            "version": "1.0.0",
            "source": {"path": "../dist/ns-src-1.0.0.tar.gz"},
            "sha256": cksum,
            "size": size,
            "requires_ansible": ">=2.16",
            "collections": ["a.a>=2.0.0"],
            "dependencies": ["a.a"],
        }
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and render_lock(lock) == (project / "alai.lock").read_text()
        assert lock.get("ns.src") is not None and lock.get("ns.src").source == ArchiveSource(  # pyright: ignore[reportOptionalMemberAccess]
            "../dist/ns-src-1.0.0.tar.gz"
        )

    def test_sync(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = project.parent / "ns-src-1.0.0.tar.gz"
        cksum, _ = tarball(path)
        write_sources(project, '"ns.src" = { path = "../ns-src-1.0.0.tar.gz" }')

        assert main(["sync"]) == 0

        # Extracted into the cache when it was locked, so there's nothing to prepare.
        assert lines(capsys.readouterr().err) == [
            "Resolved 1 collection",
            "Installed 1 collection",
            f" + ns.src==1.0.0 (from {path})",
        ]
        assert (installed_at(project / "collections", "ns.src") / "README.md").read_text() == "readme\n"
        assert install_record(project / "collections", "ns.src") == {"source": {"path": str(path)}, "cksum": cksum}

        assert main(["sync"]) == 0
        assert lines(capsys.readouterr().err) == ["Resolved 1 collection", "Checked 1 collection"]

    def test_frozen_from_the_file(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = tarball(project.parent / "ns-src-1.0.0.tar.gz")
        write_sources(project, '"ns.src" = { path = "../ns-src-1.0.0.tar.gz" }')
        _ = main(["lock", "-q"])
        shutil.rmtree(tmp_path / "cache")

        assert main(["sync", "--frozen"]) == 0

        assert lines(capsys.readouterr().err)[0] == "Installed 1 collection"
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

    def test_changed_needs_upgrade(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = project.parent / "ns-src-1.0.0.tar.gz"
        _ = tarball(path)
        write_sources(project, '"ns.src" = { path = "../ns-src-1.0.0.tar.gz" }')
        _ = main(["sync", "-q"])
        new, _ = tarball(path, readme=b"rebuilt\n")

        # As uv does: the lock pins the tarball, so a different one at the same path is an error until -P.
        for command in (["sync"], ["lock"], ["lock", "--check"]):
            assert main(command) == EXIT_ERROR
            err = capsys.readouterr().err
            assert f"{path} has changed since ns.src was locked from it: its SHA-256 is {new}" in err
            assert "run alai lock -P ns.src to lock it as it is now" in err

        assert main(["sync", "-P", "ns.src"]) == 0

        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from {path})"
        assert (installed_at(project / "collections", "ns.src") / "README.md").read_text() == "rebuilt\n"
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and lock.collections[0].cksum == new

    def test_wrong_collection(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = tarball(project.parent / "other.tar.gz", "ns.other")
        write_sources(project, '"ns.src" = { path = "../other.tar.gz" }')

        assert main(["lock"]) == EXIT_ERROR

        assert "takes ns.src from ../other.tar.gz, but it holds ns.other" in capsys.readouterr().err

    def test_not_editable(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = tarball(project.parent / "ns-src-1.0.0.tar.gz")
        write_sources(project, '"ns.src" = { path = "../ns-src-1.0.0.tar.gz", editable = true }')

        assert main(["lock"]) == EXIT_ERROR

        assert "is a tarball, which can't be editable" in capsys.readouterr().err

    def test_not_a_tarball(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project.parent / "notes.tar.gz").write_text("not a tarball\n")
        write_sources(project, '"ns.src" = { path = "../notes.tar.gz" }')

        assert main(["lock"]) == EXIT_ERROR

        assert "notes.tar.gz isn't a gzip file, so it isn't a collection tarball" in capsys.readouterr().err

    def test_replaces_the_index_copy(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # The same version from a tarball isn't the index's copy: the record tells them apart.
        _ = (project / "alai.toml").write_text('[project]\ncollections = ["c.c"]\n')
        _ = main(["sync", "-q"])
        path = project.parent / "c-c-1.0.0.tar.gz"
        _ = tarball(path, "c.c")
        write_sources(project, '"c.c" = { path = "../c-c-1.0.0.tar.gz" }', '"c.c"')

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err)[-1] == f" ~ c.c==1.0.0 (from {path})"


class TestUrl:
    def test_lock_downloads_once(self, project: Path, server: IndexServer, capsys: pytest.CaptureFixture[str]) -> None:
        cksum, size = tarball(Path(server.root) / "ns-src-1.0.0.tar.gz", dependencies={"c.c": "*"})
        url = f"{server.url}ns-src-1.0.0.tar.gz"
        write_sources(project, f'"ns.src" = {{ url = "{url}" }}')

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err)[-2:] == [" + c.c==1.0.0", f" + ns.src==1.0.0 (from {url})"]
        data = loads((project / "alai.lock").read_text())
        entry = next(c for c in data["collection"] if c["name"] == "ns.src")
        assert entry["source"] == {"url": url} and entry["sha256"] == cksum and entry["size"] == size
        assert entry["collections"] == ["c.c"]
        assert install_record(project / "collections", "ns.src") == {"source": {"url": url}, "cksum": cksum}
        assert downloads(server) == 1

        # Locked again from the lock's entry, as uv does: what's behind a URL isn't expected to change.
        assert main(["lock", "-q"]) == 0
        assert main(["lock", "--check"]) == 0
        assert downloads(server) == 1

        assert main(["lock", "-q", "-P", "ns.src"]) == 0
        assert downloads(server) == 2

    def test_changed_behind_the_url(
        self, project: Path, server: IndexServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = Path(server.root) / "ns-src-1.0.0.tar.gz"
        # Locked larger than it becomes, so the download is checked against the checksum, not cut off by the size.
        _ = tarball(path, readme=bytes(range(256)) * 16)
        url = f"{server.url}ns-src-1.0.0.tar.gz"
        write_sources(project, f'"ns.src" = {{ url = "{url}" }}')
        _ = main(["lock", "-q"])
        shutil.rmtree(tmp_path / "cache")
        new, _ = tarball(path, readme=b"rebuilt\n")

        assert main(["sync"]) == EXIT_ERROR
        assert f"doesn't match the lock: it has SHA-256 {new}" in capsys.readouterr().err

        assert main(["sync", "-q", "-P", "ns.src"]) == 0
        assert (installed_at(project / "collections", "ns.src") / "README.md").read_text() == "rebuilt\n"

    def test_downloaded_in_parallel(self, project: Path, server: IndexServer) -> None:
        for name in ("ns.one", "ns.two"):
            _ = tarball(Path(server.root) / f"{name}.tar.gz", name)
            server.delays[f"/{name}.tar.gz"] = 0.3
        write_sources(
            project,
            f'"ns.one" = {{ url = "{server.url}ns.one.tar.gz" }}\n"ns.two" = {{ url = "{server.url}ns.two.tar.gz" }}',
            '"ns.one", "ns.two"',
        )

        assert main(["lock", "-q"]) == 0

        # Both asked for before either answered.
        arrived = [r.arrived for r in server.requests if r.path.endswith(".tar.gz")]
        assert len(arrived) == 2 and abs(arrived[0] - arrived[1]) < 0.3
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and [c.name for c in lock.collections] == ["ns.one", "ns.two"]

    def test_first_failure_by_name(
        self, project: Path, server: IndexServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _ = tarball(Path(server.root) / "b.tar.gz", "ns.bbb")
        write_sources(
            project,
            f'"ns.aaa" = {{ url = "{server.url}a.tar.gz" }}\n"ns.bbb" = {{ url = "{server.url}b.tar.gz" }}\n'
            + f'"ns.ccc" = {{ url = "{server.url}c.tar.gz" }}',
            '"ns.aaa", "ns.bbb", "ns.ccc"',
        )

        assert main(["lock"]) == EXIT_ERROR

        err = capsys.readouterr().err
        assert f"can't download {server.url}a.tar.gz" in err and "c.tar.gz" not in err

    def test_wrong_collection(self, project: Path, server: IndexServer, capsys: pytest.CaptureFixture[str]) -> None:
        _ = tarball(Path(server.root) / "x.tar.gz", "ns.other")
        write_sources(project, f'"ns.src" = {{ url = "{server.url}x.tar.gz" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert f"takes ns.src from {server.url}x.tar.gz, but it holds ns.other" in capsys.readouterr().err

    def test_not_gzip(self, project: Path, server: IndexServer, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (Path(server.root) / "page.tar.gz").write_text("<html>sign in</html>\n")
        write_sources(project, f'"ns.src" = {{ url = "{server.url}page.tar.gz" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert "page.tar.gz: not a gzip file, so not a collection tarball" in capsys.readouterr().err

    def test_not_found(self, project: Path, server: IndexServer, capsys: pytest.CaptureFixture[str]) -> None:
        write_sources(project, f'"ns.src" = {{ url = "{server.url}missing.tar.gz" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert f"can't download {server.url}missing.tar.gz" in capsys.readouterr().err

    def test_lock_reads_back(self, project: Path, server: IndexServer) -> None:
        _ = tarball(Path(server.root) / "ns-src-1.0.0.tar.gz")
        url = f"{server.url}ns-src-1.0.0.tar.gz"
        write_sources(project, f'"ns.src" = {{ url = "{url}" }}')
        _ = main(["lock", "-q"])

        lock = read_lock(str(project / "alai.lock"))

        assert lock is not None and lock.collections[0].source == ArchiveUrlSource(url)
        assert render_lock(lock) == (project / "alai.lock").read_text()


class TestAddRemove:
    def test_add_path(self, project: Path) -> None:
        _ = tarball(project / "dist" / "ns-src-1.0.0.tar.gz")
        _ = (project / "alai.toml").write_text('[project]\ncollections = ["c.c"]\n')
        os.chdir(project / "dist")

        # A name ending in .tar.gz is a tarball, with or without a /.
        assert main(["add", "-q", "ns-src-1.0.0.tar.gz"]) == 0

        assert (project / "alai.toml").read_text() == (
            '[project]\ncollections = [\n    "c.c",\n    "ns.src",\n]\n\n'
            + '[project.sources]\n"ns.src" = { path = "dist/ns-src-1.0.0.tar.gz" }\n'
        )
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

    def test_add_file_url(self, project: Path) -> None:
        path = project.parent / "ns-src-1.0.0.tar.gz"
        _ = tarball(path)
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        # As uv writes one: a file URL is the file's absolute path.
        assert main(["add", "-q", path.as_uri()]) == 0

        assert f'"ns.src" = {{ path = "{path}" }}' in (project / "alai.toml").read_text()

    def test_add_file_url_fragment(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = project.parent / "ns-src-1.0.0.tar.gz"
        _ = tarball(path)
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        # uv drops it; here it's an error, as nothing would take it.
        assert main(["add", f"{path.as_uri()}#version=2.0.0"]) == EXIT_ERROR

        assert "a file URL takes nothing after a #" in capsys.readouterr().err

    def test_add_url(self, project: Path, server: IndexServer) -> None:
        _ = tarball(Path(server.root) / "ns-src-1.0.0.tar.gz")
        url = f"{server.url}ns-src-1.0.0.tar.gz"
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", "-q", url]) == 0

        assert (project / "alai.toml").read_text() == (
            f'[project]\ncollections = [\n    "ns.src",\n]\n\n[project.sources]\n"ns.src" = {{ url = "{url}" }}\n'
        )
        # Downloaded once, for its name, and that copy locked and installed.
        assert downloads(server) == 1
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

        assert main(["remove", "-q", "ns.src"]) == 0

        assert (project / "alai.toml").read_text() == "[project]\ncollections = []\n\n[project.sources]\n"
        assert not installed_at(project / "collections", "ns.src").exists()

    def test_add_editable(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = tarball(project.parent / "ns-src-1.0.0.tar.gz")
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", "--editable", "../ns-src-1.0.0.tar.gz"]) == EXIT_ERROR

        assert "is a tarball, which can't be editable" in capsys.readouterr().err


class TestCollectionInstall:
    def test_path(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = project.parent / "ns-src-1.0.0.tar.gz"
        _ = tarball(path, dependencies={"c.c": "*"})
        _ = (project / "alai.toml").write_text("")

        assert main(["collection", "install", "../ns-src-1.0.0.tar.gz"]) == 0
        assert lines(capsys.readouterr().err)[-2:] == [" + c.c==1.0.0", f" + ns.src==1.0.0 (from {path})"]

        # The same tarball is left as it is, as uv pip install leaves one; another is installed over it.
        assert main(["collection", "install", "../ns-src-1.0.0.tar.gz"]) == 0
        assert lines(capsys.readouterr().err)[-1] == "Checked 2 collections"
        _ = tarball(path, readme=b"rebuilt\n")
        assert main(["collection", "install", "../ns-src-1.0.0.tar.gz"]) == 0
        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from {path})"

    def test_url(self, project: Path, server: IndexServer, capsys: pytest.CaptureFixture[str]) -> None:
        _ = tarball(Path(server.root) / "ns-src-1.0.0.tar.gz")
        url = f"{server.url}ns-src-1.0.0.tar.gz"
        _ = (project / "alai.toml").write_text("")

        assert main(["collection", "install", url]) == 0

        assert lines(capsys.readouterr().err)[-1] == f" + ns.src==1.0.0 (from {url})"
        assert downloads(server) == 1

    def test_url_not_editable(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "alai.toml").write_text("")

        assert main(["collection", "install", "-e", "https://example.invalid/x.tar.gz"]) == EXIT_ERROR

        assert "is a tarball, which can't be editable" in capsys.readouterr().err
