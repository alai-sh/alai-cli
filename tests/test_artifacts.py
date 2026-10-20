# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import hashlib
import os
import stat
import tarfile
from pathlib import Path

import pytest
from artifact_data import Link, build_artifact
from index_server import IndexServer, serve

from alai import _artifacts
from alai._artifacts import Artifact, ArtifactCache, ArtifactError
from alai._fetch import Fetcher
from alai._semver import Version


@pytest.fixture(autouse=True)
def no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def fetcher() -> _c.Iterator[Fetcher]:
    with Fetcher(user_agent="alai-test/1") as f:
        yield f


@pytest.fixture
def cache(tmp_path: Path, fetcher: Fetcher) -> ArtifactCache:
    return ArtifactCache(str(tmp_path / "cache"), fetcher)


def artifact(tmp_path: Path, name: str = "ns.coll", version: str = "1.0.0", **kwargs: object) -> Artifact:
    path = tmp_path / "dl" / f"{name}-{version}.tar.gz"
    path.parent.mkdir(exist_ok=True)
    cksum, size = build_artifact(path, name, version, **kwargs)  # pyright: ignore[reportArgumentType]
    return Artifact(name, Version(version), url=path.as_uri(), cksum=cksum, size=size)


def tree(root: str) -> dict[str, str]:
    """Each path under ``root`` with its type and mode, or a symlink's target."""
    out: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[relative] = f"-> {path.readlink()}"
        else:
            kind = "dir" if path.is_dir() else "file"
            out[relative] = f"{kind} {stat.S_IMODE(path.stat().st_mode):o}"
    return out


def leftovers(cache: ArtifactCache) -> list[str]:
    root = Path(cache.root)
    return [p.name for p in root.iterdir() if p.name.startswith(".tmp-")] if root.is_dir() else []


class TestGet:
    def test_extracts_as_ansible_galaxy_does(self, tmp_path: Path, cache: ArtifactCache) -> None:
        files: dict[str, bytes | Link] = {
            "README.md": b"hi\n",
            "plugins/modules/mod.py": b"print()\n",
            "plugins/modules/alias.py": Link("mod.py"),
            "tests/run.sh": b"#!/bin/sh\n",
        }
        item = artifact(tmp_path, files=files, executable=frozenset({"tests/run.sh"}))
        old = os.umask(0o022)
        try:
            path = cache.get(item)
        finally:
            _ = os.umask(old)

        assert Path(path) == Path(cache.root, item.cksum)
        assert tree(path) == {
            "FILES.json": "file 644",
            "MANIFEST.json": "file 644",
            "README.md": "file 644",
            "plugins": "dir 755",
            "plugins/modules": "dir 755",
            "plugins/modules/alias.py": "-> mod.py",
            "plugins/modules/mod.py": "file 644",
            "tests": "dir 755",
            "tests/run.sh": "file 755",
        }
        assert Path(path, "plugins/modules/alias.py").read_bytes() == b"print()\n"
        assert leftovers(cache) == []

    def test_umask_applies(self, tmp_path: Path, fetcher: Fetcher) -> None:
        item = artifact(tmp_path, files={"a/b.txt": b"x"})
        old = os.umask(0o077)
        try:
            path = ArtifactCache(str(tmp_path / "cache"), fetcher).get(item)
        finally:
            _ = os.umask(old)

        assert tree(path)["a"] == "dir 700"
        assert tree(path)["a/b.txt"] == "file 600"

    def test_cached_copy_is_trusted(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = artifact(tmp_path)
        path = cache.get(item)
        Path(item.url.removeprefix("file://")).unlink()

        assert cache.get(item) == path

    def test_over_http(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = artifact(tmp_path)
        server: IndexServer
        for server in serve(tmp_path / "dl"):
            url = f"{server.url}{item.url.rsplit('/', 1)[1]}"
            path = cache.get(Artifact(item.name, item.version, url=url, cksum=item.cksum, size=item.size))

            assert Path(path, "README.md").is_file()

    def test_only_listed_files(self, tmp_path: Path, cache: ArtifactCache) -> None:
        unlisted = tarfile.TarInfo("unlisted.txt")
        unlisted.size = 1

        path = cache.get(artifact(tmp_path, extra=[unlisted]))

        assert not Path(path, "unlisted.txt").exists()

    def test_get_all(self, tmp_path: Path, cache: ArtifactCache) -> None:
        items = [artifact(tmp_path, f"ns.c{i}") for i in range(5)]

        paths = cache.get_all(items)

        assert paths == {i.name: cache.path(i.cksum) for i in items}

    def test_get_all_reports_the_first_failure(self, tmp_path: Path, cache: ArtifactCache) -> None:
        good = artifact(tmp_path, "ns.a")
        bad = [artifact(tmp_path, f"ns.{n}") for n in ("c", "b")]
        bad = [Artifact(b.name, b.version, url=b.url, cksum="0" * 64, size=b.size) for b in bad]

        with pytest.raises(ArtifactError, match=r"^ns\.b 1\.0\.0 from "):
            _ = cache.get_all([good, *bad])
        assert Path(cache.path(good.cksum)).is_dir()

    def test_lost_race(self, tmp_path: Path, cache: ArtifactCache, monkeypatch: pytest.MonkeyPatch) -> None:
        # Another alai renames the same artifact into place first: its copy is used.
        item = artifact(tmp_path)
        extract = _artifacts.extract

        def first(tarball: str, dest: str, name: str, version: Version, *, umask: int) -> None:
            extract(tarball, cache.path(item.cksum), name, version, umask=umask)
            extract(tarball, dest, name, version, umask=umask)

        monkeypatch.setattr(_artifacts, "extract", first)

        assert cache.get(item) == cache.path(item.cksum)
        assert leftovers(cache) == []


class TestDownloadErrors:
    def test_checksum_mismatch(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = artifact(tmp_path)

        with pytest.raises(ArtifactError, match="doesn't match the lock: it has SHA-256"):
            _ = cache.get(Artifact(item.name, item.version, url=item.url, cksum="0" * 64, size=item.size))
        assert list(Path(cache.root).iterdir()) == []

    def test_larger_than_listed(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = artifact(tmp_path)

        with pytest.raises(ArtifactError, match="larger than the 10 bytes the index lists"):
            _ = cache.get(Artifact(item.name, item.version, url=item.url, cksum=item.cksum, size=10))
        assert leftovers(cache) == []

    def test_missing(self, tmp_path: Path, cache: ArtifactCache) -> None:
        url = (tmp_path / "nope.tar.gz").as_uri()

        with pytest.raises(ArtifactError, match=r"can't download ns\.coll 1\.0\.0: .*not found"):
            _ = cache.get(Artifact("ns.coll", Version("1.0.0"), url=url, cksum="0" * 64, size=1))

    def test_offline_miss(self, tmp_path: Path) -> None:
        with Fetcher(user_agent="t", offline=True) as fetcher:
            cache = ArtifactCache(str(tmp_path / "cache"), fetcher)

            with pytest.raises(ArtifactError, match="isn't in the cache, and alai is offline"):
                _ = cache.get(Artifact("ns.coll", Version("1.0.0"), url="https://dl.example/x", cksum="0" * 64, size=1))

    @pytest.mark.parametrize("cksum", ["../../etc", "A" * 64, "0" * 63])
    def test_bad_cksum(self, cache: ArtifactCache, cksum: str) -> None:
        with pytest.raises(ArtifactError, match="isn't a SHA-256 checksum"):
            _ = cache.path(cksum)


def file_member(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = 1
    return info


def listed_file(name: str, sha256: str | None = "0" * 64) -> dict[str, object]:
    return {"name": name, "ftype": "file", "chksum_type": "sha256", "chksum_sha256": sha256}


class TestExtractErrors:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            (
                {"manifest": {"collection_info": {"namespace": "ns", "name": "other", "version": "1.0.0"}}},
                r"MANIFEST\.json is for ns\.other 1\.0\.0",
            ),
            (
                {"manifest": {"collection_info": {"namespace": "ns", "name": "coll", "version": "2.0.0"}}},
                r"MANIFEST\.json is for ns\.coll 2\.0\.0",
            ),
            ({"manifest": {}}, r"MANIFEST\.json is for None\.None None"),
            ({"listed": [listed_file("missing.txt")]}, r"doesn't contain missing\.txt, which FILES\.json lists"),
            ({"listed": [listed_file("README.md")]}, r"README\.md in the artifact doesn't match its checksum"),
            ({"listed": [listed_file("README.md", None)]}, r"FILES\.json has no checksum for README\.md"),
            ({"listed": [{"name": "README.md", "ftype": "fifo"}]}, "FILES.json has an unreadable entry"),
            ({"listed": [{"name": "README.md", "ftype": "dir"}]}, r"README\.md in the artifact isn't a directory"),
            ({"listed": [listed_file("../escape")]}, r"a path outside the collection: \.\./escape"),
            ({"listed": [listed_file("/abs")]}, "a path outside the collection: /abs"),
            ({"listed": [listed_file("a/./b")]}, r"a path outside the collection: a/\./b"),
            (
                {"files": {"README.md": b"x", "x": Link("../outside")}, "listed": [listed_file("x")]},
                r"x in the artifact links outside the collection, to \.\./outside",
            ),
            (
                {"files": {"README.md": b"x", "x": Link("/etc/passwd")}, "listed": [listed_file("x")]},
                "links outside the collection, to /etc/passwd",
            ),
        ],
    )
    def test_refused(self, tmp_path: Path, cache: ArtifactCache, kwargs: dict[str, object], message: str) -> None:
        with pytest.raises(ArtifactError, match=message):
            _ = cache.get(artifact(tmp_path, **kwargs))
        assert leftovers(cache) == []

    def test_chained_links_refused(self, tmp_path: Path, cache: ArtifactCache) -> None:
        # Each link stays inside by name (a -> ., s -> a/.. is .), but on disk s is the collection's parent.
        dirs = [{"name": n, "ftype": "dir", "chksum_type": None, "chksum_sha256": None} for n in ("a", "s")]
        item = artifact(
            tmp_path,
            files={"a": Link("."), "s": Link("a/.."), "x.txt": b"x"},
            listed=[*dirs, listed_file("s/x.txt")],
            extra=[file_member("s/x.txt")],
        )

        with pytest.raises(ArtifactError, match=r"s/x\.txt in the artifact leads outside the collection"):
            _ = cache.get(item)
        assert not (Path(cache.root) / "x.txt").exists()

    def test_link_made_to_lead_out_later(self, tmp_path: Path, cache: ArtifactCache) -> None:
        # s -> a/.. is inside while a doesn't exist; a -> . made afterwards turns it into the parent.
        dirs = [{"name": n, "ftype": "dir", "chksum_type": None, "chksum_sha256": None} for n in ("s", "a")]

        with pytest.raises(ArtifactError, match="s in the artifact leads outside the collection"):
            _ = cache.get(artifact(tmp_path, files={"s": Link("a/.."), "a": Link(".")}, listed=dirs))

    def test_device_refused(self, tmp_path: Path, cache: ArtifactCache) -> None:
        device = tarfile.TarInfo("dev")
        device.type = tarfile.CHRTYPE

        with pytest.raises(ArtifactError, match="dev in the artifact isn't a file"):
            _ = cache.get(artifact(tmp_path, extra=[device], listed=[listed_file("dev")]))

    def test_corrupt(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = artifact(tmp_path)
        path = Path(item.url.removeprefix("file://"))
        data = path.read_bytes()[:200]
        _ = path.write_bytes(data)
        cksum = hashlib.sha256(data).hexdigest()

        with pytest.raises(ArtifactError, match=r"ns\.coll 1\.0\.0: can't extract the artifact"):
            _ = cache.get(Artifact(item.name, item.version, url=item.url, cksum=cksum, size=len(data)))
        assert leftovers(cache) == []
