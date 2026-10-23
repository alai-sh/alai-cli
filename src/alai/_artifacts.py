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
import os
import shutil
import tempfile
import threading
import typing as _t

from ._common import SHA256_RE, ConfigError, Error, Record, worker_threads
from ._output import trace
from ._semver import Version

if _t.TYPE_CHECKING:
    from . import _native
    from ._fetch import Fetcher

LAYOUT = "collections-v1"
"""The cache directory for extracted collections; a new name if what's extracted, or how, ever changes."""
GZIP_MAGIC = b"\x1f\x8b"
"""The bytes every gzip file starts with (RFC 1952). A tarball source must be gzipped, as every collection build is, so
one that isn't is refused before it's read or downloaded whole."""
MAX_ARCHIVE_SIZE = 1 << 30
"""The most a tarball named by URL may download, with no size known beforehand: 1 GiB, far beyond any published
collection, so a wrong or hostile response can't fill the disk."""
MAX_UNPACKED_SIZE = 1 << 29
"""The most a tarball may unpack to, extracted or not, unless ``ALAI_MAX_UNPACKED_SIZE`` says otherwise: 512 MiB, about
20 times the largest of sivel.acd's 97 collections, so a small tarball can't fill the disk, or take hours to read. What
a hard link copies, and a symlink's checksum reads, counts too, as each costs the tarball only a header."""
MAX_UNPACKED_SIZE_ENV = "ALAI_MAX_UNPACKED_SIZE"


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
        self._limit = max_unpacked_size(os.environ)
        # How many extract at once, for get and add as get_all's pool limits its own: see worker_threads.
        self._extracting = threading.Semaphore(worker_threads())

    def path(self, cksum: str) -> str:
        """Where the collection with this artifact SHA-256 is, or would be, extracted."""
        if not SHA256_RE.fullmatch(cksum):
            raise ArtifactError(f"'{cksum}' isn't a SHA-256 checksum")
        return os.path.join(self.root, cksum)

    def cached(self, cksum: str) -> str | None:
        """Where the collection with this artifact SHA-256 is extracted, or None if it isn't cached."""
        path = self.path(cksum)
        return path if os.path.isdir(path) else None

    def get(self, artifact: Artifact, report: Report | None = None) -> str:
        """The directory ``artifact`` is extracted to, downloading and extracting it first if it isn't cached:
        ``get_all`` for one."""
        return self.get_all([artifact], report)[artifact.name]

    def _workdir(self) -> str:
        """A new directory in the cache to download or extract into, for the caller to remove."""
        try:
            os.makedirs(self.root, exist_ok=True)
            return tempfile.mkdtemp(dir=self.root, prefix=".tmp-")
        except OSError as e:
            raise ArtifactError(f"can't write to the cache at {self.root}: {e.strerror or e}") from None

    def _download(self, artifact: Artifact, report: Report) -> tuple[str, str]:
        """Download ``artifact`` into a new work directory in the cache, checked against the lock: the directory and
        the tarball in it, for ``_unpack_download``, which removes the directory."""
        from ._fetch import FetchError

        if self._fetcher is None:
            raise ArtifactError(f"{artifact} isn't in the cache, and there's no fetcher to download it")
        work = self._workdir()
        try:
            tarball = os.path.join(work, "artifact.tar.gz")
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
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        report(artifact, "waiting")
        return work, tarball

    def _unpack_download(self, artifact: Artifact, work: str, tarball: str, target: str, report: Report) -> str:
        """Extract ``artifact``'s downloaded ``tarball`` and rename it into place as ``target``, removing ``work``."""
        try:
            self._unpack(
                tarball,
                work,
                target,
                str(artifact),
                (artifact.name, artifact.version),
                started=lambda: report(artifact, "extracting"),
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return target

    def add(self, tarball: str, cksum: str) -> str:
        """The directory the collection tarball at ``tarball``, whose SHA-256 is ``cksum``, is extracted to,
        extracting it first if it isn't cached. Its collection needn't be known beforehand: it's the one its
        ``MANIFEST.json`` names, which the caller checks."""
        target = self.path(cksum)
        if os.path.isdir(target):
            return target
        work = self._workdir()
        try:
            self._unpack(tarball, work, target, tarball, None)
            return target
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def download(self, url: str) -> tuple[str, int, str]:
        """Download the collection tarball at ``url`` and extract it into the cache, for a source that names a URL
        rather than an index's artifact: its SHA-256, size, and the directory it's extracted to. Its collection is
        the one its ``MANIFEST.json`` names, which the caller checks."""
        from ._fetch import FetchError

        if self._fetcher is None:
            raise ArtifactError(f"{url} isn't in the cache, and there's no fetcher to download it")
        work = self._workdir()
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
                self._unpack(tarball, work, target, url, None)
            return digest, size, target
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _unpack(
        self,
        tarball: str,
        work: str,
        target: str,
        shown: str,
        expected: tuple[str, Version] | None,
        *,
        started: _c.Callable[[], None] | None = None,
    ) -> None:
        """Extract ``tarball``, said to be ``shown`` in messages, to ``target`` through ``work``, as ``extract`` does
        with ``expected``, once it's this one's turn to extract, which ``started`` is told of."""
        extracted = os.path.join(work, "collection")
        try:
            with self._extracting:
                if started is not None:
                    started()
                name, version = extract(tarball, extracted, shown, expected=expected, limit=self._limit)
            try:
                os.rename(extracted, target)
            except OSError:
                # Another alai extracted the same artifact meanwhile; theirs is as good as ours.
                if not os.path.isdir(target):
                    raise
        except OSError as e:
            raise ArtifactError(f"can't extract {shown} into the cache: {e.strerror or e}") from None
        trace(f"{name} {version}: extracted to {target}")

    def get_all(self, artifacts: _c.Iterable[Artifact], report: Report | None = None) -> dict[str, str]:
        """Each artifact's extracted directory, by collection name, fetching those that aren't cached in parallel.

        Every one is attempted; if any fails, the first by name is raised once the rest have finished. ``report`` is
        as for ``get``.
        """
        from ._fetch import MAX_CONNECTIONS

        wanted = sorted(artifacts, key=lambda a: a.name)
        tell = report or _ignore

        def fetch(artifact: Artifact) -> str | concurrent.futures.Future[str]:
            target = self.path(artifact.cksum)
            if os.path.isdir(target):
                trace(f"{artifact}: cached at {target}")
                tell(artifact, None)
                return target
            try:
                work, tarball = self._download(artifact, tell)
            except BaseException:
                tell(artifact, None)
                raise
            return extractors.submit(unpack, artifact, work, tarball, target)

        def unpack(artifact: Artifact, work: str, tarball: str, target: str) -> str:
            try:
                return self._unpack_download(artifact, work, tarball, target, tell)
            finally:
                tell(artifact, None)

        # Downloads never wait on extraction: each hands its tarball to the extraction pool and takes the next. The
        # download pool is shut down first, so every tarball is handed over before the extraction pool waits.
        with (
            concurrent.futures.ThreadPoolExecutor(worker_threads(), thread_name_prefix="alai-extract") as extractors,
            concurrent.futures.ThreadPoolExecutor(MAX_CONNECTIONS, thread_name_prefix="alai-download") as pool,
        ):
            downloads = {a.name: pool.submit(fetch, a) for a in wanted}
        # Every artifact has been attempted by now, so the first failure by name is the one raised.
        paths: dict[str, str] = {}
        for name, download in downloads.items():
            result = download.result()
            paths[name] = result if isinstance(result, str) else result.result()
        return paths


def extract(
    tarball: str,
    dest: str,
    shown: str,
    *,
    expected: tuple[str, Version] | None = None,
    limit: int = MAX_UNPACKED_SIZE,
) -> tuple[str, Version]:
    """Extract a collection artifact into the new directory ``dest``, as ``ansible-galaxy collection install`` does,
    and return the collection and version its ``MANIFEST.json`` names: with ``expected``, as a lock gives them, they
    must be those; without, as ansible-galaxy reads a tarball's (``_get_meta_from_tar``), they're checked once it's
    extracted, into a directory the caller discards on an error. ``shown`` is where it came from, for messages.

    Only ``MANIFEST.json``, ``FILES.json`` and the entries ``FILES.json`` lists are extracted, each file checked
    against its SHA-256 there. Every path and symlink must stay inside the collection, checked on disk as well as by
    name, since one symlink can lead another out. Files are ``0644``, or ``0755`` if the tarball marks them
    executable, and directories ``0755``, both masked by the process umask: not the tarball's own modes. More than
    ``limit`` bytes unpacked from the tarball is refused, counting what a hard link copies and a symlink's checksum
    reads; 0 is no limit. The work is the extension's (``_native.extract_collection``); the design is in
    ``design/performance.md``.
    """
    # Imported here, so commands that never extract don't load it.
    from . import _native
    from ._manifest import collection_name

    want = None
    if expected is not None:
        namespace, short = expected[0].split(".")
        want = (namespace, short, expected[1].text)
    try:
        namespace, short, text = _native.extract_collection(tarball, dest, limit, want)
    except _native.ExtractError as e:
        kind, entry, _ = e.args
        if want is None and kind == "missing" and entry == "MANIFEST.json":
            raise ArtifactError(f"{shown} has no MANIFEST.json, so it isn't a built collection") from None
        raise ArtifactError(f"{shown}: {_message(e)}") from None
    except OSError as e:
        raise ArtifactError(f"{shown}: can't extract the artifact: {e.strerror or e}") from None
    if expected is not None:
        return expected
    try:
        return collection_name(f"{namespace}.{short}"), Version(text)
    except ValueError as e:
        raise ArtifactError(f"{shown}: MANIFEST.json: {e}") from None


_MESSAGES = {
    "corrupt": "can't extract the artifact: {detail}",
    "missing": "the artifact doesn't contain {entry}, which FILES.json lists",
    "not_file": "{entry} in the artifact isn't a file, as FILES.json says",
    "not_dir": "{entry} in the artifact isn't a directory, as FILES.json says",
    "path": "the artifact has a path outside the collection: {entry}",
    "link": "{entry} in the artifact links outside the collection, to {detail}",
    "escape": "{entry} in the artifact leads outside the collection, through a symlink",
    "unreadable": "{entry} in the artifact can't be read",
    "checksum": "{entry} in the artifact doesn't match its checksum in FILES.json",
    "json": "{entry} in the artifact isn't valid JSON: {detail}",
    "not_object": "{entry} in the artifact isn't a JSON object",
    "manifest": "the artifact's MANIFEST.json is for {detail}",
    "manifest_fields": "MANIFEST.json's collection_info has no namespace, name and version",
    "files_list": "the artifact's FILES.json has no 'files' list",
    "files_entry": "the artifact's FILES.json has an unreadable entry: {detail}",
    "files_checksum": "the artifact's FILES.json has no checksum for {entry}",
    "files_special": "the artifact's FILES.json lists {entry}, which a build never does",
    "files_twice": "the artifact's FILES.json lists {entry} more than once, differently",
    "too_large": (
        "can't extract the artifact: it unpacks to more than {detail} bytes; set "
        + MAX_UNPACKED_SIZE_ENV
        + " to a larger number of bytes, or 0 for no limit, if it's genuine"
    ),
}


def _message(e: _native.ExtractError) -> str:
    kind, entry, detail = e.args
    return _MESSAGES[kind].format(entry=entry, detail=detail)


def max_unpacked_size(environ: _c.Mapping[str, str]) -> int:
    """The most a tarball may unpack to, in bytes, from ``ALAI_MAX_UNPACKED_SIZE`` (0 for no limit) or the default."""
    value = environ.get(MAX_UNPACKED_SIZE_ENV) or ""
    if not value:
        return MAX_UNPACKED_SIZE
    if not value.isdigit():
        raise ConfigError(f"{MAX_UNPACKED_SIZE_ENV}: expected a number of bytes, or 0 for no limit, got '{value}'")
    return int(value)
