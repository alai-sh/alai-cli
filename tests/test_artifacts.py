# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import gzip
import hashlib
import io
import json
import os
import socket
import stat
import tarfile
from pathlib import Path

import pytest
from alai_core.semver import Version
from artifact_data import Link, build_artifact
from index_server import IndexServer, serve

from alai import _artifacts, _fetch
from alai._artifacts import Artifact, ArtifactCache, ArtifactError
from alai._fetch import Fetcher


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

        def first(
            tarball: str, dest: str, shown: str, *, expected: tuple[str, Version] | None, limit: int
        ) -> tuple[str, Version]:
            _ = extract(tarball, cache.path(item.cksum), shown, expected=expected, limit=limit)
            return extract(tarball, dest, shown, expected=expected, limit=limit)

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

        with pytest.raises(ArtifactError, match="larger than the 10 bytes expected"):
            _ = cache.get(Artifact(item.name, item.version, url=item.url, cksum=item.cksum, size=10))
        assert leftovers(cache) == []

    def test_missing(self, tmp_path: Path, cache: ArtifactCache) -> None:
        url = (tmp_path / "nope.tar.gz").as_uri()

        with pytest.raises(ArtifactError, match=r"can't download ns\.coll 1\.0\.0: .*not found"):
            _ = cache.get(Artifact("ns.coll", Version("1.0.0"), url=url, cksum="0" * 64, size=1))

    def test_unreadable(self, tmp_path: Path, cache: ArtifactCache) -> None:
        url = tmp_path.as_uri()

        with pytest.raises(ArtifactError, match=rf"can't download ns\.coll 1\.0\.0: {tmp_path}: Is a directory$"):
            _ = cache.get(Artifact("ns.coll", Version("1.0.0"), url=url, cksum="0" * 64, size=1))

    @pytest.mark.parametrize(
        ("status", "message"),
        [(404, r"not found \(HTTP 404\)"), (410, r"not found \(HTTP 410\)"), (403, "HTTP 403 Forbidden")],
    )
    def test_error_status(self, tmp_path: Path, cache: ArtifactCache, status: int, message: str) -> None:
        server: IndexServer
        for server in serve(tmp_path):
            server.responses["/x.tar.gz"] = [(status, {}, b"")]
            url = f"{server.url}x.tar.gz"

            with pytest.raises(ArtifactError, match=rf"can't download ns\.coll 1\.0\.0: {url}: {message}$"):
                _ = cache.get(Artifact("ns.coll", Version("1.0.0"), url=url, cksum="0" * 64, size=1))
        assert leftovers(cache) == []

    def test_connection_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Retried without waiting: the client takes the backoff when the Fetcher is made, so it's made after this.
        monkeypatch.setattr(_fetch, "_BACKOFF", 0.0)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        url = f"http://127.0.0.1:{port}/x.tar.gz"

        with (
            Fetcher(user_agent="t") as fetcher,
            pytest.raises(ArtifactError, match=rf"can't download ns\.coll 1\.0\.0: {url}: can't connect: "),
        ):
            _ = ArtifactCache(str(tmp_path / "cache"), fetcher).get(
                Artifact("ns.coll", Version("1.0.0"), url=url, cksum="0" * 64, size=1)
            )

    def test_offline_miss(self, tmp_path: Path) -> None:
        with Fetcher(user_agent="t", offline=True) as fetcher:
            cache = ArtifactCache(str(tmp_path / "cache"), fetcher)

            with pytest.raises(ArtifactError, match="isn't in the cache, and alai is offline"):
                _ = cache.get(Artifact("ns.coll", Version("1.0.0"), url="https://dl.example/x", cksum="0" * 64, size=1))

    @pytest.mark.parametrize("cksum", ["../../etc", "A" * 64, "0" * 63])
    def test_bad_cksum(self, cache: ArtifactCache, cksum: str) -> None:
        with pytest.raises(ArtifactError, match="isn't a SHA-256 checksum"):
            _ = cache.path(cksum)


def listed_file(name: str, sha256: str | None = "0" * 64) -> dict[str, object]:
    return {"name": name, "ftype": "file", "chksum_type": "sha256", "chksum_sha256": sha256}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def appended(item: Artifact, members: dict[str, bytes]) -> Artifact:
    """``item`` with ``members`` appended, as ``tar -r`` on the uncompressed tarball then gzip again does."""
    path = Path(item.url.removeprefix("file://"))
    buffer = io.BytesIO(gzip.decompress(path.read_bytes()))
    with tarfile.open(fileobj=buffer, mode="a") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    data = gzip.compress(buffer.getvalue())
    _ = path.write_bytes(data)
    return Artifact(item.name, item.version, url=item.url, cksum=sha256(data), size=len(data))


def manifest(name: str = "coll", version: str = "1.0.0") -> bytes:
    return json.dumps({"collection_info": {"namespace": "ns", "name": name, "version": version}}).encode()


class TestAppended:
    """A member appended with ``tar -r`` replaces the earlier one of the same name, as some published collections
    replace their MANIFEST.json: only the last is read and extracted."""

    def test_manifest(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = appended(
            artifact(tmp_path, manifest=json.loads(manifest(version="0.9.0"))), {"MANIFEST.json": manifest()}
        )

        path = Path(cache.get(item))

        assert (path / "MANIFEST.json").read_bytes() == manifest()

    def test_files_json_and_a_file(self, tmp_path: Path, cache: ArtifactCache) -> None:
        listed = [listed_file("README.md", sha256(b"new")), listed_file("added.txt", sha256(b"added"))]
        files_json = json.dumps({"files": listed, "format": 1}).encode()
        item = appended(artifact(tmp_path), {"README.md": b"new", "added.txt": b"added", "FILES.json": files_json})

        path = Path(cache.get(item))

        assert (path / "README.md").read_bytes() == b"new"
        assert (path / "added.txt").read_bytes() == b"added"
        assert (path / "FILES.json").read_bytes() == files_json

    def test_files_json_lists_an_earlier_file(self, tmp_path: Path, cache: ArtifactCache) -> None:
        # extra.txt is stored before the replacement that lists it, so it's only extracted by starting again.
        files = {"README.md": b"x", "extra.txt": b"e"}
        original = [listed_file("README.md", sha256(b"x"))]
        listed = [*original, listed_file("extra.txt", sha256(b"e"))]
        files_json = json.dumps({"files": listed, "format": 1}).encode()
        item = appended(artifact(tmp_path, files=files, listed=original), {"FILES.json": files_json})

        path = Path(cache.get(item))

        assert (path / "extra.txt").read_bytes() == b"e"

    def test_collection_of_a_tarball(self, tmp_path: Path, cache: ArtifactCache) -> None:
        # The collection a tarball is for, when it isn't known beforehand, is read from the same MANIFEST.json.
        item = appended(artifact(tmp_path, manifest=json.loads(manifest("other"))), {"MANIFEST.json": manifest()})

        path = Path(cache.add(item.url.removeprefix("file://"), item.cksum))

        assert (path / "MANIFEST.json").read_bytes() == manifest()


class TestExtractErrors:
    """What the cache does with a tarball the extractor refuses; what it refuses is test_extract_safety's."""

    def test_corrupt(self, tmp_path: Path, cache: ArtifactCache) -> None:
        item = artifact(tmp_path)
        path = Path(item.url.removeprefix("file://"))
        data = path.read_bytes()[:200]
        _ = path.write_bytes(data)
        cksum = hashlib.sha256(data).hexdigest()

        with pytest.raises(ArtifactError, match=r"ns\.coll 1\.0\.0: can't extract the artifact"):
            _ = cache.get(Artifact(item.name, item.version, url=item.url, cksum=cksum, size=len(data)))
        assert leftovers(cache) == []

    def test_not_gzipped(self, tmp_path: Path, cache: ArtifactCache) -> None:
        gzipped = artifact(tmp_path)
        plain = tmp_path / "plain.tar"
        with tarfile.open(gzipped.url.removeprefix("file://")) as src, tarfile.open(plain, "w") as dst:
            for member in src.getmembers():
                dst.addfile(member, src.extractfile(member))
        data = plain.read_bytes()
        item = Artifact(gzipped.name, gzipped.version, url=plain.as_uri(), cksum=sha256(data), size=len(data))

        with pytest.raises(ArtifactError, match=r"ns\.coll 1\.0\.0: can't extract the artifact"):
            _ = cache.get(item)
