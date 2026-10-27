# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import errno
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import time
import typing as _t
from pathlib import Path

import pytest
from index_server import IndexServer, serve

from alai import _fetch, _native, _output
from alai._fetch import Fetcher, FetchError

if _t.TYPE_CHECKING:
    import trustme


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_fetch, "_BACKOFF", 0.0)


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    root.mkdir()
    (root / "a.json").write_text('{"a": 1}')
    return root


@pytest.fixture
def server(site: Path) -> _c.Iterator[IndexServer]:
    yield from serve(site)


@pytest.fixture(scope="session")
def ca() -> trustme.CA:
    # Skipped where it can't be imported: on uv's musl Python 3.10, cffi's musllinux wheel names its extension
    # linux-gnu, which that Python doesn't load, so cryptography, which trustme needs, can't be imported.
    return pytest.importorskip("trustme").CA()


@pytest.fixture(scope="session")
def ca_file(ca: trustme.CA, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The CA's certificate, for ``SSL_CERT_FILE``."""
    path = tmp_path_factory.mktemp("ca") / "ca.pem"
    ca.cert_pem.write_to_path(str(path))
    return path


@pytest.fixture
def tls_server(site: Path, ca: trustme.CA) -> _c.Iterator[IndexServer]:
    """``server`` over HTTPS, with a certificate for 127.0.0.1 from ``ca``, which nothing trusts unless told to."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("127.0.0.1").configure_cert(context)
    yield from serve(site, context)


@pytest.fixture
def fetcher() -> _c.Iterator[Fetcher]:
    with Fetcher(user_agent="alai-test/1") as f:
        yield f


def cache_file(tmp_path: Path, name: str = "a.json") -> str:
    return str(tmp_path / "cache" / name)


class TestHttp:
    def test_get_caches_with_etag(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        path = cache_file(tmp_path)

        assert fetcher.get(f"{server.url}a.json", path) == b'{"a": 1}'

        header, _, body = Path(path).read_bytes().partition(b"\n")
        meta = json.loads(header)
        assert meta["v"] == 1
        assert meta["url"] == f"{server.url}a.json"
        assert meta["etag"].startswith('"')
        assert body == b'{"a": 1}'
        request = server.requests[0]
        assert request.headers["user-agent"] == "alai-test/1"
        assert "gzip" in request.headers["accept-encoding"]
        assert "if-none-match" not in request.headers

    def test_revalidates_with_304(self, server: IndexServer, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        with Fetcher(user_agent="t") as first:
            _ = first.get(f"{server.url}a.json", path)
        etag = json.loads(Path(path).read_bytes().partition(b"\n")[0])["etag"]

        with Fetcher(user_agent="t") as second:
            assert second.get(f"{server.url}a.json", path) == b'{"a": 1}'

        assert server.requests[1].headers["if-none-match"] == etag

    def test_changed_file_replaces_cache(self, server: IndexServer, site: Path, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        with Fetcher(user_agent="t") as first:
            _ = first.get(f"{server.url}a.json", path)
        (site / "a.json").write_text('{"a": 2}')

        with Fetcher(user_agent="t") as second:
            assert second.get(f"{server.url}a.json", path) == b'{"a": 2}'

        assert Path(path).read_bytes().endswith(b'{"a": 2}')

    def test_uncompressed(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.compress = False

        assert fetcher.get(f"{server.url}a.json", None) == b'{"a": 1}'

    def test_each_url_fetched_once(self, server: IndexServer, fetcher: Fetcher) -> None:
        futures = [fetcher.fetch(f"{server.url}a.json", None) for _ in range(5)]

        assert {f.result() for f in futures} == {b'{"a": 1}'}
        assert server.paths() == ["/a.json"]

    def test_many_in_parallel(self, server: IndexServer, site: Path, fetcher: Fetcher, tmp_path: Path) -> None:
        for i in range(40):
            (site / f"{i}.txt").write_text(str(i))

        futures = [fetcher.fetch(f"{server.url}{i}.txt", cache_file(tmp_path, f"{i}.txt")) for i in range(40)]

        assert [f.result() for f in futures] == [str(i).encode() for i in range(40)]

    @pytest.mark.parametrize("status", [404, 410, 451])
    def test_not_found_removes_cache(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, status: int) -> None:
        path = cache_file(tmp_path)
        with Fetcher(user_agent="t") as first:
            _ = first.get(f"{server.url}a.json", path)
        server.responses["/a.json"] = [(status, {}, b"")]

        assert fetcher.get(f"{server.url}a.json", path) is None
        assert not Path(path).exists()

    def test_missing_file(self, server: IndexServer, fetcher: Fetcher) -> None:
        assert fetcher.get(f"{server.url}nope.json", None) is None

    def test_credentials_required(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(401, {}, b"")]

        with pytest.raises(FetchError, match=r"a\.json: credentials required \(HTTP 401\)"):
            _ = fetcher.get(f"{server.url}a.json", None)

    def test_error_status(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(403, {}, b"")]

        with pytest.raises(FetchError, match=r"a\.json: HTTP 403 Forbidden$"):
            _ = fetcher.get(f"{server.url}a.json", None)

    def test_retries_server_errors(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(503, {}, b""), (500, {}, b"")]

        assert fetcher.get(f"{server.url}a.json", None) == b'{"a": 1}'
        assert server.paths() == ["/a.json"] * 3

    def test_honours_retry_after(self, server: IndexServer, fetcher: Fetcher) -> None:
        # Backoff is 0 in these tests, so the wait is the header's.
        server.responses["/a.json"] = [(503, {"Retry-After": "1"}, b"")]

        assert fetcher.get(f"{server.url}a.json", None) == b'{"a": 1}'
        first, second = (r.arrived for r in server.requests)
        assert second - first >= 0.9

    def test_gives_up_after_retries(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(502, {}, b"")] * 4

        with pytest.raises(FetchError, match="HTTP 502"):
            _ = fetcher.get(f"{server.url}a.json", None)
        assert len(server.requests) == 4

    def test_follows_redirects(
        self,
        server: IndexServer,
        fetcher: Fetcher,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(_output, "verbosity", 2)
        server.responses["/old.json"] = [(301, {"Location": "/moved/a.json"}, b"")]
        server.responses["/moved/a.json"] = [(307, {"Location": f"{server.url}a.json"}, b"")]

        assert fetcher.get(f"{server.url}old.json", None) == b'{"a": 1}'

        assert server.paths() == ["/old.json", "/moved/a.json", "/a.json"]
        # Each hop is traced, with a relative Location resolved against the URL that sent it.
        assert capsys.readouterr().err == (
            f"trace: GET {server.url}old.json: 301, redirected to {server.url}moved/a.json\n"
            f"trace: GET {server.url}moved/a.json: 307, redirected to {server.url}a.json\n"
            f"trace: GET {server.url}old.json: 200, 8 bytes from {server.url}a.json\n"
        )

    def test_redirect_keeps_its_connection(self, server: IndexServer, fetcher: Fetcher) -> None:
        # A redirect's body is read before following it, so its HTTP/1.1 connection goes back to the pool rather than
        # being closed. Whether the next hop uses it, or a new one hyper opened meanwhile, is a race, so what's checked
        # is that nothing is closed. The body is bigger than arrives with the headers, as hyper keeps a connection
        # whose body it already has either way.
        server.responses["/old.json"] = [(301, {"Location": "/a.json"}, b"m" * (48 << 10))]

        assert fetcher.get(f"{server.url}old.json", None) == b'{"a": 1}'
        # Without the reading, hyper closes it as soon as the redirect is dropped.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            assert server.closed == 0
            time.sleep(0.01)

    def test_redirect_loop(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(302, {"Location": "/a.json"}, b"")] * 20

        with pytest.raises(FetchError, match="more than 10 redirects"):
            _ = fetcher.get(f"{server.url}a.json", None)

    def test_refuses_https_downgrade(
        self, tls_server: IndexServer, ca_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
        plain = tls_server.url.replace("https://", "http://")
        tls_server.responses["/a.json"] = [(302, {"Location": f"{plain}a.json"}, b"")]

        with (
            Fetcher(user_agent="t") as f,
            pytest.raises(FetchError, match=r"refusing to follow a redirect from https to http://127"),
        ):
            _ = f.get(f"{tls_server.url}a.json", None)

    def test_connection_refused(self, fetcher: Fetcher) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        with pytest.raises(FetchError, match=rf"127\.0\.0\.1:{port}/a\.json: can't connect"):
            _ = fetcher.get(f"http://127.0.0.1:{port}/a.json", None)


class TestDownload:
    BODY = bytes(range(256)) * 4096

    @pytest.fixture
    def artifact(self, site: Path) -> str:
        _ = (site / "a.tar.gz").write_bytes(self.BODY)
        return hashlib.sha256(self.BODY).hexdigest()

    @pytest.mark.parametrize("compress", [True, False])
    def test_streams_to_file(
        self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, artifact: str, compress: bool
    ) -> None:
        # Content-Encoding is the transport's: what's hashed and written is the file itself.
        server.compress = compress

        result = fetcher.download(f"{server.url}a.tar.gz", str(tmp_path / "out"), max_size=len(self.BODY))

        assert result == (artifact, len(self.BODY))
        assert (tmp_path / "out").read_bytes() == self.BODY

    def test_follows_redirects(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        server.responses["/dl"] = [(302, {"Location": "/a.tar.gz"}, b"moved")]

        assert fetcher.download(f"{server.url}dl", str(tmp_path / "out"), max_size=len(self.BODY))[0] == artifact

    def test_too_large(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        with pytest.raises(FetchError, match="larger than the 1000 bytes expected"):
            _ = fetcher.download(f"{server.url}a.tar.gz", str(tmp_path / "out"), max_size=1000)

    def test_magic(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        result = fetcher.download(
            f"{server.url}a.tar.gz", str(tmp_path / "out"), max_size=len(self.BODY), magic=self.BODY[:2]
        )

        assert result == (artifact, len(self.BODY))

    def test_wrong_magic_stops_early(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        # Something else, such as an error page served with a 200, is given up on at its first chunk, not downloaded.
        server.compress = False
        server.responses["/big"] = [(200, {}, b"<html>" + b"x" * (8 << 20))]

        with pytest.raises(FetchError, match="big: not a gzip file"):
            _ = fetcher.download(
                f"{server.url}big", str(tmp_path / "out"), max_size=16 << 20, magic=b"\x1f\x8b", kind="gzip file"
            )

        assert (tmp_path / "out").stat().st_size == 0

    def test_shorter_than_magic(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        server.responses["/short"] = [(200, {}, b"\x1f")]

        with pytest.raises(FetchError, match="short: not a gzip file"):
            _ = fetcher.download(
                f"{server.url}short", str(tmp_path / "out"), max_size=10, magic=b"\x1f\x8b", kind="gzip file"
            )

    def test_not_found(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        with pytest.raises(FetchError, match=r"not found \(HTTP 404\)"):
            _ = fetcher.download(f"{server.url}nope.tar.gz", str(tmp_path / "out"), max_size=1)

    def test_error_status(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        server.responses["/a.tar.gz"] = [(403, {}, b"no")]

        with pytest.raises(FetchError, match="HTTP 403 Forbidden"):
            _ = fetcher.download(f"{server.url}a.tar.gz", str(tmp_path / "out"), max_size=1)

    def test_connection_reused(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        # Each download releases its connection, so a pool of one isn't exhausted.
        with Fetcher(user_agent="t", max_requests=1) as one:
            for i in range(3):
                _ = one.download(f"{server.url}a.tar.gz", str(tmp_path / f"out{i}"), max_size=len(self.BODY))

    def test_file_url(self, site: Path, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        url = (site / "a.tar.gz").as_uri()

        assert fetcher.download(url, str(tmp_path / "out"), max_size=len(self.BODY)) == (artifact, len(self.BODY))

    def test_offline(self, tmp_path: Path) -> None:
        with Fetcher(user_agent="t", offline=True) as offline, pytest.raises(FetchError, match="offline"):
            _ = offline.download("https://dl.example/a.tar.gz", str(tmp_path / "out"), max_size=1)

    def test_existing_file_not_overwritten(self, site: Path, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        _ = (tmp_path / "out").write_bytes(b"mine")

        with pytest.raises(FetchError, match="can't write"):
            _ = fetcher.download((site / "a.tar.gz").as_uri(), str(tmp_path / "out"), max_size=len(self.BODY))
        assert (tmp_path / "out").read_bytes() == b"mine"


class TestErrors:
    """Network failures, each reported as its cause rather than urllib3's wrapping."""

    def test_unknown_host(self, fetcher: Fetcher) -> None:
        with pytest.raises(FetchError, match=r"index\.invalid/a\.json: can't resolve the host name: "):
            _ = fetcher.get("https://index.invalid/a.json", None)

    def test_tls_error(self, server: IndexServer, fetcher: Fetcher) -> None:
        url = server.url.replace("http://", "https://")

        with pytest.raises(FetchError, match=r"a\.json: TLS error: "):
            _ = fetcher.get(f"{url}a.json", None)

    def test_timeout(self, server: IndexServer, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_fetch, "_READ_TIMEOUT", 0.05)
        monkeypatch.setattr(_fetch, "_RETRIES", 0)
        server.delay = 0.5

        with Fetcher(user_agent="t") as f, pytest.raises(FetchError, match=r"a\.json: timed out$"):
            _ = f.get(f"{server.url}a.json", None)

    def test_dead_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")

        with (
            Fetcher(user_agent="t") as f,
            pytest.raises(FetchError, match=rf"can't connect: .* \(through the proxy http://127\.0\.0\.1:{port}\)$"),
        ):
            _ = f.get("https://index.example/a.json", None)


class TestCache:
    def test_offline_reads_cache(self, server: IndexServer, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        with Fetcher(user_agent="t") as online:
            _ = online.get(f"{server.url}a.json", path)

        with Fetcher(user_agent="t", offline=True) as offline:
            assert offline.get(f"{server.url}a.json", path) == b'{"a": 1}'
        assert len(server.requests) == 1

    def test_refresh(self, server: IndexServer, fetcher: Fetcher, site: Path, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        _ = fetcher.get(f"{server.url}a.json", path)
        (site / "a.json").write_text('{"a": 2}')

        assert fetcher.refresh(f"{server.url}a.json", path) == b'{"a": 2}'
        assert server.requests[1].target.startswith("/a.json?r=")
        assert json.loads(Path(path).read_bytes().partition(b"\n")[0])["url"] == f"{server.url}a.json"

    def test_refresh_keeps_query(self, server: IndexServer, fetcher: Fetcher) -> None:
        _ = fetcher.refresh(f"{server.url}a.json?x=1", None)

        assert server.requests[0].target.startswith("/a.json?x=1&r=")

    def test_refresh_offline(self, server: IndexServer, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        with Fetcher(user_agent="t") as online:
            _ = online.get(f"{server.url}a.json", path)

        with Fetcher(user_agent="t", offline=True) as offline:
            assert offline.refresh(f"{server.url}a.json", path) == b'{"a": 1}'
        assert len(server.requests) == 1

    def test_cached(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        assert fetcher.cached(path) is None
        assert fetcher.cached(None) is None

        _ = fetcher.get(f"{server.url}a.json", path)

        assert fetcher.cached(path) == b'{"a": 1}'

    def test_offline_miss(self, tmp_path: Path) -> None:
        with Fetcher(user_agent="t", offline=True) as f, pytest.raises(FetchError, match="isn't in the cache"):
            _ = f.get("https://index.example/a.json", cache_file(tmp_path))

    @pytest.mark.parametrize("content", [b"", b"garbage", b'{"v": 2}\nbody', b"[1]\nbody", b'{"v": 1}'])
    def test_unreadable_cache_is_a_miss(
        self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, content: bytes
    ) -> None:
        path = Path(cache_file(tmp_path))
        path.parent.mkdir(parents=True)
        path.write_bytes(content)

        assert fetcher.get(f"{server.url}a.json", str(path)) == b'{"a": 1}'
        assert "if-none-match" not in server.requests[0].headers

    def test_without_etag(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        path = cache_file(tmp_path)
        server.responses["/a.json"] = [(200, {}, b"no etag")]

        assert fetcher.get(f"{server.url}a.json", path) == b"no etag"
        assert json.loads(Path(path).read_bytes().partition(b"\n")[0])["etag"] is None

    def test_unwritable_cache(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        blocker = tmp_path / "cache"
        blocker.write_text("a file where the cache directory should be")

        with pytest.raises(FetchError, match="can't write to the cache"):
            _ = fetcher.get(f"{server.url}a.json", cache_file(tmp_path))


class TestFile:
    def test_read(self, site: Path, fetcher: Fetcher) -> None:
        assert fetcher.get((site / "a.json").as_uri(), None) == b'{"a": 1}'

    def test_missing(self, site: Path, fetcher: Fetcher) -> None:
        assert fetcher.get((site / "nope" / "b.json").as_uri(), None) is None
        assert fetcher.get(f"{(site / 'a.json').as_uri()}/under-a-file", None) is None

    def test_localhost(self, site: Path, fetcher: Fetcher) -> None:
        assert fetcher.get(f"file://localhost{site / 'a.json'}", None) == b'{"a": 1}'

    def test_other_host(self, fetcher: Fetcher) -> None:
        with pytest.raises(FetchError, match="can't name another host"):
            _ = fetcher.get("file://server/share/a.json", None)

    def test_unreadable(self, site: Path, fetcher: Fetcher) -> None:
        with pytest.raises(FetchError, match=re.escape(os.strerror(errno.EISDIR))):
            _ = fetcher.get(site.as_uri(), None)

    def test_offline_reads_files(self, site: Path) -> None:
        with Fetcher(user_agent="t", offline=True) as f:
            assert f.get((site / "a.json").as_uri(), None) == b'{"a": 1}'


class TestProxy:
    @pytest.mark.parametrize(
        ("env", "url", "expected"),
        [
            ({}, "https://index.example/", None),
            ({"HTTPS_PROXY": "http://proxy:3128"}, "https://index.example/", "http://proxy:3128"),
            ({"https_proxy": "proxy:3128"}, "https://index.example/", "http://proxy:3128"),
            ({"HTTPS_PROXY": "http://proxy:3128"}, "http://index.example/", None),
            ({"HTTP_PROXY": "http://proxy:3128"}, "http://index.example/", "http://proxy:3128"),
            ({"ALL_PROXY": "http://proxy:3128"}, "https://index.example/", "http://proxy:3128"),
            ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "example"}, "https://index.example/", None),
            ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "other.example"}, "https://index.example/", "http://p:1"),
            ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "*"}, "https://index.example/", None),
            # An IP range, which only reqwest's matching understood, so an error once named a proxy it never used.
            ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "10.0.0.0/8"}, "https://10.1.2.3/", None),
            ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "10.0.0.0/8"}, "https://11.1.2.3/", "http://p:1"),
            # Never the credentials, which an error would otherwise show.
            ({"HTTPS_PROXY": "http://user:secret@proxy:3128"}, "https://index.example/", "http://proxy:3128"),
            ({"ALL_PROXY": "socks5h://proxy:1080"}, "https://index.example/", "socks5h://proxy:1080"),
        ],
    )
    def test_proxy(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], url: str, expected: str | None) -> None:
        # The proxy reqwest uses, by the matcher it uses, read when the client is made.
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        client = _native.HttpClient("t", connect_timeout=1, read_timeout=1, retries=0, backoff=0, max_idle=1)

        assert client.proxy(url) == expected

    def test_bypassed_proxy_not_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
        monkeypatch.setenv("NO_PROXY", "127.0.0.0/8")

        with Fetcher(user_agent="t") as f, pytest.raises(FetchError) as raised:
            _ = f.get(f"http://127.0.0.1:{port}/a.json", None)

        assert str(raised.value) == f"http://127.0.0.1:{port}/a.json: can't connect: Connection refused"

    def test_through_proxy(self, server: IndexServer, monkeypatch: pytest.MonkeyPatch) -> None:
        # The client reads the proxy variables when it's made, not for each request.
        monkeypatch.setenv("HTTP_PROXY", server.url)

        with Fetcher(user_agent="t") as fetcher:
            assert fetcher.get("http://index.invalid/a.json", None) == b'{"a": 1}'
        assert server.requests[0].target == "http://index.invalid/a.json"


class TestTls:
    def test_unknown_ca(self, tls_server: IndexServer, fetcher: Fetcher) -> None:
        with pytest.raises(FetchError, match=r"a\.json: TLS error: invalid peer certificate: UnknownIssuer"):
            _ = fetcher.get(f"{tls_server.url}a.json", None)

    def test_cert_file(self, tls_server: IndexServer, ca_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))

        with Fetcher(user_agent="t") as f:
            assert f.get(f"{tls_server.url}a.json", None) == b'{"a": 1}'

    def test_cert_dir(
        self, tls_server: IndexServer, ca_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        certs = tmp_path / "certs"
        certs.mkdir()
        _ = shutil.copy(ca_file, certs / "ca.pem")
        monkeypatch.setenv("SSL_CERT_DIR", str(certs))

        with Fetcher(user_agent="t") as f:
            assert f.get(f"{tls_server.url}a.json", None) == b'{"a": 1}'

    def test_bad_cert_file_trusts_nothing(
        self,
        tls_server: IndexServer,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(_output, "verbosity", 0)
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))

        with Fetcher(user_agent="t") as f, pytest.raises(FetchError, match="TLS error"):
            _ = f.get(f"{tls_server.url}a.json", None)
        assert "no certificates found in SSL_CERT_FILE or SSL_CERT_DIR" in capsys.readouterr().err

    def test_keylog_warning(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        monkeypatch.setattr(_output, "verbosity", 0)
        with Fetcher(user_agent="t"):
            pass
        assert capsys.readouterr().err == ""
        keys = tmp_path / "keys.log"
        monkeypatch.setenv("SSLKEYLOGFILE", str(keys))

        with Fetcher(user_agent="t"):
            pass

        assert capsys.readouterr().err == (
            f"warning: SSLKEYLOGFILE is set: TLS session keys are being written to {keys}\n"
        )
        # Created as rustls creates it: only its owner can read the keys.
        assert keys.stat().st_mode & 0o777 == 0o600

    def test_keylog_unopenable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        monkeypatch.setattr(_output, "verbosity", 0)
        keys = tmp_path / "missing" / "keys.log"
        monkeypatch.setenv("SSLKEYLOGFILE", str(keys))

        with Fetcher(user_agent="t"):
            pass

        assert capsys.readouterr().err == (
            f"warning: SSLKEYLOGFILE is set, but {keys} can't be opened, so no TLS session keys are written: "
            + "No such file or directory\n"
        )


class TestConnections:
    """How requests to a host share its connections: the first HTTPS request alone, and connecting early."""

    def test_preconnect(self, tls_server: IndexServer, ca_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))

        with Fetcher(user_agent="t") as f:
            f.preconnect(tls_server.url)

            assert tls_server.wait_for("/")
        assert [r.method for r in tls_server.requests] == ["HEAD"]

    def test_preconnect_only_https(self, server: IndexServer, fetcher: Fetcher) -> None:
        fetcher.preconnect(server.url)

        assert not server.wait_for("/", timeout=0.3)

    def test_first_request_waits_at_most_a_second(
        self, tls_server: IndexServer, ca_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The second request waits for the first's response to share its connection, but not past 1 s, so a host
        # slow to answer doesn't hold everything else up.
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
        tls_server.delays["/slow"] = 3.0

        with Fetcher(user_agent="t") as f:
            slow = f.fetch(f"{tls_server.url}slow", None)
            assert tls_server.wait_for("/slow")
            started = time.monotonic()
            assert f.get(f"{tls_server.url}a.json", None) == b'{"a": 1}'
            waited = time.monotonic() - started
            assert slow.result() is None

        assert 0.8 <= waited < 2.5
