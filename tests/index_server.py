# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A local HTTP server for the fetch and index tests: serves a directory as an index host would.

Files get an ETag from their content and a 304 for a matching If-None-Match, and are gzipped when the client accepts
it. ``responses`` scripts other answers for a path, such as a redirect or an error, and every request is recorded.
"""

from __future__ import annotations

import collections.abc as _c
import gzip
import hashlib
import http.server
import ssl
import sys
import threading
import time
import urllib.parse
from pathlib import Path


class Request:
    def __init__(self, target: str, path: str, headers: dict[str, str], method: str = "GET") -> None:
        self.method = method
        self.target = target
        """The request target as sent: a path, or an absolute URL when the server is used as a proxy."""
        self.path = path
        self.headers = headers
        self.arrived = time.monotonic()


Response = tuple[int, dict[str, str], bytes]


class IndexServer:
    def __init__(self, root: Path, tls: ssl.SSLContext | None = None) -> None:
        self.root = root
        self.tls = tls
        self.requests: list[Request] = []
        self.responses: dict[str, list[Response]] = {}
        """Scripted answers by path, used in order; once a path's list is empty its file is served again."""
        self.compress = True
        self.delay = 0.0
        """Seconds to wait before answering, for timeouts."""
        self.delays: dict[str, float] = {}
        """Seconds to wait before answering, by path."""
        self.closed = 0
        """Connections that have ended, by the client closing them."""
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, so connection reuse is exercised

            def finish(self) -> None:
                super().finish()
                server.closed += 1

            def do_GET(self) -> None:
                path = urllib.parse.urlsplit(self.path).path
                server.requests.append(Request(self.path, path, {k.lower(): v for k, v in self.headers.items()}))
                if delay := server.delays.get(path, server.delay):
                    time.sleep(delay)
                scripted = server.responses.get(path)
                if scripted:
                    self._send(*scripted.pop(0))
                    return
                file = server.root / path.lstrip("/")
                if not file.is_file():
                    self._send(404, {}, b"not found")
                    return
                body = file.read_bytes()
                etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
                if self.headers.get("If-None-Match") == etag:
                    self._send(304, {"ETag": etag}, b"")
                    return
                headers = {"ETag": etag}
                if server.compress and "gzip" in self.headers.get("Accept-Encoding", ""):
                    body = gzip.compress(body)
                    headers["Content-Encoding"] = "gzip"
                self._send(200, headers, body)

            def do_HEAD(self) -> None:
                # Only recorded and answered by whether the file exists: the fetcher's HEAD is to connect, not to read.
                path = urllib.parse.urlsplit(self.path).path
                headers = {k.lower(): v for k, v in self.headers.items()}
                server.requests.append(Request(self.path, path, headers, "HEAD"))
                self.send_response(200 if (server.root / path.lstrip("/")).is_file() else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                _ = self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self._server = _Server(("127.0.0.1", 0), Handler)
        if tls is not None:
            self._server.socket = tls.wrap_socket(self._server.socket, server_side=True)
        # A short poll interval, as shutdown() waits for the next poll: the default 0.5 s is paid by every test.
        self._thread = threading.Thread(target=self._server.serve_forever, args=(0.01,), daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"{'https' if self.tls else 'http'}://{host!s}:{port}/"

    def paths(self) -> list[str]:
        return [r.path for r in self.requests]

    def wait_for(self, path: str, timeout: float = 10.0) -> bool:
        """Whether ``path`` is requested within ``timeout`` seconds, for a request made in the background, such as a
        prefetch nothing waits for, which can arrive after the call that started it has returned."""
        deadline = time.monotonic() + timeout
        while path not in self.paths():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def __enter__(self) -> IndexServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        # A client that hung up before the answer was written, as in the timeout tests or when a test's Fetcher is
        # closed, isn't a failure of the server. Anything else is still printed.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)  # pyright: ignore[reportArgumentType]


def serve(root: Path, tls: ssl.SSLContext | None = None) -> _c.Iterator[IndexServer]:
    """For a pytest fixture: ``yield from serve(path)``."""
    with IndexServer(root, tls) as server:
        yield server
