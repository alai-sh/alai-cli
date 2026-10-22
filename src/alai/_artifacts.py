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
import threading
import typing as _t

from ._common import Error, Record
from ._output import trace
from ._semver import Version

if _t.TYPE_CHECKING:
    from ._fetch import Fetcher

LAYOUT = "collections-v1"
"""The cache directory for extracted collections; a new name if what's extracted, or how, ever changes."""
_CKSUM = re.compile(r"[0-9a-f]{64}")
GZIP_MAGIC = b"\x1f\x8b"
"""The bytes every gzip file starts with (RFC 1952). A tarball source must be gzipped, as every collection build is, so
one that isn't is refused before it's read or downloaded whole; tarfile could read other formats, if that's ever
wanted."""
MAX_ARCHIVE_SIZE = 1 << 30
"""The most a tarball named by URL may download, with no size known beforehand: 1 GiB, far beyond any published
collection, so a wrong or hostile response can't fill the disk."""
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


Report = _c.Callable[[Artifact, "str | None"], None]
"""Progress for an artifact: what it's doing (``downloading``, ``extracting``), or None when it's done."""


def _ignore(_artifact: Artifact, _state: str | None) -> None:
    pass


class ArtifactCache:
    """Extracted collections under ``cache_dir``, downloading with ``fetcher`` what isn't there yet.

    Without a ``fetcher`` it can only say what's cached, and doesn't load the HTTP library (``_fetch``) to do it.
    """

    def __init__(self, cache_dir: str, fetcher: Fetcher | None = None) -> None:
        self.root = os.path.join(cache_dir, LAYOUT)
        self._fetcher = fetcher
        # Read once, here, before any thread starts: reading the umask means setting it, which isn't thread-safe.
        self._umask = _umask()
        # One extraction at a time: it's CPU-bound Python, so under the GIL more at once is slower, not faster
        # (13.3 s for 97 collections on 16 threads, 7.0 s on one). Downloads still run in parallel meanwhile.
        self._extracting = threading.Lock()

    def path(self, cksum: str) -> str:
        """Where the collection with this artifact SHA-256 is, or would be, extracted."""
        if not _CKSUM.fullmatch(cksum):
            raise ArtifactError(f"'{cksum}' isn't a SHA-256 checksum")
        return os.path.join(self.root, cksum)

    def cached(self, cksum: str) -> str | None:
        """Where the collection with this artifact SHA-256 is extracted, or None if it isn't cached."""
        path = self.path(cksum)
        return path if os.path.isdir(path) else None

    def get(self, artifact: Artifact, report: Report | None = None) -> str:
        """The directory ``artifact`` is extracted to, downloading and extracting it first if it isn't cached.

        ``report``, for progress, is called with ``downloading``, ``waiting`` (downloaded, for its turn to extract) and
        ``extracting`` as each starts, and None once the artifact is done with, whether it worked or not.
        """
        target = self.path(artifact.cksum)
        if os.path.isdir(target):
            trace(f"{artifact}: cached at {target}")
            if report is not None:
                report(artifact, None)
            return target
        try:
            return self._fetch(artifact, target, report or _ignore)
        finally:
            if report is not None:
                report(artifact, None)

    def _fetch(self, artifact: Artifact, target: str, report: Report) -> str:
        try:
            os.makedirs(self.root, exist_ok=True)
            work = tempfile.mkdtemp(dir=self.root, prefix=".tmp-")
        except OSError as e:
            raise ArtifactError(f"can't write to the cache at {self.root}: {e.strerror or e}") from None
        try:
            tarball = os.path.join(work, "artifact.tar.gz")
            from ._fetch import FetchError

            if self._fetcher is None:
                raise ArtifactError(f"{artifact} isn't in the cache, and there's no fetcher to download it")
            report(artifact, "downloading")
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
            report(artifact, "waiting")
            with self._extracting:
                report(artifact, "extracting")
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

    def add(self, tarball: str, cksum: str) -> str:
        """The directory the collection tarball at ``tarball``, whose SHA-256 is ``cksum``, is extracted to,
        extracting it first if it isn't cached. Its collection needn't be known beforehand: it's the one its
        ``MANIFEST.json`` names, which the caller checks."""
        target = self.path(cksum)
        if os.path.isdir(target):
            return target
        try:
            os.makedirs(self.root, exist_ok=True)
            work = tempfile.mkdtemp(dir=self.root, prefix=".tmp-")
        except OSError as e:
            raise ArtifactError(f"can't write to the cache at {self.root}: {e.strerror or e}") from None
        try:
            return self._unpack(tarball, work, target, tarball)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def download(self, url: str) -> tuple[str, int, str]:
        """Download the collection tarball at ``url`` and extract it into the cache, for a source that names a URL
        rather than an index's artifact: its SHA-256, size, and the directory it's extracted to. Its collection is
        the one its ``MANIFEST.json`` names, which the caller checks."""
        from ._fetch import FetchError

        if self._fetcher is None:
            raise ArtifactError(f"{url} isn't in the cache, and there's no fetcher to download it")
        try:
            os.makedirs(self.root, exist_ok=True)
            work = tempfile.mkdtemp(dir=self.root, prefix=".tmp-")
        except OSError as e:
            raise ArtifactError(f"can't write to the cache at {self.root}: {e.strerror or e}") from None
        try:
            tarball = os.path.join(work, "artifact.tar.gz")
            try:
                digest, size = self._fetcher.download(
                    url,
                    tarball,
                    max_size=MAX_ARCHIVE_SIZE,
                    magic=GZIP_MAGIC,
                    kind="gzip file, so not a collection tarball",
                )
            except FetchError as e:
                raise ArtifactError(f"can't download {url}: {e}") from None
            target = self.path(digest)
            if not os.path.isdir(target):
                _ = self._unpack(tarball, work, target, url)
            return digest, size, target
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _unpack(self, tarball: str, work: str, target: str, shown: str) -> str:
        """Extract ``tarball``, said to be ``shown`` in messages, to ``target`` through ``work``."""
        name, version = artifact_collection(tarball, shown)
        extracted = os.path.join(work, "collection")
        try:
            with self._extracting:
                extract(tarball, extracted, name, version, umask=self._umask)
            try:
                os.rename(extracted, target)
            except OSError:
                # Another alai extracted the same artifact meanwhile; theirs is as good as ours.
                if not os.path.isdir(target):
                    raise
        except OSError as e:
            raise ArtifactError(f"can't extract {shown} into the cache: {e.strerror or e}") from None
        trace(f"{name} {version}: extracted to {target}")
        return target

    def get_all(self, artifacts: _c.Iterable[Artifact], report: Report | None = None) -> dict[str, str]:
        """Each artifact's extracted directory, by collection name, fetching those that aren't cached in parallel.

        Every one is attempted; if any fails, the first by name is raised once the rest have finished. ``report`` is
        as for ``get``.
        """
        from ._fetch import MAX_CONNECTIONS

        wanted = sorted(artifacts, key=lambda a: a.name)
        with concurrent.futures.ThreadPoolExecutor(MAX_CONNECTIONS, thread_name_prefix="alai-artifact") as pool:
            futures = {a.name: pool.submit(self.get, a, report) for a in wanted}
        return {name: future.result() for name, future in futures.items()}


def artifact_collection(tarball: str, shown: str) -> tuple[str, Version]:
    """The collection and version a tarball's ``MANIFEST.json`` names, for one whose collection isn't known yet, as
    ansible-galaxy reads a tarball's (``_get_meta_from_tar``). ``shown`` is where it came from, for messages."""
    from ._manifest import collection_name

    try:
        with tarfile.open(tarball, "r:*") as tar:
            member = tar.next()
            # A build writes MANIFEST.json first, so this rarely reads further than the first member.
            while member is not None and member.name != "MANIFEST.json":
                member = tar.next()
            if member is None:
                raise ArtifactError(f"{shown} has no MANIFEST.json, so it isn't a built collection")
            manifest = _read_json(tar, {"MANIFEST.json": member}, "MANIFEST.json", shown)
    except (tarfile.TarError, EOFError, OSError) as e:
        message = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
        raise ArtifactError(f"{shown} isn't a collection tarball: {message}") from None
    info = manifest.get("collection_info")
    info = _t.cast("dict[str, object]", info) if isinstance(info, dict) else {}
    namespace, short, text = info.get("namespace"), info.get("name"), info.get("version")
    if not isinstance(namespace, str) or not isinstance(short, str) or not isinstance(text, str):
        raise ArtifactError(f"{shown}: MANIFEST.json's collection_info has no namespace, name and version")
    try:
        return collection_name(f"{namespace}.{short}"), Version(text)
    except ValueError as e:
        raise ArtifactError(f"{shown}: MANIFEST.json: {e}") from None


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
            # Looked up by name once: tarfile's getmember searches every member, each time it's called. The last of a
            # repeated name wins, as getmember's does.
            members = {m.name: m for m in tar.getmembers()}
            _check_manifest(_read_json(tar, members, "MANIFEST.json", where), name, version, where)
            entries = _files_json(_read_json(tar, members, "FILES.json", where), where)
            wanted: list[tuple[tarfile.TarInfo, str, str, str | None]] = [
                (_member(members, special, where), special, "file", None) for special in ("MANIFEST.json", "FILES.json")
            ]
            for entry_name, ftype, sha256 in entries:
                if entry_name == ".":
                    continue
                _check_path(entry_name, where)
                wanted.append((_member(members, entry_name, where), entry_name, ftype, sha256))
            # In the order they're stored, so reading the compressed stream only ever moves forward: going back means
            # decompressing again from the start.
            for member, entry_name, ftype, sha256 in sorted(wanted, key=lambda w: w[0].offset):
                if ftype == "dir":
                    _extract_dir(member, entry_name, out)
                else:
                    _extract_file(tar, member, entry_name, out, sha256)
        # A link made later can change where an earlier one leads, so each is checked again once all exist.
        for entry_name, path in out.links:
            _ = out.check_inside(path, entry_name)
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
        self._plain: set[str] = set()
        """Parent directories already checked whose path has no symlink in it, so can't later lead anywhere else."""

    def path(self, entry: str) -> str:
        """Where ``entry`` goes, its parent directories made and checked to resolve inside the collection on disk."""
        path = os.path.join(self.dest, *entry.split("/"))
        parent = os.path.dirname(path)
        if parent not in self._plain:
            os.makedirs(parent, 0o755 & ~self.umask, exist_ok=True)
            if self.check_inside(parent, entry) == os.path.abspath(parent):
                self._plain.add(parent)
        return path

    def check_inside(self, path: str, entry: str) -> str:
        """Fail unless ``path`` resolves inside the collection on disk; return where it resolves to."""
        real = os.path.realpath(path)
        if real != self.real and not real.startswith(self.real + os.sep):
            raise ArtifactError(
                f"{self.where}: {entry} in the artifact leads outside the collection, through a symlink"
            )
        return real

    def symlink(self, target: str, path: str, entry: str) -> None:
        os.symlink(target, path)
        self.links.append((entry, path))


def _umask() -> int:
    mask = os.umask(0o022)
    _ = os.umask(mask)
    return mask


def _member(members: dict[str, tarfile.TarInfo], entry: str, where: str) -> tarfile.TarInfo:
    try:
        return members[entry]
    except KeyError:
        raise ArtifactError(f"{where}: the artifact doesn't contain {entry}, which FILES.json lists") from None


def _read_json(tar: tarfile.TarFile, members: dict[str, tarfile.TarInfo], entry: str, where: str) -> dict[str, object]:
    member = _member(members, entry, where)
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


def _extract_dir(member: tarfile.TarInfo, entry: str, out: _Target) -> None:
    where = out.where
    if member.type not in _DIR_TYPES:
        raise ArtifactError(f"{where}: {entry} in the artifact isn't a directory, as FILES.json says")
    path = out.path(entry)
    if member.issym():
        _check_link(member, entry, where)
        out.symlink(member.linkname, path, entry)
    elif not os.path.isdir(path):
        os.mkdir(path, 0o755 & ~out.umask)


def _extract_file(tar: tarfile.TarFile, member: tarfile.TarInfo, entry: str, out: _Target, sha256: str | None) -> None:
    where = out.where
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
