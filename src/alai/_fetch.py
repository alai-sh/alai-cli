# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Fetching files from ``http(s)://`` and ``file://`` URLs, with an on-disk cache revalidated by ETag.

Only commands that use the network import this: urllib3 and its imports cost ~40 ms. Requests run on a pool of threads
sharing urllib3's connection pool, so callers can start many fetches and wait on them as they need each one. The design
is in ``design/index-client.md``.
"""

from __future__ import annotations

import collections.abc as _c
import concurrent.futures
import contextlib
import hashlib
import json
import os
import secrets
import ssl
import tempfile
import threading
import urllib.parse
import urllib.request

import urllib3

from ._common import Error
from ._output import debug, trace, warn

MAX_CONNECTIONS = 16
"""Concurrent requests, and connections kept open, per host."""
_MAX_REDIRECTS = 10
_REDIRECTS = frozenset((301, 302, 303, 307, 308))
_NOT_FOUND = frozenset((404, 410, 451))
_CACHE_VERSION = 1
# Connection errors and these statuses are retried with backoff (0.5 s, 1 s, 2 s), honouring Retry-After.
_RETRY = urllib3.Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET",)),
    raise_on_status=False,
)
_TIMEOUT = urllib3.Timeout(connect=15, read=60)
_CHUNK = 256 * 1024


class FetchError(Error):
    """A file couldn't be fetched: a network error, an HTTP error status, or a miss in the cache when offline."""


class Fetcher:
    """Fetches URLs on a pool of threads, each URL at most once.

    ``cache_path`` is where a URL's last response is kept, with its ETag. Each fetch revalidates it with
    ``If-None-Match``, as the index spec asks for every resolve, unless ``offline``, which reads only the cache.
    ``system_certs`` verifies TLS certificates with the operating system's trust store (see ``tls_context``).
    """

    def __init__(
        self,
        *,
        user_agent: str,
        offline: bool = False,
        system_certs: bool = True,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self.offline = offline
        self._headers = urllib3.make_headers(user_agent=user_agent, accept_encoding=True)
        self._max_connections = max_connections
        self._tls = tls_context(system_certs=system_certs)
        if keylog := _keylog_file():
            warn(f"SSLKEYLOGFILE is set: TLS session keys are being written to {keylog}")
        # block makes a thread wait for a free connection rather than open one it then throws away.
        self._pool = urllib3.PoolManager(
            num_pools=4, maxsize=max_connections, block=True, timeout=_TIMEOUT, ssl_context=self._tls
        )
        self._proxies: dict[str, urllib3.ProxyManager] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_connections, thread_name_prefix="alai-fetch")
        self._lock = threading.Lock()
        self._fetches: dict[str, concurrent.futures.Future[bytes | None]] = {}

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._executor.shutdown(cancel_futures=True)
        self._pool.clear()
        for proxy in self._proxies.values():
            proxy.clear()

    def fetch(self, url: str, cache_path: str | None) -> concurrent.futures.Future[bytes | None]:
        """Start fetching ``url``, or return the fetch already started. Its result is the body, or None if the URL
        doesn't exist; a failure raises FetchError from ``result()``."""
        with self._lock:
            future = self._fetches.get(url)
            if future is None:
                future = self._fetches[url] = self._executor.submit(self._get, url, cache_path)
            return future

    def get(self, url: str, cache_path: str | None) -> bytes | None:
        """Fetch ``url`` and wait for it: the body, or None if it doesn't exist."""
        return self.fetch(url, cache_path).result()

    def cached(self, cache_path: str | None) -> bytes | None:
        """The body cached at ``cache_path``, without a request, or None if there's none."""
        entry = _read_cache(cache_path) if cache_path else None
        return entry[1] if entry else None

    def refresh(self, url: str, cache_path: str | None) -> bytes | None:
        """Fetch ``url`` again, past any cache between here and its host, and cache the result as ``url``.

        For a copy known to be out of date, such as one a CDN kept after the file changed. A query string nobody
        could predict makes the request one no cache holds; the index spec requires hosts to ignore it. Offline, or
        for a file URL, this is ``get``.
        """
        if self.offline or url.startswith("file:"):
            return self.get(url, cache_path)
        unique = f"{url}{'&' if '?' in url else '?'}r={secrets.token_hex(8)}"
        return self._executor.submit(self._get, url, cache_path, unique).result()

    def download(self, url: str, path: str, *, max_size: int) -> tuple[str, int]:
        """Download ``url`` to the new file ``path``, waiting for it, and return the SHA-256 and size of what was
        written.

        For artifacts: streamed rather than held in memory, and never cached here, since the caller keeps what it
        extracts. It stops once more than ``max_size`` bytes arrive, so a wrong or hostile response can't fill the disk.
        Not found and offline are FetchErrors, as the caller only asks for what a lock lists.
        """
        digest = hashlib.sha256()
        size = 0
        try:
            with open(path, "xb") as out:
                for chunk in self._chunks(url):
                    size += len(chunk)
                    if size > max_size:
                        raise FetchError(f"{url}: larger than the {max_size} bytes the index lists")
                    digest.update(chunk)
                    _ = out.write(chunk)
        except OSError as e:
            raise FetchError(f"can't write {path}: {e.strerror or e}") from None
        trace(f"GET {url}: {size} bytes")
        return digest.hexdigest(), size

    def _chunks(self, url: str) -> _c.Iterator[bytes]:
        if url.startswith("file:"):
            path = _file_path(url)
            try:
                with open(path, "rb") as fd:
                    while chunk := fd.read(_CHUNK):
                        yield chunk
            except FileNotFoundError:
                raise FetchError(f"{path}: not found") from None
            except OSError as e:
                raise FetchError(f"{path}: {e.strerror or e}") from None
            return
        if self.offline:
            raise FetchError(f"{url} isn't in the cache, and alai is offline")
        _, response = self._request(url, dict(self._headers), preload_content=False)
        try:
            if response.status in _NOT_FOUND:
                raise FetchError(f"{url}: not found (HTTP {response.status})")
            if response.status == 401:
                raise FetchError(f"{url}: credentials required (HTTP 401)")
            if response.status != 200:
                raise FetchError(f"{url}: HTTP {response.status}{f' {response.reason}' if response.reason else ''}")
            try:
                yield from response.stream(_CHUNK)
            except urllib3.exceptions.HTTPError as e:
                raise FetchError(f"{url}: {_describe(e)}") from None
        finally:
            response.release_conn()

    def _get(self, url: str, cache_path: str | None, request_url: str | None = None) -> bytes | None:
        """Fetch ``url``, revalidating its cached copy. ``request_url`` is what to request, when it isn't ``url``."""
        if url.startswith("file:"):
            return _read_file(url)

        cached = _read_cache(cache_path) if cache_path else None
        if self.offline:
            if cached is None:
                raise FetchError(f"{url} isn't in the cache, and alai is offline")
            trace(f"GET {url}: from the cache, offline")
            return cached[1]

        headers = dict(self._headers)
        if cached and cached[0]:
            headers["If-None-Match"] = cached[0]
        requested = request_url or url
        final_url, response = self._request(requested, headers)

        if response.status == 304 and cached:
            trace(f"GET {requested}: 304, the cached copy is current")
            return cached[1]
        if response.status == 200:
            trace(
                f"GET {requested}: 200, {len(response.data)} bytes"
                + (f" from {final_url}" if final_url != requested else "")
            )
            if cache_path:
                _write_cache(cache_path, url, response.headers.get("ETag"), response.data)
            return response.data
        if response.status in _NOT_FOUND:
            trace(f"GET {requested}: {response.status}, not found")
            if cached and cache_path:
                _remove(cache_path)
            return None
        if response.status == 401:
            raise FetchError(f"{url}: credentials required (HTTP 401)")
        raise FetchError(f"{url}: HTTP {response.status}{f' {response.reason}' if response.reason else ''}")

    def _request(
        self, url: str, headers: dict[str, str], *, preload_content: bool = True
    ) -> tuple[str, urllib3.BaseHTTPResponse]:
        """GET ``url``, following redirects but never from ``https`` to anything else. Without ``preload_content``, the
        body is left to stream and the caller releases the connection."""
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                response = self._manager(url).request(
                    "GET", url, headers=headers, redirect=False, retries=_RETRY, preload_content=preload_content
                )
            except urllib3.exceptions.HTTPError as e:
                proxy = proxy_for(url)
                raise FetchError(
                    f"{url}: {_describe(e)}" + (f" (through the proxy {proxy})" if proxy else "")
                ) from None
            location = response.headers.get("Location")
            if response.status not in _REDIRECTS or not location:
                return url, response
            if not preload_content:
                response.drain_conn()
                response.release_conn()
            target = urllib.parse.urljoin(url, location)
            if url.startswith("https://") and not target.startswith("https://"):
                raise FetchError(f"{url}: refusing to follow a redirect from https to {target}")
            trace(f"GET {url}: {response.status}, redirected to {target}")
            url = target
        raise FetchError(f"{url}: more than {_MAX_REDIRECTS} redirects")

    def _manager(self, url: str) -> urllib3.PoolManager:
        """The connection pool for ``url``: direct, or through the proxy the environment names for it."""
        proxy = proxy_for(url)
        if proxy is None:
            return self._pool
        with self._lock:
            manager = self._proxies.get(proxy)
            if manager is None:
                manager = self._proxies[proxy] = urllib3.ProxyManager(
                    proxy,
                    num_pools=4,
                    maxsize=self._max_connections,
                    block=True,
                    timeout=_TIMEOUT,
                    ssl_context=self._tls,
                    proxy_ssl_context=self._tls,
                )
            return manager


def tls_context(*, system_certs: bool) -> ssl.SSLContext | None:
    """The TLS context for connections, or None for urllib3's own, which verifies with OpenSSL's default paths.

    With ``system_certs``, a truststore context, which verifies with the operating system: the Security framework
    on macOS, CryptoAPI on Windows, and OpenSSL's system paths elsewhere. That's what sees roots a company installs
    in the OS, such as for TLS interception. An explicit ``SSL_CERT_FILE`` or ``SSL_CERT_DIR`` wins, since on macOS
    and Windows truststore doesn't use OpenSSL's verification and would ignore them.
    """
    if not system_certs or os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return None
    try:
        # Imported here, not at the top, because truststore itself raises ImportError on a Python without ssl or with
        # too old an OpenSSL: that falls back to OpenSSL's verification below, rather than breaking every import of
        # this module, and --no-system-certs never touches it.
        import truststore

        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except (ImportError, OSError) as e:
        debug(f"can't use the system trust store ({e}); verifying with OpenSSL's instead")
        return None
    # urllib3 applies SSLKEYLOGFILE only to a context it builds itself, so a context passed in has to have it here.
    if keylog := _keylog_file():
        context.keylog_filename = keylog
    return context


def _keylog_file() -> str | None:
    """Where ``SSLKEYLOGFILE`` asks for TLS session keys to be written, expanded as urllib3 expands it."""
    value = os.environ.get("SSLKEYLOGFILE")
    return os.path.expandvars(value) if value else None


def proxy_for(url: str) -> str | None:
    """The proxy URL the environment gives for ``url``, or None to connect directly.

    The usual variables, as curl and requests read them: ``HTTPS_PROXY`` and ``HTTP_PROXY`` by the URL's scheme,
    else ``ALL_PROXY``, in either case, with hosts in ``NO_PROXY`` connected to directly.
    """
    parts = urllib.parse.urlsplit(url)
    proxies = urllib.request.getproxies_environment()
    proxy = proxies.get(parts.scheme) or proxies.get("all")
    # The environment-only forms: proxy_bypass and getproxies also read macOS and Windows system settings.
    # proxy_bypass_environment is in every supported Python but not in typeshed.
    bypass: bool = urllib.request.proxy_bypass_environment(parts.netloc, proxies)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType]
    if not proxy or bypass:
        return None
    # A bare host:port, as some set HTTPS_PROXY, is an HTTP proxy.
    return proxy if "://" in proxy else f"http://{proxy}"


def _file_path(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        raise FetchError(f"{url}: a file URL can't name another host")
    return urllib.request.url2pathname(parts.path)


def _read_file(url: str) -> bytes | None:
    path = _file_path(url)
    try:
        with open(path, "rb") as fd:
            data = fd.read()
    except (FileNotFoundError, NotADirectoryError):
        trace(f"read {path}: not found")
        return None
    except OSError as e:
        raise FetchError(f"{path}: {e.strerror or e}") from None
    trace(f"read {path}: {len(data)} bytes")
    return data


def _read_cache(path: str) -> tuple[str | None, bytes] | None:
    """A cached response as ``(etag, body)``, or None if there's none or it's unreadable.

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
    return (etag if isinstance(etag, str) else None), body


def _write_cache(path: str, url: str, etag: str | None, body: bytes) -> None:
    # Written beside the target and renamed over it, so another alai reading it never sees half a file.
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                _ = f.write(json.dumps({"v": _CACHE_VERSION, "url": url, "etag": etag}).encode() + b"\n" + body)
            os.replace(temp, path)
        except BaseException:
            _remove(temp)
            raise
    except OSError as e:
        raise FetchError(f"can't write to the cache at {path}: {e.strerror or e}") from None


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def _describe(error: BaseException) -> str:
    """The underlying reason for a urllib3 error, without its repr of the connection object."""
    reason: BaseException = error
    while isinstance(reason, urllib3.exceptions.MaxRetryError) and reason.reason is not None:
        reason = reason.reason
    cause = reason.__cause__ or reason.__context__
    # NameResolutionError is a NewConnectionError, so it's checked first.
    if isinstance(reason, urllib3.exceptions.NameResolutionError) and isinstance(cause, OSError):
        return f"can't resolve the host name: {cause.strerror or cause}"
    if isinstance(reason, urllib3.exceptions.NewConnectionError) and isinstance(cause, OSError):
        return f"can't connect: {cause.strerror or cause}"
    if isinstance(reason, urllib3.exceptions.SSLError):
        return f"TLS error: {cause or reason}"
    if isinstance(reason, urllib3.exceptions.TimeoutError):
        return "timed out"
    if isinstance(reason, urllib3.exceptions.ProxyError):
        original = getattr(reason, "original_error", None)
        return f"proxy error: {_describe(original) if isinstance(original, BaseException) else reason}"
    return str(reason)
