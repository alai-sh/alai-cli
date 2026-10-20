# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The artifact cache: each collection version downloaded once, checked against its ``cksum``, and kept extracted.

An extracted collection is ``<cache_dir>/collections-v1/<cksum>/``, keyed by the artifact's SHA-256 alone, so the same
artifact from two indexes is stored once. It's trusted once it's there, as uv trusts its cache. The design is in
``design/install.md``.
"""

from __future__ import annotations

import collections.abc as _c
import concurrent.futures
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile
import typing as _t

from ._common import Error, Record
from ._fetch import MAX_CONNECTIONS, Fetcher, FetchError
from ._output import trace
from ._semver import Version

LAYOUT = "collections-v1"
"""The cache directory for extracted collections; a new name if what's extracted, or how, ever changes."""
_CKSUM = re.compile(r"[0-9a-f]{64}")
_FILE_TYPES = frozenset((tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.LNKTYPE, tarfile.SYMTYPE))
_DIR_TYPES = frozenset((tarfile.DIRTYPE, tarfile.SYMTYPE))


class ArtifactError(Error):
    """An artifact couldn't be downloaded, doesn't match its ``cksum``, or can't be extracted safely."""


class Artifact(Record):
    """One collection version to fetch: what a lock entry and its index's ``dl`` template give."""

    __slots__ = ("cksum", "name", "size", "url", "version")

    def __init__(self, name: str, version: Version, *, url: str, cksum: str, size: int) -> None:
        self.name = name
        self.version = version
        self.url = url
        self.cksum = cksum
        self.size = size

    def __str__(self) -> str:
        return f"{self.name} {self.version}"


class ArtifactCache:
    """Extracted collections under ``cache_dir``, downloading with ``fetcher`` what isn't there yet."""

    def __init__(self, cache_dir: str, fetcher: Fetcher) -> None:
        self.root = os.path.join(cache_dir, LAYOUT)
        self._fetcher = fetcher
        # Read once, here, before any thread starts: reading the umask means setting it, which isn't thread-safe.
        self._umask = _umask()

    def path(self, cksum: str) -> str:
        """Where the collection with this artifact SHA-256 is, or would be, extracted."""
        if not _CKSUM.fullmatch(cksum):
            raise ArtifactError(f"'{cksum}' isn't a SHA-256 checksum")
        return os.path.join(self.root, cksum)

    def get(self, artifact: Artifact) -> str:
        """The directory ``artifact`` is extracted to, downloading and extracting it first if it isn't cached."""
        target = self.path(artifact.cksum)
        if os.path.isdir(target):
            trace(f"{artifact}: cached at {target}")
            return target
        try:
            os.makedirs(self.root, exist_ok=True)
            work = tempfile.mkdtemp(dir=self.root, prefix=".tmp-")
        except OSError as e:
            raise ArtifactError(f"can't write to the cache at {self.root}: {e.strerror or e}") from None
        try:
            tarball = os.path.join(work, "artifact.tar.gz")
            try:
                digest, size = self._fetcher.download(artifact.url, tarball, max_size=artifact.size)
            except FetchError as e:
                raise ArtifactError(f"can't download {artifact}: {e}") from None
            if digest != artifact.cksum or size != artifact.size:
                raise ArtifactError(
                    f"{artifact} from {artifact.url} doesn't match the lock: it has SHA-256 {digest} and "
                    + f"{size} bytes, where the lock has {artifact.cksum} and {artifact.size} bytes"
                )
            extracted = os.path.join(work, "collection")
            extract(tarball, extracted, artifact.name, artifact.version, umask=self._umask)
            try:
                os.rename(extracted, target)
            except OSError:
                # Another alai extracted the same artifact meanwhile; theirs is as good as ours.
                if not os.path.isdir(target):
                    raise
        except OSError as e:
            raise ArtifactError(f"can't extract {artifact} into the cache: {e.strerror or e}") from None
        finally:
            shutil.rmtree(work, ignore_errors=True)
        trace(f"{artifact}: extracted to {target}")
        return target

    def get_all(self, artifacts: _c.Iterable[Artifact]) -> dict[str, str]:
        """Each artifact's extracted directory, by collection name, fetching those that aren't cached in parallel.

        Every one is attempted; if any fails, the first by name is raised once the rest have finished.
        """
        wanted = sorted(artifacts, key=lambda a: a.name)
        with concurrent.futures.ThreadPoolExecutor(MAX_CONNECTIONS, thread_name_prefix="alai-artifact") as pool:
            futures = {a.name: pool.submit(self.get, a) for a in wanted}
        return {name: future.result() for name, future in futures.items()}


def extract(tarball: str, dest: str, name: str, version: Version, *, umask: int) -> None:
    """Extract a collection artifact into the new directory ``dest``, as ``ansible-galaxy collection install`` does.

    Only ``MANIFEST.json``, ``FILES.json`` and the entries ``FILES.json`` lists are extracted, each file checked
    against its SHA-256 there, and ``MANIFEST.json`` must name this collection and version. Every path and symlink must
    stay inside the collection, checked on disk as well as by name, since one symlink can lead another out. Files are
    ``0644``, or ``0755`` if the tarball marks them executable, and directories ``0755``, both masked by ``umask``: not
    the tarball's own modes.
    """
    where = f"{name} {version}"
    os.mkdir(dest, 0o755 & ~umask)
    out = _Target(dest, umask, where)
    try:
        with tarfile.open(tarball, "r:*") as tar:
            _check_manifest(_read_json(tar, "MANIFEST.json", where), name, version, where)
            entries = _files_json(_read_json(tar, "FILES.json", where), where)
            for special in ("MANIFEST.json", "FILES.json"):
                _extract_file(tar, special, out, None)
            for entry_name, ftype, sha256 in entries:
                if entry_name == ".":
                    continue
                _check_path(entry_name, where)
                if ftype == "dir":
                    _extract_dir(tar, entry_name, out)
                else:
                    _extract_file(tar, entry_name, out, sha256)
        # A link made later can change where an earlier one leads, so each is checked again once all exist.
        for entry_name, path in out.links:
            out.check_inside(path, entry_name)
    except (tarfile.TarError, EOFError, OSError) as e:
        # A truncated or corrupt gzip stream raises EOFError or OSError (BadGzipFile) rather than a TarError.
        message = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
        raise ArtifactError(f"{where}: can't extract the artifact: {message}") from None


class _Target:
    """Where an artifact is being extracted, and the symlinks made in it so far."""

    def __init__(self, dest: str, umask: int, where: str) -> None:
        self.dest = dest
        self.real = os.path.realpath(dest)
        self.umask = umask
        self.where = where
        self.links: list[tuple[str, str]] = []

    def path(self, entry: str) -> str:
        """Where ``entry`` goes, its parent directories made and checked to resolve inside the collection on disk."""
        path = os.path.join(self.dest, *entry.split("/"))
        parent = os.path.dirname(path)
        os.makedirs(parent, 0o755 & ~self.umask, exist_ok=True)
        self.check_inside(parent, entry)
        return path

    def check_inside(self, path: str, entry: str) -> None:
        real = os.path.realpath(path)
        if real != self.real and not real.startswith(self.real + os.sep):
            raise ArtifactError(
                f"{self.where}: {entry} in the artifact leads outside the collection, through a symlink"
            )

    def symlink(self, target: str, path: str, entry: str) -> None:
        os.symlink(target, path)
        self.links.append((entry, path))


def _umask() -> int:
    mask = os.umask(0o022)
    _ = os.umask(mask)
    return mask


def _member(tar: tarfile.TarFile, entry: str, where: str) -> tarfile.TarInfo:
    try:
        return tar.getmember(entry)
    except KeyError:
        raise ArtifactError(f"{where}: the artifact doesn't contain {entry}, which FILES.json lists") from None


def _read_json(tar: tarfile.TarFile, entry: str, where: str) -> dict[str, object]:
    member = _member(tar, entry, where)
    stream = tar.extractfile(member) if member.isfile() else None
    if stream is None:
        raise ArtifactError(f"{where}: {entry} in the artifact isn't a file")
    with stream:
        try:
            data = json.loads(stream.read())
        except ValueError as e:
            raise ArtifactError(f"{where}: {entry} in the artifact isn't valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ArtifactError(f"{where}: {entry} in the artifact isn't a JSON object")
    return _t.cast("dict[str, object]", data)


def _check_manifest(manifest: dict[str, object], name: str, version: Version, where: str) -> None:
    info = manifest.get("collection_info")
    info = _t.cast("dict[str, object]", info) if isinstance(info, dict) else {}
    namespace, short = name.split(".")
    found = (info.get("namespace"), info.get("name"), info.get("version"))
    try:
        same = found[:2] == (namespace, short) and isinstance(found[2], str) and Version(found[2]) == version
    except ValueError:
        same = False
    if not same:
        shown = ".".join(str(f) for f in found[:2]) + f" {found[2]}"
        raise ArtifactError(f"{where}: the artifact's MANIFEST.json is for {shown}")


def _files_json(files: dict[str, object], where: str) -> list[tuple[str, str, str | None]]:
    """``(name, ftype, sha256)`` for each entry of a ``FILES.json``."""
    entries = files.get("files")
    if not isinstance(entries, list):
        raise ArtifactError(f"{where}: the artifact's FILES.json has no 'files' list")
    out: list[tuple[str, str, str | None]] = []
    for entry in _t.cast("list[object]", entries):
        fields = _t.cast("dict[str, object]", entry) if isinstance(entry, dict) else {}
        entry_name, ftype, sha256 = fields.get("name"), fields.get("ftype"), fields.get("chksum_sha256")
        if not isinstance(entry_name, str) or ftype not in ("file", "dir"):
            raise ArtifactError(f"{where}: the artifact's FILES.json has an unreadable entry: {entry!r}")
        if ftype == "file" and not isinstance(sha256, str):
            raise ArtifactError(f"{where}: the artifact's FILES.json has no checksum for {entry_name}")
        out.append((entry_name, _t.cast("str", ftype), sha256 if isinstance(sha256, str) else None))
    return out


def _check_path(entry: str, where: str) -> None:
    """Refuse a path that isn't plainly inside the collection: absolute, or with an empty, ``.`` or ``..`` part."""
    if entry.startswith("/") or "\\" in entry or any(part in ("", ".", "..") for part in entry.split("/")):
        raise ArtifactError(f"{where}: the artifact has a path outside the collection: {entry}")


def _check_link(member: tarfile.TarInfo, entry: str, where: str) -> None:
    target = posixpath.normpath(posixpath.join(posixpath.dirname(entry), member.linkname))
    if member.linkname.startswith("/") or target == ".." or target.startswith("../"):
        raise ArtifactError(f"{where}: {entry} in the artifact links outside the collection, to {member.linkname}")


def _extract_dir(tar: tarfile.TarFile, entry: str, out: _Target) -> None:
    where = out.where
    member = _member(tar, entry, where)
    if member.type not in _DIR_TYPES:
        raise ArtifactError(f"{where}: {entry} in the artifact isn't a directory, as FILES.json says")
    path = out.path(entry)
    if member.issym():
        _check_link(member, entry, where)
        out.symlink(member.linkname, path, entry)
    elif not os.path.isdir(path):
        os.mkdir(path, 0o755 & ~out.umask)


def _extract_file(tar: tarfile.TarFile, entry: str, out: _Target, sha256: str | None) -> None:
    where = out.where
    member = _member(tar, entry, where)
    if member.type not in _FILE_TYPES:
        raise ArtifactError(f"{where}: {entry} in the artifact isn't a file, as FILES.json says")
    if member.issym():
        _check_link(member, entry, where)
    path = out.path(entry)
    # A symlink's checksum in FILES.json is of the file it points to, as ansible-galaxy checks it.
    stream = tar.extractfile(member)
    if stream is None:
        raise ArtifactError(f"{where}: {entry} in the artifact can't be read")
    digest = hashlib.sha256()
    with stream:
        if member.issym():
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            out.symlink(member.linkname, path, entry)
        else:
            with open(path, "xb") as written:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    _ = written.write(chunk)
            mode = 0o755 if stat.S_IMODE(member.mode) & stat.S_IXUSR else 0o644
            os.chmod(path, mode & ~out.umask)
    if sha256 is not None and digest.hexdigest() != sha256:
        raise ArtifactError(f"{where}: {entry} in the artifact doesn't match its checksum in FILES.json")
