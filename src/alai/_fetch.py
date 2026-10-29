# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Fetching files from ``http(s)://`` and ``file://`` URLs, with an on-disk cache revalidated by ETag.

Requests run on a pool of threads sharing one ``_native.HttpClient``, so callers can start many fetches and wait on them
as they need each one; over HTTP/2, a host's requests share one connection. The design is in
``design/index-client.md``.
"""

from __future__ import annotations

import collections.abc as _c
import concurrent.futures
import contextlib
import hashlib
import json
import os
import secrets
import threading

from . import _native
from ._common import Error, file_url_to_path, write_atomic
from ._config import Settings
from ._output import trace, warn

MAX_CONNECTIONS = 16
"""Concurrent downloads, and connections kept open per host over HTTP/1.1."""
MAX_REQUESTS = int(os.environ.get("_ALAI_FETCH_THREADS", "64"))
"""Concurrent index requests: the fetcher's threads, each blocked in the client without the GIL, so more cost little.
Over HTTP/2 they share a host's connection; a host that only speaks HTTP/1.1 gets one for each."""
_NOT_FOUND = frozenset((404, 410, 451))
_CACHE_VERSION = 1
# Connection errors and 429, 500, 502, 503 and 504 are retried with backoff (0.5 s, 1 s, 2 s), honouring Retry-After.
_RETRIES = 3
_BACKOFF = 0.5
# read is how long to wait for the server's next bytes, uv's default UV_HTTP_TIMEOUT: with three retries, a host that
# stalls keeps alai waiting at most about two minutes, not three as at 60 s.
_CONNECT_TIMEOUT = 15.0
_READ_TIMEOUT = 30.0
_CHUNK = 256 * 1024


class FetchError(Error):
    """A file couldn't be fetched: a network error, an HTTP error status, or a miss in the cache when offline."""


class Fetcher:
    """Fetches URLs on a pool of threads, each URL at most once.

    ``cache_path`` is where a URL's last response is kept, with its ETag. Each fetch revalidates it with
    ``If-None-Match``, as the index spec asks for every resolve, unless ``offline``, which reads only the cache.
    TLS certificates are verified by the operating system, or against only those ``SSL_CERT_FILE`` and
    ``SSL_CERT_DIR`` name if either is set.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        offline: bool = False,
        max_requests: int = MAX_REQUESTS,
    ) -> None:
        self.offline = offline
        # A client that can't be made, as on a system with no CA certificates, only fails the requests that need it:
        # offline, and for file URLs, none do.
        self._client: _native.HttpClient | None = None
        self._client_error = ""
        try:
            self._client = _native.HttpClient(
                user_agent,
                connect_timeout=_CONNECT_TIMEOUT,
                read_timeout=_READ_TIMEOUT,
                retries=_RETRIES,
                backoff=_BACKOFF,
                max_idle=MAX_CONNECTIONS,
            )
        except _native.HttpError as e:
            self._client_error = e.args[1]
        else:
            for problem in self._client.cert_warnings:
                warn(problem)
        if keylog := os.environ.get("SSLKEYLOGFILE"):
            warn(_keylog_warning(keylog))
        self._executor = concurrent.futures.ThreadPoolExecutor(max_requests, thread_name_prefix="alai-fetch")
        self._lock = threading.Lock()
        self._fetches: dict[str, concurrent.futures.Future[bytes | None]] = {}

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._executor.shutdown(cancel_futures=True)

    def fetch(
        self, url: str, cache_path: str | None, *, what: str | None = None
    ) -> concurrent.futures.Future[bytes | None]:
        """Start fetching ``url``, or return the fetch already started. Its result is the body, or None if the URL
        doesn't exist; a failure raises FetchError from ``result()``. ``what`` says what it's for in the trace, such
        as the collection an index file found by its SHA-256 belongs to, where the URL doesn't say."""
        with self._lock:
            future = self._fetches.get(url)
            if future is None:
                future = self._fetches[url] = self._executor.submit(self._get, url, cache_path, what=what)
            return future

    def get(self, url: str, cache_path: str | None, *, what: str | None = None) -> bytes | None:
        """Fetch ``url`` and wait for it: the body, or None if it doesn't exist."""
        return self.fetch(url, cache_path, what=what).result()

    def cached(self, cache_path: str | None) -> bytes | None:
        """The body cached at ``cache_path``, without a request, or None if there's none."""
        entry = _read_cache(cache_path) if cache_path else None
        return entry[1] if entry else None

    def refresh(self, url: str, cache_path: str | None, *, what: str | None = None) -> bytes | None:
        """Fetch ``url`` again, past any cache between here and its host, and cache the result as ``url``.

        For a copy known to be out of date, such as one a CDN kept after the file changed. A query string nobody
        could predict makes the request one no cache holds; the index spec requires hosts to ignore it. Offline, or
        for a file URL, this is ``get``.
        """
        if self.offline or url.startswith("file:"):
            return self.get(url, cache_path, what=what)
        unique = f"{url}{'&' if '?' in url else '?'}r={secrets.token_hex(8)}"
        return self._executor.submit(self._get, url, cache_path, unique, what=what).result()

    def download(
        self, url: str, path: str, *, max_size: int, magic: bytes = b"", kind: str = "file"
    ) -> tuple[str, int]:
        """Download ``url`` to the new file ``path``, waiting for it, and return the SHA-256 and size of what was
        written.

        For artifacts: streamed rather than held in memory, and never cached here, since the caller keeps what it
        extracts. It stops once more than ``max_size`` bytes arrive, so a wrong or hostile response can't fill the disk,
        and as soon as it's clear the file doesn't start with ``magic``, the bytes every ``kind`` of file starts with,
        so a response that's something else, such as an error page, isn't downloaded whole. Not found and offline are
        FetchErrors, as the caller only asks for what a lock or a source names.
        """
        if url.startswith("file:"):
            return self._copy(url, path, max_size=max_size, magic=magic, kind=kind)
        if self.offline:
            raise FetchError(f"{url} isn't in the cache, and alai is offline")
        try:
            status, reason, redirects, sha256, size = self._http(url).download(url, path, max_size, magic)
        except _native.HttpError as e:
            error_kind, _ = e.args
            if error_kind == "too_large":
                raise FetchError(f"{url}: larger than the {max_size} bytes expected") from e
            if error_kind == "not_magic":
                raise FetchError(f"{url}: not a {kind}") from e
            raise _transport_error(url, e, self._http(url).proxy(url)) from e
        except OSError as e:
            raise FetchError(f"can't write {path}: {e.strerror or e}") from e
        _trace_redirects(url, redirects)
        if status in _NOT_FOUND:
            raise FetchError(f"{url}: not found (HTTP {status})")
        if status != 200:
            raise _status_error(url, status, reason)
        trace(f"GET {url}: {size} bytes")
        return sha256, size

    def _copy(self, url: str, path: str, *, max_size: int, magic: bytes, kind: str) -> tuple[str, int]:
        """``download`` for a file URL."""
        digest = hashlib.sha256()
        size = 0
        start = b""
        try:
            with open(path, "xb") as out:
                for chunk in self._chunks(url):
                    size += len(chunk)
                    if size > max_size:
                        raise FetchError(f"{url}: larger than the {max_size} bytes expected")
                    if len(start) < len(magic):
                        start += chunk[: len(magic) - len(start)]
                        if not magic.startswith(start):
                            raise FetchError(f"{url}: not a {kind}")
                    digest.update(chunk)
                    out.write(chunk)
        except OSError as e:
            raise FetchError(f"can't write {path}: {e.strerror or e}") from e
        if len(start) < len(magic):
            raise FetchError(f"{url}: not a {kind}")
        trace(f"read {url}: {size} bytes")
        return digest.hexdigest(), size

    def _chunks(self, url: str) -> _c.Iterator[bytes]:
        path = _file_path(url)
        try:
            with open(path, "rb") as fd:
                while chunk := fd.read(_CHUNK):
                    yield chunk
        except FileNotFoundError as e:
            raise FetchError(f"{path}: not found") from e
        except OSError as e:
            raise FetchError(f"{path}: {e.strerror or e}") from e

    def preconnect(self, url: str) -> None:
        """Start connecting to ``url``'s host, for requests soon to follow; nothing if offline or not HTTPS."""
        if self._client is not None and not self.offline and url.startswith("https://"):
            self._client.preconnect(url)

    def _http(self, url: str) -> _native.HttpClient:
        if self._client is None:
            raise FetchError(
                f"{url}: TLS error: {self._client_error}\n\n"
                + "hint: install the system's CA certificates (the ca-certificates package), or name a bundle to "
                + "trust with SSL_CERT_FILE"
            )
        return self._client

    def _get(
        self, url: str, cache_path: str | None, request_url: str | None = None, *, what: str | None = None
    ) -> bytes | None:
        """Fetch ``url``, revalidating its cached copy. ``request_url`` is what to request, when it isn't ``url``."""
        if url.startswith("file:"):
            return _read_file(url, what)

        label = f" ({what})" if what else ""
        cached = _read_cache(cache_path, url) if cache_path else None
        if self.offline:
            if cached is None:
                raise FetchError(f"{url} isn't in the cache, and alai is offline")
            trace(f"GET {url}{label}: from the cache, offline")
            return cached[1]

        headers: list[tuple[str, str]] = []
        if cached and cached[0]:
            headers.append(("If-None-Match", cached[0]))
        requested = request_url or url
        try:
            status, reason, redirects, etag, body = self._http(requested).get(requested, headers)
        except _native.HttpError as e:
            raise _transport_error(requested, e, self._http(requested).proxy(requested)) from e
        _trace_redirects(requested, redirects, label)

        if status == 304 and cached:
            trace(f"GET {requested}{label}: 304, the cached copy is current")
            return cached[1]
        if status == 200:
            trace(
                f"GET {requested}{label}: 200, {len(body)} bytes" + (f" from {redirects[-1][1]}" if redirects else "")
            )
            if cache_path:
                _write_cache(cache_path, url, etag, body)
            return body
        if status in _NOT_FOUND:
            trace(f"GET {requested}{label}: {status}, not found")
            if cached and cache_path:
                _remove(cache_path)
            return None
        raise _status_error(url, status, reason)


def open_fetcher(settings: Settings) -> Fetcher:
    """A ``Fetcher`` as alai's commands make one: identified as alai, offline and verifying as ``settings`` say."""
    from . import __version__

    return Fetcher(user_agent=f"alai/{__version__}", offline=settings.offline)


def _trace_redirects(url: str, redirects: list[tuple[int, str]], label: str = "") -> None:
    """A trace line for each redirect the client followed from ``url``, as it reports them."""
    for status, target in redirects:
        trace(f"GET {url}{label}: {status}, redirected to {target}")
        url = target


def _status_error(url: str, status: int, reason: str) -> FetchError:
    """A response that's neither what was asked for nor not found."""
    if status == 401:
        return FetchError(f"{url}: credentials required (HTTP 401)")
    return FetchError(f"{url}: HTTP {status}{f' {reason}' if reason else ''}")


def _file_path(url: str) -> str:
    try:
        return file_url_to_path(url)
    except ValueError as e:
        raise FetchError(f"{url}: {e}") from e


def _read_file(url: str, what: str | None = None) -> bytes | None:
    path = _file_path(url)
    label = f" ({what})" if what else ""
    try:
        with open(path, "rb") as fd:
            data = fd.read()
    except (FileNotFoundError, NotADirectoryError):
        trace(f"read {path}{label}: not found")
        return None
    except OSError as e:
        raise FetchError(f"{path}: {e.strerror or e}") from e
    trace(f"read {path}{label}: {len(data)} bytes")
    return data


def _read_cache(path: str, url: str | None = None) -> tuple[str | None, bytes] | None:
    """A cached response as ``(etag, body)``, or None if there's none or it's unreadable. With ``url``, the ETag only
    if the body came from there: an index file is cached by collection, and its URL changes with its SHA-256.

    The file is one JSON line of metadata, then the body as received, after decompression.
    """
    try:
        with open(path, "rb") as fd:
            data = fd.read()
    except OSError:
        return None
    header, sep, body = data.partition(b"\n")
    try:
        meta = json.loads(header)
    except ValueError:
        return None
    if not sep or not isinstance(meta, dict) or meta.get("v") != _CACHE_VERSION:
        return None
    etag = meta.get("etag")  # pyright: ignore[reportUnknownVariableType]
    if url is not None and meta.get("url") != url:
        etag = None
    return (etag if isinstance(etag, str) else None), body


def _write_cache(path: str, url: str, etag: str | None, body: bytes) -> None:
    # Written beside the target and renamed over it, so another alai reading it never sees half a file.
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_atomic(path, json.dumps({"v": _CACHE_VERSION, "url": url, "etag": etag}).encode() + b"\n" + body)
    except OSError as e:
        raise FetchError(f"can't write to the cache at {path}: {e.strerror or e}") from e


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def _keylog_warning(path: str) -> str:
    """The warning while ``SSLKEYLOGFILE`` is set: where TLS session keys go, or that they can't, which rustls only
    reports to a log alai doesn't show. Opened as rustls opens it, appending, and created owner-only."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600))
    except OSError as e:
        return (
            f"SSLKEYLOGFILE is set, but {path} can't be opened, so no TLS session keys are written: {e.strerror or e}"
        )
    return f"SSLKEYLOGFILE is set: TLS session keys are being written to {path}"


def _transport_error(url: str, error: _native.HttpError, proxy: str | None) -> FetchError:
    """A request that got no response, by its underlying reason, naming the ``proxy`` it went through."""
    kind, detail = error.args
    if kind == "redirect":
        return FetchError(f"{url}: {detail}")
    reason = {
        "dns": f"can't resolve the host name: {detail}",
        "connect": f"can't connect: {detail}",
        "tls": f"TLS error: {detail}",
        "timeout": "timed out",
    }.get(kind, detail)
    return FetchError(f"{url}: {reason}" + (f" (through the proxy {proxy})" if proxy else ""))
