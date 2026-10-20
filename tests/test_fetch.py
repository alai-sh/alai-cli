# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import collections.abc as _c
import hashlib
import json
import socket
import sys
from pathlib import Path

import pytest
import truststore
import urllib3
from index_server import IndexServer, serve

from alai import _fetch, _output
from alai._fetch import Fetcher, FetchError, proxy_for, tls_context

_PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


@pytest.fixture(autouse=True)
def no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's proxy and TLS settings out, or requests to 127.0.0.1 would go through them."""
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_fetch, "_RETRY", _fetch._RETRY.new(backoff_factor=0))


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    root.mkdir()
    (root / "a.json").write_text('{"a": 1}')
    return root


@pytest.fixture
def server(site: Path) -> _c.Iterator[IndexServer]:
    yield from serve(site)


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

    def test_gives_up_after_retries(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(502, {}, b"")] * 4

        with pytest.raises(FetchError, match="HTTP 502"):
            _ = fetcher.get(f"{server.url}a.json", None)
        assert len(server.requests) == 4

    def test_follows_redirects(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/old.json"] = [(301, {"Location": "/moved/a.json"}, b"")]
        server.responses["/moved/a.json"] = [(307, {"Location": f"{server.url}a.json"}, b"")]

        assert fetcher.get(f"{server.url}old.json", None) == b'{"a": 1}'
        assert server.paths() == ["/old.json", "/moved/a.json", "/a.json"]

    def test_redirect_loop(self, server: IndexServer, fetcher: Fetcher) -> None:
        server.responses["/a.json"] = [(302, {"Location": "/a.json"}, b"")] * 20

        with pytest.raises(FetchError, match="more than 10 redirects"):
            _ = fetcher.get(f"{server.url}a.json", None)

    def test_refuses_https_downgrade(self, fetcher: Fetcher, monkeypatch: pytest.MonkeyPatch) -> None:
        class Manager:
            def request(self, *_: object, **__: object) -> urllib3.HTTPResponse:
                return urllib3.HTTPResponse(b"", status=302, headers={"Location": "http://index.example/config.json"})

        monkeypatch.setattr(fetcher, "_manager", lambda _url: Manager())

        with pytest.raises(FetchError, match=r"refusing to follow a redirect from https to http://index\.example/"):
            _ = fetcher.get("https://index.example/config.json", None)

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
        with pytest.raises(FetchError, match="larger than the 1000 bytes the index lists"):
            _ = fetcher.download(f"{server.url}a.tar.gz", str(tmp_path / "out"), max_size=1000)

    def test_not_found(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        with pytest.raises(FetchError, match=r"not found \(HTTP 404\)"):
            _ = fetcher.download(f"{server.url}nope.tar.gz", str(tmp_path / "out"), max_size=1)

    def test_error_status(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path) -> None:
        server.responses["/a.tar.gz"] = [(403, {}, b"no")]

        with pytest.raises(FetchError, match="HTTP 403 Forbidden"):
            _ = fetcher.download(f"{server.url}a.tar.gz", str(tmp_path / "out"), max_size=1)

    def test_connection_reused(self, server: IndexServer, fetcher: Fetcher, tmp_path: Path, artifact: str) -> None:
        # Each download releases its connection, so a pool of one isn't exhausted.
        with Fetcher(user_agent="t", max_connections=1) as one:
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

    def test_unknown_host(self, fetcher: Fetcher, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*_: object, **__: object) -> list[object]:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", fail)

        with pytest.raises(FetchError, match=r"index\.example/a\.json: can't resolve the host name: Name or service"):
            _ = fetcher.get("https://index.example/a.json", None)

    def test_tls_error(self, server: IndexServer, fetcher: Fetcher) -> None:
        url = server.url.replace("http://", "https://")

        with pytest.raises(FetchError, match=r"a\.json: TLS error: "):
            _ = fetcher.get(f"{url}a.json", None)

    def test_timeout(self, server: IndexServer, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_fetch, "_TIMEOUT", urllib3.Timeout(connect=5, read=0.05))
        monkeypatch.setattr(_fetch, "_RETRY", _fetch._RETRY.new(total=0))
        server.delay = 0.5

        with Fetcher(user_agent="t") as f, pytest.raises(FetchError, match=r"a\.json: timed out$"):
            _ = f.get(f"{server.url}a.json", None)

    def test_dead_proxy(self, fetcher: Fetcher, monkeypatch: pytest.MonkeyPatch) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")

        with pytest.raises(
            FetchError,
            match=rf"proxy error: can't connect: Connection refused \(through the proxy http://127\.0\.0\.1:{port}\)$",
        ):
            _ = fetcher.get("https://index.example/a.json", None)


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
        with pytest.raises(FetchError, match="Is a directory"):
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
        ],
    )
    def test_proxy_for(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], url: str, expected: str | None
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)

        assert proxy_for(url) == expected

    def test_through_proxy(self, server: IndexServer, fetcher: Fetcher, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTP_PROXY", server.url)

        assert fetcher.get("http://index.invalid/a.json", None) == b'{"a": 1}'
        assert server.requests[0].target == "http://index.invalid/a.json"


class TestTls:
    def test_system_trust_store_by_default(self) -> None:
        assert isinstance(tls_context(system_certs=True), truststore.SSLContext)

    def test_off(self) -> None:
        assert tls_context(system_certs=False) is None

    @pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
    def test_explicit_bundle_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> None:
        monkeypatch.setenv(name, str(tmp_path / "bundle.pem"))

        assert tls_context(system_certs=True) is None

    def test_fallback_without_truststore(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setitem(sys.modules, "truststore", None)  # importing it raises ImportError
        monkeypatch.setattr(_output, "verbosity", 1)

        assert tls_context(system_certs=True) is None
        assert "can't use the system trust store" in capsys.readouterr().err

    def test_keylog_applied(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # urllib3 only applies it to contexts it builds, so ours has to carry it, expanded the same way.
        monkeypatch.setenv("KEYS_DIR", str(tmp_path))
        monkeypatch.setenv("SSLKEYLOGFILE", "$KEYS_DIR/keys.log")

        context = tls_context(system_certs=True)

        assert context is not None
        assert context.keylog_filename == str(tmp_path / "keys.log")

    def test_pools_use_the_context(self, server: IndexServer, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", server.url)
        with Fetcher(user_agent="t") as f:
            proxy = f._manager("https://index.example/")

            assert isinstance(f._tls, truststore.SSLContext)
            assert f._pool.connection_pool_kw["ssl_context"] is f._tls
            assert proxy.connection_pool_kw["ssl_context"] is f._tls

    def test_keylog_warning(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(_output, "verbosity", 0)
        with Fetcher(user_agent="t"):
            pass
        assert capsys.readouterr().err == ""
        monkeypatch.setenv("SSLKEYLOGFILE", "/tmp/keys.log")  # noqa: S108  # never written: no connection is made

        with Fetcher(user_agent="t", system_certs=False):
            pass

        assert capsys.readouterr().err == (
            "warning: SSLKEYLOGFILE is set: TLS session keys are being written to /tmp/keys.log\n"
        )
