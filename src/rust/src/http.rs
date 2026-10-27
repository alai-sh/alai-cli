// Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
// MIT License (see LICENSE or https://opensource.org/licenses/MIT)

//! HTTP for `alai._fetch`: GETs with redirects and retries, and downloads streamed to a file, on reqwest. HTTP/2 is
//! negotiated by ALPN, falling back to HTTP/1.1; TLS is rustls, verified by the operating system
//! (rustls-platform-verifier) unless `SSL_CERT_FILE` or `SSL_CERT_DIR` names the only certificates to trust, as uv
//! reads them; proxies are reqwest's, from the environment. Each call blocks its Python thread with the GIL released,
//! so the fetcher's thread pool stays as it is, its requests sharing one client and so one HTTP/2 connection per host.

use std::collections::HashMap;
use std::error::Error as StdError;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use hyper_util::client::proxy::matcher::Matcher;
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use reqwest::header::{ETAG, HeaderMap, HeaderName, HeaderValue, LOCATION, RETRY_AFTER};
use reqwest::{Certificate, Client, RequestBuilder, Response, StatusCode, Url, redirect};
use sha2::{Digest, Sha256};

use crate::error::OsContext;

create_exception!(
    alai._native,
    HttpError,
    PyException,
    "A request failed: HttpError(kind, detail), kind one of dns, connect, tls, timeout, redirect, too_large, \
     not_magic or other."
);

const MAX_REDIRECTS: usize = 10;
/// The statuses followed to their `Location`. By hand rather than by reqwest's policy, so each hop is reported for
/// the trace, and so `https` to anything else can be refused.
const REDIRECTS: [u16; 5] = [301, 302, 303, 307, 308];
/// The most of a redirect's body read before following it; see `drain`.
const REDIRECT_BODY_LIMIT: usize = 64 * 1024;
/// The statuses retried, as urllib3's `status_forcelist` was.
const RETRY_STATUSES: [u16; 5] = [429, 500, 502, 503, 504];
/// How long a host's other requests wait for its first to connect, to share its connection if it's HTTP/2.
const FIRST_REQUEST_WAIT: Duration = Duration::from_secs(1);
/// The longest `Retry-After` honoured, so a host can't stall alai for long.
const MAX_RETRY_AFTER: Duration = Duration::from_secs(120);

/// The runtime every client's connections run on; calls block their own thread on it.
fn runtime() -> &'static tokio::runtime::Runtime {
    static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("alai-http")
            .enable_all()
            .build()
            .expect("can't start the HTTP runtime")
    })
}

/// A failed request as `HttpError(kind, detail)`.
fn http_error(kind: &str, detail: impl Into<String>) -> PyErr {
    HttpError::new_err((kind.to_owned(), detail.into()))
}

/// The kind of failure and its underlying reason, without reqwest's wrapping.
fn describe(err: &reqwest::Error) -> PyErr {
    let mut source: Option<&(dyn StdError + 'static)> = err.source();
    let mut innermost = err.to_string();
    while let Some(e) = source {
        if let Some(tls) = rustls_error(e) {
            return http_error("tls", tls.to_string());
        }
        innermost = e.to_string();
        source = e.source();
    }
    // io::Error's message ends in " (os error N)", which the reason reads better without, as in error.rs.
    if let Some((reason, _)) = innermost.split_once(" (os error ") {
        innermost = reason.to_owned();
    }
    if err.is_timeout() {
        http_error("timeout", "")
    } else if err.is_dns() {
        http_error("dns", innermost)
    } else if err.is_connect() {
        http_error("connect", innermost)
    } else {
        http_error("other", innermost)
    }
}

/// rustls's error, if `err` is one or wraps one: the connector nests it in `io::Error`s, whose `source()` skips the
/// error each wraps.
fn rustls_error<'a>(mut err: &'a (dyn StdError + 'static)) -> Option<&'a rustls::Error> {
    loop {
        if let Some(tls) = err.downcast_ref::<rustls::Error>() {
            return Some(tls);
        }
        err = err.downcast_ref::<std::io::Error>()?.get_ref()?;
    }
}

/// Whether a failed request is worth sending again: the connection, not the request, was at fault.
fn retryable(err: &reqwest::Error) -> bool {
    err.is_connect() || err.is_timeout() || err.is_request() || err.is_body()
}

/// How long to wait before attempt `attempt` (1 for the first retry): the response's `Retry-After` if it gives one
/// in seconds, else backoff doubling from `backoff`.
fn delay(response: Option<&Response>, attempt: u32, backoff: f64) -> Duration {
    let after = response
        .and_then(|r| r.headers().get(RETRY_AFTER))
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.trim().parse::<u64>().ok())
        .map(Duration::from_secs);
    after
        .unwrap_or_else(|| Duration::from_secs_f64(backoff * f64::from(1u32 << (attempt - 1))))
        .min(MAX_RETRY_AFTER)
}

/// The certificates `SSL_CERT_FILE` and `SSL_CERT_DIR` name, if either is set: then they're the only ones trusted,
/// even if none load. The errors are for the caller to warn about.
fn custom_certs() -> Option<(Vec<Certificate>, Vec<String>)> {
    let var = |name| std::env::var_os(name).filter(|v| !v.is_empty()).map(PathBuf::from);
    let (file, dir) = (var("SSL_CERT_FILE"), var("SSL_CERT_DIR"));
    if file.is_none() && dir.is_none() {
        return None;
    }
    let loaded = rustls_native_certs::load_certs_from_paths(file.as_deref(), dir.as_deref());
    let mut errors: Vec<String> = loaded.errors.iter().map(ToString::to_string).collect();
    let certs: Vec<Certificate> = loaded
        .certs
        .iter()
        .filter_map(|c| Certificate::from_der(c).ok())
        .collect();
    if certs.is_empty() {
        errors.push("no certificates found in SSL_CERT_FILE or SSL_CERT_DIR, so none are trusted".to_owned());
    }
    Some((certs, errors))
}

/// A redirect followed: its status, and the URL it led to.
type Redirect = (u16, String);

/// `get`'s result: status, reason, the redirects followed, ETag and body.
type GetResult<'py> = (u16, String, Vec<Redirect>, Option<String>, Bound<'py, PyBytes>);

/// Whether a host has been connected to, and the lock its first request holds until then.
type Gate = Arc<(AtomicBool, tokio::sync::Mutex<()>)>;

/// A reqwest client and its gates: one per process for each configuration, so every `HttpClient` made alike, such
/// as a resolve's and the downloads' after it, shares one connection pool.
struct Inner {
    client: Client,
    /// The proxies the client uses, read when it was made as reqwest reads them, for naming one in an error.
    proxies: Matcher,
    /// By host and port. Whether a host speaks HTTP/2 is only known once a connection to it is made, so until then
    /// every request would open its own: the first is sent alone, and the rest wait up to `FIRST_REQUEST_WAIT` for its
    /// response, then share its connection if they can. reqwest has no hook for "connected", so it's the response.
    gates: Mutex<HashMap<String, Gate>>,
}

/// The clients made so far, by configuration.
static SHARED: OnceLock<Mutex<HashMap<String, Arc<Inner>>>> = OnceLock::new();

#[pyclass(frozen, module = "alai._native")]
pub(crate) struct HttpClient {
    inner: Arc<Inner>,
    retries: u32,
    backoff: f64,
    /// Problems reading `SSL_CERT_FILE` or `SSL_CERT_DIR`, for the caller to warn about.
    #[pyo3(get)]
    cert_warnings: Vec<String>,
}

#[pymethods]
impl HttpClient {
    #[new]
    #[pyo3(signature = (user_agent, *, connect_timeout, read_timeout, retries, backoff, max_idle))]
    fn new(
        user_agent: &str,
        connect_timeout: f64,
        read_timeout: f64,
        retries: u32,
        backoff: f64,
        max_idle: usize,
    ) -> PyResult<Self> {
        // Everything the client is built from, the environment it reads included.
        let mut key = format!("{user_agent}\0{connect_timeout}\0{read_timeout}\0{max_idle}");
        for name in [
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "SSLKEYLOGFILE",
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        ] {
            key.push('\0');
            key.push_str(&std::env::var(name).unwrap_or_default());
        }
        let shared = SHARED.get_or_init(Mutex::default);
        if let Some(inner) = shared
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(&key)
        {
            let inner = Arc::clone(inner);
            return Ok(HttpClient {
                inner,
                retries,
                backoff,
                cert_warnings: Vec::new(),
            });
        }
        let mut builder = Client::builder()
            .user_agent(user_agent)
            .connect_timeout(Duration::from_secs_f64(connect_timeout))
            .read_timeout(Duration::from_secs_f64(read_timeout))
            .pool_max_idle_per_host(max_idle)
            // Followed by `Inner::get` instead.
            .redirect(redirect::Policy::none())
            .tls_sslkeylogfile(true);
        let mut cert_warnings = Vec::new();
        if let Some((certs, errors)) = custom_certs() {
            builder = builder.tls_certs_only(certs);
            cert_warnings = errors;
        }
        // The client's connector spawns onto the runtime, so it's built inside it.
        let _guard = runtime().enter();
        let client = builder.build().map_err(|e| {
            // reqwest's own message is "builder error"; the reason, such as no CA certificates, is innermost.
            let mut reason: &(dyn StdError + 'static) = &e;
            while let Some(inner) = reason.source() {
                reason = inner;
            }
            http_error("tls", reason.to_string())
        })?;
        let inner = Arc::new(Inner {
            client,
            proxies: Matcher::from_system(),
            gates: Mutex::default(),
        });
        _ = shared
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .insert(key, Arc::clone(&inner));
        Ok(HttpClient {
            inner,
            retries,
            backoff,
            cert_warnings,
        })
    }

    /// Connect to `url`'s host in the background, by a HEAD whose answer is ignored, so requests to it later find a
    /// connection ready, and over HTTP/2 share it.
    fn preconnect(&self, url: &str) {
        let inner = Arc::clone(&self.inner);
        let url = url.to_owned();
        _ = runtime().spawn(async move {
            _ = inner.send(&url, inner.client.head(&url)).await;
        });
    }

    /// The proxy a request to `url` goes through, as `scheme://host:port`, without any credentials it was given, or
    /// None to connect directly.
    fn proxy(&self, url: &str) -> Option<String> {
        let uri = url.parse::<http::Uri>().ok()?;
        let proxy = self.inner.proxies.intercept(&uri)?;
        let proxy = proxy.uri();
        Some(format!("{}://{}", proxy.scheme_str()?, proxy.authority()?))
    }

    /// GET `url` with `headers`, following redirects: `(status, reason, redirects followed, ETag, body)`. Connection
    /// failures and the retried statuses are retried with backoff; the last status is returned if they persist.
    fn get<'py>(&self, py: Python<'py>, url: &str, headers: Vec<(String, String)>) -> PyResult<GetResult<'py>> {
        let headers = header_map(headers)?;
        let (status, redirects, etag, body) = py.detach(|| {
            runtime().block_on(async {
                // A body that fails part way is asked for again, as urllib3 did, with the request's own retries.
                let mut attempt = 0;
                loop {
                    let (response, redirects) = self.inner.get(url, &headers, self.retries, self.backoff).await?;
                    let status = response.status();
                    let etag = etag(&response);
                    match response.bytes().await {
                        Ok(body) => return Ok((status, redirects, etag, body)),
                        Err(e) if attempt < self.retries && retryable(&e) => {
                            attempt += 1;
                            tokio::time::sleep(delay(None, attempt, self.backoff)).await;
                        }
                        Err(e) => return Err(describe(&e)),
                    }
                }
            })
        })?;
        Ok((
            status.as_u16(),
            reason(status),
            redirects,
            etag,
            PyBytes::new(py, &body),
        ))
    }

    /// Download `url` to the new file `path`: `(status, reason, redirects followed, SHA-256, size)`. Only a 200
    /// creates the file. It stops once more than `max_size` bytes arrive (`too_large`), or as soon as the body can't
    /// start with `magic` (`not_magic`). Retried as `get` is until the body starts; not after, as the file is half
    /// written.
    fn download(
        &self,
        py: Python<'_>,
        url: &str,
        path: PathBuf,
        max_size: u64,
        magic: &[u8],
    ) -> PyResult<(u16, String, Vec<Redirect>, String, u64)> {
        py.detach(|| {
            runtime().block_on(async {
                let headers = HeaderMap::new();
                let (mut response, redirects) = self.inner.get(url, &headers, self.retries, self.backoff).await?;
                let status = response.status();
                if status != StatusCode::OK {
                    return Ok((status.as_u16(), reason(status), redirects, String::new(), 0));
                }
                let mut out = OpenOptions::new().write(true).create_new(true).open(&path).at(&path)?;
                let mut digest = Sha256::new();
                let mut size: u64 = 0;
                let mut start: Vec<u8> = Vec::with_capacity(magic.len());
                while let Some(chunk) = response.chunk().await.map_err(|e| describe(&e))? {
                    size += chunk.len() as u64;
                    if size > max_size {
                        return Err(http_error("too_large", max_size.to_string()));
                    }
                    if start.len() < magic.len() {
                        let take = (magic.len() - start.len()).min(chunk.len());
                        start.extend_from_slice(&chunk[..take]);
                        if !magic.starts_with(&start) {
                            return Err(http_error("not_magic", ""));
                        }
                    }
                    digest.update(&chunk);
                    out.write_all(&chunk).at(&path)?;
                }
                if start.len() < magic.len() {
                    return Err(http_error("not_magic", ""));
                }
                let sha256 = digest.finalize().iter().map(|b| format!("{b:02x}")).collect();
                Ok((200, reason(status), redirects, sha256, size))
            })
        })
    }
}

impl Inner {
    /// GET `url` with `headers`, following up to `MAX_REDIRECTS` redirects but never from `https` to anything else,
    /// each hop retried as `get_once` retries: the response, and the redirects followed to reach it.
    async fn get(
        &self,
        url: &str,
        headers: &HeaderMap,
        retries: u32,
        backoff: f64,
    ) -> PyResult<(Response, Vec<Redirect>)> {
        let mut url = url.to_owned();
        let mut redirects = Vec::new();
        loop {
            let response = self.get_once(&url, headers, retries, backoff).await?;
            let status = response.status().as_u16();
            let location = if REDIRECTS.contains(&status) {
                response
                    .headers()
                    .get(LOCATION)
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_owned)
            } else {
                None
            };
            let Some(location) = location else {
                return Ok((response, redirects));
            };
            if redirects.len() >= MAX_REDIRECTS {
                return Err(http_error("redirect", format!("more than {MAX_REDIRECTS} redirects")));
            }
            let target = Url::parse(&url)
                .and_then(|from| Ok((from.join(&location)?, from)))
                .map_err(|e| http_error("redirect", format!("redirected to {location}, which isn't a URL: {e}")))?;
            let (target, from) = target;
            if from.scheme() == "https" && target.scheme() != "https" {
                let detail = format!("refusing to follow a redirect from https to {target}");
                return Err(http_error("redirect", detail));
            }
            drain(response).await;
            url = target.to_string();
            redirects.push((status, url.clone()));
        }
    }

    /// GET `url` with `headers`, without following a redirect. A connection failure, or one of `RETRY_STATUSES`,
    /// is retried up to `retries` times after `delay`; the last response is returned if the status persists.
    async fn get_once(&self, url: &str, headers: &HeaderMap, retries: u32, backoff: f64) -> PyResult<Response> {
        let mut attempt = 0;
        loop {
            match self.send(url, self.client.get(url).headers(headers.clone())).await {
                Ok(r) if attempt < retries && RETRY_STATUSES.contains(&r.status().as_u16()) => {
                    attempt += 1;
                    tokio::time::sleep(delay(Some(&r), attempt, backoff)).await;
                }
                Ok(r) => return Ok(r),
                Err(e) if attempt < retries && retryable(&e) => {
                    attempt += 1;
                    tokio::time::sleep(delay(None, attempt, backoff)).await;
                }
                Err(e) => return Err(describe(&e)),
            }
        }
    }

    /// Send `request` for `url`, alone if it's the first to its host.
    async fn send(&self, url: &str, request: RequestBuilder) -> reqwest::Result<Response> {
        let Some(gate) = self.gate(url) else {
            return request.send().await;
        };
        if gate.0.load(Ordering::Acquire) {
            return request.send().await;
        }
        // A slow first response mustn't hold the rest back for long: past this, they connect for themselves.
        let Ok(first) = tokio::time::timeout(FIRST_REQUEST_WAIT, gate.1.lock()).await else {
            return request.send().await;
        };
        if gate.0.load(Ordering::Acquire) {
            drop(first);
            return request.send().await;
        }
        let sent = request.send().await;
        // Connected, even if the response is an error status: a failure to connect lets the next one try.
        if sent.is_ok() {
            gate.0.store(true, Ordering::Release);
        }
        sent
    }

    /// The gate for an HTTPS URL's host; HTTP/2 is only negotiated over TLS, so plain HTTP has none.
    fn gate(&self, url: &str) -> Option<Gate> {
        let url = Url::parse(url).ok().filter(|u| u.scheme() == "https")?;
        let key = format!(
            "{}://{}:{}",
            url.scheme(),
            url.host_str()?,
            url.port_or_known_default()?
        );
        let mut gates = self.gates.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        Some(Arc::clone(gates.entry(key).or_default()))
    }
}

/// Read what's left of a redirect's body, up to `REDIRECT_BODY_LIMIT`, so its HTTP/1.1 connection can be used again
/// rather than closed, as urllib3 drained one; a longer body, or one that fails, is dropped with its connection.
async fn drain(mut response: Response) {
    let mut read = 0;
    while read <= REDIRECT_BODY_LIMIT {
        match response.chunk().await {
            Ok(Some(chunk)) => read += chunk.len(),
            _ => return,
        }
    }
}

fn etag(response: &Response) -> Option<String> {
    response
        .headers()
        .get(ETAG)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
}

fn reason(status: StatusCode) -> String {
    status.canonical_reason().unwrap_or("").to_owned()
}

fn header_map(headers: Vec<(String, String)>) -> PyResult<HeaderMap> {
    let mut map = HeaderMap::new();
    for (name, value) in headers {
        let name = HeaderName::from_bytes(name.as_bytes()).map_err(|e| http_error("other", e.to_string()))?;
        let value = HeaderValue::from_str(&value).map_err(|e| http_error("other", e.to_string()))?;
        _ = map.insert(name, value);
    }
    Ok(map)
}
