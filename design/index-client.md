# Index client

How alai reads an index: `src/alai/_fetch.py` fetches and caches files, over
the extension's HTTP client (`src/rust/src/http.rs`), and
`src/alai/_index.py` reads the index format (galaxy-sync's
`docs/index-spec/README.md`) on top of it. No command uses it yet; `alai lock`
will be the first. Measurements are from 2026-10-19 against `index.alai.sh`,
from a machine ~250 ms (TCP plus TLS) from Cloudflare.

## Decisions

- **Galaxy servers are never a source** (2026-10-21): not Galaxy itself,
  Automation Hub or a private Galaxy (Galaxy NG). Their API's caching and
  reliability aren't good enough to resolve and install from directly, which
  is why there's an index at all. Content from one could still be used
  through an index built from it, if a way to set that up is added; the
  client only ever reads indexes. The docs say so without "yet".
- **HTTP in the extension, with reqwest** (2026-10-27, replacing urllib3).
  `_native.HttpClient` makes the requests: reqwest 0.13 on hyper and Tokio,
  with HTTP/2 negotiated by ALPN and HTTP/1.1 the fallback, as uv does
  (`crates/uv-client`, read 2026-10-27). `_fetch` keeps the cache,
  revalidation and its pool of threads, each blocked in the extension with
  the GIL released, so callers still start fetches and wait on futures.
  Every `Fetcher` made with the same settings shares one reqwest client, and
  so its connections, keyed by everything the client is built from, the
  environment variables it reads included: a sync's downloads reuse its
  resolve's connection to the index. Why: over HTTP/2 a host's requests
  share one connection, which a cold `sivel.acd` lock made 1 of, against
  16, and it took 0.85 s against urllib3's 1.13 s; the measurements, and
  what it costs in wheel size and build time, are in
  [performance.md](performance.md#http-in-the-extension). The Python
  libraries gave the gain back (tried 2026-10-27, against `index.alai.sh`
  and `dl.alai.sh`, 12 to 50 ms away):
  - **urllib3's opt-in HTTP/2** (2.8, with h2) sends one request per
    connection, so it's HTTP/1.1 in other framing, and it deadlocked: 16
    threads connecting to `dl.alai.sh` at once all waited in
    `urllib3/http2/probe.py` for its probe of the host.
  - **httpx's HTTP/2** (0.28, async) multiplexes, and took 130 index files
    in 0.23 s against urllib3's 0.43 s, but its first request cost about
    0.3 s more than urllib3's, so it lost overall; its HTTP/1.1 was slower
    than urllib3's throughout.
  - **curl**, as a control (8.18, nghttp2, `--parallel`): HTTP/2 beat 16
    HTTP/1.1 connections by 20 to 35%, for index files and tarballs alike,
    so the gain is the protocol's, not the library's.

  The choice before it, measured 2026-10-19 from a machine ~250 ms (TCP plus
  TLS) from Cloudflare, where every option took about the same, fetching 60
  index files (median of 5 runs):

  | Library | Model | Median |
  |---|---|---|
  | stdlib `http.client` | 16 threads, HTTP/1.1 | 319 ms |
  | urllib3 2.8 | 16 threads, HTTP/1.1 | 300 to 500 ms |
  | httpx 0.28 | sync, HTTP/2, 8 threads | 513 ms |
  | httpx 0.28 | async, HTTP/2 | 589 ms |
  | aiohttp 3.14 | async | 480 ms |
  | niquests 3.21 | multiplexed HTTP/2 | 343 ms |

  urllib3 won on robustness then. niquests installs `urllib3-future`, which
  replaces the `urllib3` module for everything in the environment, a risk
  when alai shares one with ansible-core and requests, and httpx's
  maintenance has stalled; both still hold.
- **64 threads for the fetcher, blocked in the extension** (2026-10-27,
  `MAX_REQUESTS`, from 16, the HTTP/1.1 connection count it was swept as).
  Each holds no GIL while it waits, so more cost little, and over HTTP/2
  they share a host's connection; a host that only speaks HTTP/1.1 gets one
  each, still more requests in flight than 16, and most environments
  negotiate HTTP/2. Downloads keep their 16 (`MAX_CONNECTIONS`). A cold
  `sivel.acd` lock took 0.94 s against 1.06 s, and a cold sync with no lock
  2.44 s against 2.77 s; eight collections, at most 15 index files a wave,
  don't change. Two ways of doing more of this in the extension were built
  and measured that day, and dropped, as neither beat these 64 threads
  ([performance.md](performance.md#http-in-the-extension)):
  - **Fetches started in the extension**, a GET spawned on its runtime with
    a handle Python waits on, and a cap on requests in flight instead of
    threads: the lock took 0.91 s and the sync 2.59 s, so the gain was the
    requests in flight, which threads give as well.
  - **One call to download, check and extract every artifact**, with one
    `Fetcher` for a command's resolve and downloads: preparing took as long
    as with the pools (2.03 s against 1.97 s, the ranges overlapping), and
    saved only the downloads' second `config.json`.

  Together they added about 200 lines of Python and 400 of Rust, for
  nothing a user would notice; the patch was kept outside the repository.
- **Import cost.** The extension's HTTP adds nothing at import: the release
  `_native` imports in 0.78 ms against 0.69 ms without it (median of 15),
  and network commands no longer import urllib3 (~40 ms). Its Tokio runtime,
  two threads, starts with the first client.
- **No HTTP/3** (2026-10-27). Neither `index.alai.sh` nor `dl.alai.sh`
  offers it (no `alt-svc`, and `curl --http3-only` fails), and Cloudflare
  turns it on per zone. reqwest's is experimental (`--cfg
  reqwest_unstable`), isn't found from `alt-svc` and isn't raced against
  TCP, so a network that blocks UDP would fail rather than fall back, and
  alai would need its own fallback. QUIC would save one round trip per host
  per run; many corporate networks block outbound UDP, and HTTP proxies only
  tunnel TCP, so the networks most likely to be slow would fall back
  anyway. HTTP/2 is never forced (`http2_prior_knowledge`), which would
  drop the fallback for a host or proxy that only speaks HTTP/1.1.
- **The first HTTPS request to a host goes alone** (2026-10-27). Whether a
  host speaks HTTP/2 is only known once a connection's TLS handshake
  finishes, so 16 downloads started at once opened 16 connections. The
  first request to each `https` host is sent alone, and the others wait for
  its response, at most 1 s, then share its connection if they can: a cold
  `sivel.acd` sync went from 17 connections to 2. reqwest has no hook for
  "connected", so it's the response; the 1 s cap is for a host slow to
  answer, as `index.alai.sh` has been, where waiting longer would cost more
  than separate connections. A test of parallel tarball downloads from a
  server that waits 0.3 s before answering caught the uncapped first
  version. Plain `http` isn't gated, as it's never HTTP/2 here.
- **Commands that download connect early** (2026-10-27). `alai sync`, and
  `alai add` and `alai remove` when they sync, connect to each index's
  download host as soon as its `config.json` is read, while resolving, with
  a `HEAD` whose answer is ignored, through the same gate; `alai lock`
  doesn't, so it makes no connection it won't use. Without a lock, that
  took a cold `sivel.acd` sync from 3.46 s to 3.13 s, and with one, where
  there's no resolve to overlap, it changes nothing.
- **Compression: gzip, deflate and zstd**, reqwest's features, decompressed
  in the extension (zstd through the zstd crate's C library). Cloudflare
  compresses on the fly, picks zstd whenever it's offered, and caches each
  encoding separately:

  | File | Raw | gzip | br | zstd |
  |---|---|---|---|---|
  | `collections.json` | 331,613 B | 57,226 B | 57,684 B | 62,115 B |
  | `index/community/general.jsonl` | 64,219 B | 13,613 B | 12,016 B | 12,339 B |

  br and zstd are ~10% smaller than gzip on index files and zstd is larger on
  `collections.json`: a few kilobytes per lock. zstd is offered as uv offers
  it, and br isn't, as it would be another decoder for little.
- **The cache is keyed by index URL**, under
  `<cache_dir>/index/<host>-<sha256 of the root, 16 hex>/`, with the index's
  own paths below that (`config.json`, `collections.json`), as Cargo keys
  `registry/index/<host>-<hash>`. Renaming an index in `alai.toml` keeps its
  cache, and two names for one URL share it. `file://` indexes aren't
  cached. Index files are the exception: each is cached by collection, at
  `index/<namespace>/<name>.jsonl`, whatever URL the template gives
  (2026-10-25). Keyed by URL, an index that finds its files by SHA-256 would
  leave one copy behind for every change to a collection, and need a cache
  pruning step to clear them; keyed by collection, a new copy replaces the
  old one, and the cache holds one file per collection ever used, as it did
  before.
- **A cache file is one JSON line of metadata (`v`, `url`, `etag`), then the
  body** after decompression. It's written to a temporary file beside it and
  renamed into place, so a concurrent alai never reads half a file. An
  unreadable file, or one with another `v`, is a miss. The ETag is stored as
  received: Cloudflare weakens it (`W/"…"`) on compressed responses, and
  `If-None-Match` compares weakly, so it revalidates either way. It's only
  sent to the URL it came from (2026-10-25), since an index file's cached
  copy may be from the URL its old SHA-256 gave.
- **The cache is trusted, so who can write it matters** (2026-10-20). A
  cached `collections.json` whose `ETag` still matches is used after a 304,
  so anyone able to rewrite it, and the index files it hashes, chooses which
  `sha256` an artifact is checked against. Files are `0600` (`mkstemp`'s
  mode, kept by `os.replace`) and directories follow the umask, so the
  default `~/.cache/alai` is writable only by its owner. alai doesn't check a
  custom `cache_dir`'s ownership or mode, as neither pip nor uv does; the
  docs tell users that one outside their home directory is theirs to
  restrict. `0700` directories and a warning for a `cache_dir` writable by
  others are possible later. Extracted collections don't inherit `0600`:
  they're hardlinked into `collections/`, so they get ansible-galaxy's
  modes masked by the umask, or execution environments and other users
  couldn't read installed collections ([install.md](install.md)).
- **`config.json` and `collections.json` are revalidated every run**, as the
  index spec asks for every resolve, each URL at most once per run.
  `max-age` isn't used to skip revalidation. The listing is requested
  alongside `config.json`, from the path the cached config gives, so the two
  cost one round trip; if the config has changed, the guess is one unused
  request. With no config cached, a first run, the guess is
  `<root>/collections.json`, where the spec's layout and `index.alai.sh`
  keep it (2026-10-23): only `config.json`'s location is fixed, so an index
  that keeps it elsewhere, or a `moved-to` one, costs one unused request
  instead, and a `file://` index isn't guessed for, having no round trip to
  save. On `index.alai.sh` the two had been about 65 ms apiece, one after
  the other. Offline reads only the cache and fails on a miss; it'll be
  `--offline` and `ALAI_OFFLINE` once a command uses it.
- **Index files are checked against the listing's `sha256`** (the spec's
  "Cached index files"). A cached file whose SHA-256, computed over its own
  bytes, matches the listing is used with no request, so a run where nothing
  changed costs two requests however many collections it reads, and one
  where a few changed adds one round trip, for those files only. A file that
  doesn't match is revalidated as usual. If what comes back still doesn't
  match, an edge cache kept the old copy after the file changed (for up to
  its 60 s `max-age`) or the listing is the older of the two, so it's fetched
  once more as `?r=<16 random hex>`, which no cache holds, and the result is
  cached under the plain URL. Whatever arrives then is used: being stale for
  one run is the worst case, never stuck. Without `sha256` in the listing,
  every index file is revalidated, as before. Measured on `index.alai.sh`
  with 10 collections: a warm run went from ~0.9 s to ~0.45 s, Python start-up
  included. That's for an index whose template finds files by name; for one
  that finds them by SHA-256, see the next decision.
- **What was rejected for that, and why.** The index side of the scheme
  (the `sha256` field, the publishing order, purging and query strings) is
  in the index spec, in galaxy-sync's `docs/index-spec/README.md`: the
  `collections.json` section, "Cached index files", "Publishing order" and
  "HTTP behaviour". The alternatives, as tested against `index.alai.sh` on
  2026-10-19:
  - **Trusting `updated`.** galaxy-sync writes each changed index file
    during a run and sets its `updated` then, but writes `collections.json`
    at the end of the run, and Cloudflare caches the listing and each file
    separately for up to their 60 s `max-age`. A client can read the new
    listing, get the old file from the edge, record it as current for that
    `updated`, and never ask again until the collection next changes.
  - **Cache-busting with `?updated=<value>`.** Cloudflare keys its cache on
    the query string and R2 ignores it (tested: the first request is a
    `MISS`, the second a `HIT`, the body identical to the plain URL's). That
    fixes the race above, but a write to R2 doesn't invalidate Cloudflare's
    copy: anyone can request `?updated=<a future second>` now, for every
    second of a sync window if they like, and make the edge cache today's
    content under the key clients will use after the next change. Clients
    would then pin the old file.
  - **A 16-hex prefix of the hash.** Enough against outsiders, who'd need
    old content whose prefix matches the new one without choosing either.
    Not against collection authors: they control much of their index file's
    content (version strings, dependency requirements, how many versions
    there are) and can predict its next form, such as the same file with a
    version yanked. Finding a file whose 64-bit prefix equals that of its own
    future yanked form takes ~2^32 attempts, after which clients that cached
    it never see the yank. The listing's size, gzipped, measured on the live
    one: ~60 KB without a hash, ~117 KB with 16 hex, ~162 KB with 32, ~248 KB
    with the full 64.
  - **Asking the CDN to skip its cache.** Cloudflare ignores
    `Cache-Control: no-cache`, `max-age=0` and `Pragma: no-cache` from
    clients (tested: still a `HIT`, with `Age`). A new cache key, like the
    `?r=` retry's, is the only thing a client can do.
- **The index template** (2026-10-25). `formats.<n>.index` in
  `config.json` is a URL template for each collection's index file, like
  `dl`, rather than a directory: `index/{namespace}/{name}.jsonl` for an
  index whose files are found by name, or
  `index/{sha256-prefix}/{sha256-checksum}.jsonl` for one whose files are
  found by the SHA-256 `collections.json` lists, which is what `index.alai.sh`
  is to use. It has to name each file: `{namespace}` and `{name}`, or
  `{sha256-checksum}`; `{sha256-prefix}` alone doesn't. It's filled in, then
  resolved against the index root as RFC 3986 has it, so it can be relative
  or absolute, on another host, and a leading `/` is from the host's root.
  Format 1 was changed in place rather than adding a format 2, as no index
  but `index.alai.sh` exists and only alai reads it. SHA-256 only: the
  placeholders and the listing's field name the algorithm, so another would
  be a new field and placeholders in a new format, not a setting every
  client has to handle (artifacts are SHA-256 too, as Galaxy's are).
- **Paths in `config.json` can't lead anywhere** (2026-10-25). The index
  template, `collections` and `plugins` are refused if they lead from an
  `https` index to anything but `https`, or from an index that isn't on
  disk to a `file://` URL, so a remote index can't make alai read local
  files. An index on disk can point at a remote one. The same rule as
  redirects and `moved-to`.
- **Index files found by SHA-256 never change** (2026-10-25). A cached copy
  that matches the listing is used with no request, as for any index file.
  Otherwise the file at its URL is fetched with no `If-None-Match`, and if
  it doesn't match the SHA-256 in its own URL, that's an error: it can't be
  a stale copy, so there's no `?r=` retry. A collection the listing doesn't
  have isn't in the index, without a request, as only the listing says
  where its file is, and an entry with no `sha256` is an error for that
  collection. A 404 means the listing is older than the host keeps replaced
  files for, so `collections.json` is fetched again past any cache (`?r=`),
  at most once a run, and the file it names now is tried; still gone is an
  error. Offline, the cached copy is used as it is, as for files found by
  name. As the URL names only a hash, `-vv`'s trace lines for an index file
  name its collection, `GET <url> (community.general): 200, …`; the
  fetcher takes what a request is for (`what=`) for its trace.
- **An index can have no listing** (2026-10-27). `formats.<n>.collections`
  is optional in `config.json`, for two kinds of host:
  - **One that serves only index files.** A directory of
    `<namespace>/<name>.jsonl` files is then a whole index, with no
    listing to rebuild on every change, and a host that generates files on
    request needn't enumerate them.
  - **One that shows each user only what they can see**, which one shared
    listing can't do. Authentication itself isn't done yet (see
    [Not done yet](#not-done-yet)).

  Such an index:
  - **finds its files by name.** A template by SHA-256 needs the listing's
    `sha256`, so `config.json` with one and no listing is an error.
  - **has a collection if its index file is there.** A 404, 410 or 451
    means it doesn't, and the next index is asked. Anything else, 401 and
    403 included, is an error, as the index might have it. uv does the same
    with its first index: tried with uv 0.13.5 on 2026-10-27, against two
    local simple indexes, the first answering 401 or 403 for a package
    failed the lock without asking the second. So a host that hides a
    collection from a user has to answer 404 for it, as GitHub does for
    private repositories.
  - **is asked before any later index.** The resolver starts a
    collection's file from the first index that might have it, without
    waiting, and stops there. An index with a listing answers from it with
    no request, so it's skipped or chosen at once. One without a listing
    has to be asked, and the indexes after it wait for its answer. Asking
    them at the same time would save a round trip for a collection the
    first doesn't have, but would send a public index the names of private
    collections. uv asked its second index only after the first's 404.
  - **revalidates each index file every run it's read**, with
    `If-None-Match`, as an index file found by name without a `sha256`
    always was. A warm run costs one request per collection, not two in
    all, but each is a 304 with no body, and they run in parallel.
  - **doesn't remember a 404 past the run.** The spec allows caching one
    briefly. But a collection a user has just been given access to would
    then stay missing for that time, with nothing to tell them why. Within
    a run each file is asked for once. uv asked again on its next run too.
  - **has no "did you mean"** for a collection no index has, unless another
    index's listing suggests one.

  Only `config.json` says whether there's a listing. One it names that
  isn't found is an error (`IndexDataError`), never taken as no listing:
  that would hide a broken or half-published index, and an index by
  SHA-256 would have no way to find its files.

  On a first run nothing says the index has no listing, so `collections.json`
  is still guessed alongside `config.json`, and the 404 is one unused
  request. Once `config.json` is cached, it isn't guessed. A cached
  `collections.json` from before the index dropped it is never read.
- **Why `index.alai.sh` finds its files by SHA-256** (2026-10-25). A cold
  `alai add community.general` resolved in about 3 s on the first run after
  a pause and in 0.45 s straight after it. The index files were served with
  `Cache-Control: public, max-age=60`, which Cloudflare also took as its own
  lifetime, so more than a minute after anyone near a Cloudflare location
  last asked, each file was `REVALIDATED` with R2: 0.61 to 0.72 s, against
  0.26 to 0.29 s for a `HIT`. A resolve learns which files it needs from the
  ones before (`community.general`, then `ansible.netcommon`,
  `kubernetes.core`, `cloud.common`, `ansible.utils`), so five of those in a
  row made the 3 s. A file named by its SHA-256 never changes, so it can be
  cached for a year everywhere, as artifacts are, and the first request
  after a pause is a `HIT`. Only `config.json` and `collections.json` change,
  and a stale listing is old but consistent, as the files it names are kept
  for days, so galaxy-sync stopped purging Cloudflare altogether
  (2026-10-26): a change reaches clients up to 10 minutes after its sync,
  and no request waits for R2 after one. Considered, as header changes
  alone:
  - **`stale-while-revalidate` alone.** Cloudflare now serves the expired
    copy at once and revalidates in the background, so the wait goes, but
    R2 is still asked once a minute per location for every file in use, and
    the purge ordering still has to hold.
  - **`s-maxage`, a longer lifetime for shared caches only.** Fewer R2
    reads, but a missed purge (an expired token, a Cloudflare API error, a
    run killed between writing and purging, which nothing records) leaves
    files stale for the whole lifetime, so purges would need retrying from
    state; it also applies to company proxies, which no purge reaches.
  - **A Cloudflare Cache Rule** for the longer lifetime: Cloudflare only,
    but configured outside any repository, with the same purge problem.
  A template keeps the choice with each host: one that generates files on
  request, or rewrites another index's, keeps the name layout and works as
  before, and none of this changes what a client does beyond the template.
- **404, 410 and 451 mean "not found"** and remove the cached copy. 401 is
  "credentials required". Anything else is an error. Connection errors, 429,
  500, 502, 503 and 504 are retried three times with backoff (0.5 s, 1 s,
  2 s), or after `Retry-After` when it gives seconds, up to 120 s. The loop
  is the extension's own: reqwest 0.13.5's `retry` module only resends what
  failed at the protocol level, with no backoff and no `Retry-After`. A
  download is retried until its body starts, not after, as the file is half
  written by then.
- **Redirects are followed by the extension, by hand**, up to 10, with
  reqwest's own following turned off (`redirect::Policy::none()`,
  2026-10-27): each hop is returned to `_fetch` for a trace line, as the
  Python client gave, and a redirect from `https` to anything else is
  refused, as the spec requires. A custom reqwest policy did the refusing
  first, but it can't report the hops, since one client serves every
  request, and the final URL alone isn't enough: reqwest normalises it, so
  comparing it with the URL requested said "from" with no redirect for a
  URL written with an explicit port or an uppercase host. Each hop gets
  the retries, as urllib3 gave. The downgrade check compares the parsed
  URLs' schemes, not their text, so `HTTPS://` can't slip past it. A
  redirect's body, up to 64 KiB, is read before following it, as urllib3
  drained one, so its HTTP/1.1 connection can carry the next hop rather
  than being closed; a longer body is dropped with its connection. Its test
  uses a 48 KiB body, as hyper already reuses a connection whose body came
  in with the headers, so a short one passes with or without the reading.
  The next hop can still lose the drained connection to a race in
  hyper-util's pool (0.1.21, read 2026-10-27): finding none idle, it waits
  for one while connecting, and if the drained one is handed to it just as
  the new one connects, it's dropped with the wait. That's rare and costs
  one connection, so the test redirects to another server, whose hop never
  asks the pool for the first's connection.
  `moved-to` is followed the same way, with a warning to update the
  configured URL, and fails on a loop or more than 5 hops.
- **Proxies are reqwest's** (2026-10-27, taking the library's default as
  the user chose): `HTTPS_PROXY` and `HTTP_PROXY` by scheme, else
  `ALL_PROXY`, in either case, with `NO_PROXY` bypassing hosts, and on macOS
  the system settings for whatever those leave unset (hyper-util's
  `Matcher::from_system`, read in 0.1.21). That's a change from reading the
  environment only, which kept machines with the same environment alike; a
  proxy set only in macOS's settings now works, as it does for uv. They're
  read when the client is made, so a test that sets one makes its `Fetcher`
  after. reqwest's `NO_PROXY` matching also takes IP ranges, which the
  stdlib's didn't.
- **The OS verifies certificates, always** (2026-10-27, decided with the
  user; it replaced truststore and the `system_certs` setting, removed
  outright). rustls-platform-verifier hands the chain to the Security
  framework on macOS, as truststore did; on Linux and other Unixes it loads
  the system's CA certificates from where OpenSSL keeps them
  (`rustls-native-certs`, through `openssl-probe`) and checks the chain with
  rustls's own `webpki`. It's reqwest's default with rustls, and uv's
  `--system-certs`. No bundled roots, as uv's default has (Mozilla's, in
  `webpki-root-certs`): adding or distrusting a CA is then the OS vendor's
  update, never an alai release. `SSL_CERT_FILE` or `SSL_CERT_DIR`, when
  either is set and not empty, are the only certificates trusted, even if
  none load, with a warning naming what couldn't be read: uv's rule
  (`crates/uv-client/src/tls.rs`), and what OpenSSL does. The crypto is
  aws-lc-rs, reqwest's and uv's choice, rather than ring: it's maintained by
  AWS's cryptography team, has post-quantum key exchange and a FIPS build if
  ever wanted, and built unchanged in the manylinux2014 and musllinux 1.2
  aarch64 containers, with no cmake; ring is smaller and simpler to build,
  but kept by a small team since its author stepped back in 2025. What's
  given up:
  - **rustls is stricter than OpenSSL** about malformed certificates, such
    as some TLS-interception products issue: version 1 CA certificates, a
    host named only in the common name, bad encodings. On macOS the OS
    checks the chain, so this is Linux only. `SSL_CERT_FILE` changes which
    roots are trusted, not how strictly, so it's no way round; the fix is
    the certificate. Asked about in [user-questions.md](user-questions.md).
  - **A system with no CA certificates** fails, as it did with OpenSSL. The
    client can't be made then, which is only an error for a request that
    needs it, offline and `file://` runs being unaffected: `TLS error: No CA
    certificates were loaded from the system`, with a hint to install the
    `ca-certificates` package or set `SSL_CERT_FILE`. Checked in the
    musllinux image with `/etc/ssl` removed; that image sets
    `SSL_CERT_FILE=/opt/_internal/certs.pem` itself, as the manylinux ones
    do, which a check has to unset.
- **`SSLKEYLOGFILE` stays environment-only** (2026-10-20). The key
  log decrypts any captured session, credentials included, so it's never a
  config key or flag: a committed `alai.toml` could turn it on for everyone
  who clones the project, and a user file would be forgotten. That's also
  the convention (curl, browsers, Python's `ssl`, uv). reqwest's
  `tls_sslkeylogfile` has rustls write to it (2026-10-27), as it's given,
  without urllib3's expansion of variables in it. While it's set, every run
  warns that TLS session keys are being written and where, so a leftover
  export doesn't leak keys unnoticed.
- **Errors name the URL and the cause**, unwrapped from reqwest's errors:
  `can't resolve the host name: …`, `can't connect: …`, `TLS error: …`,
  `timed out`, and the proxy named after any of them, `(through the proxy
  …)`, without the " (os error N)" `io::Error` adds. The proxy comes from
  the extension (`HttpClient.proxy`), by hyper-util's
  `Matcher::from_system`, which is what reqwest uses, so it's always the
  one the request went through (2026-10-27). It replaced `proxy_for`, which
  read the variables in Python and could disagree: the stdlib's `NO_PROXY`
  matching has no IP ranges, so with `NO_PROXY=10.0.0.0/8` an error named
  a proxy reqwest never used for `10.1.2.3`, and it never saw macOS's
  settings. It's shown as `scheme://host:port`, without any credentials the
  variable holds, which `proxy_for` showed as written. rustls's error is
  two `io::Error`s deep, and `io::Error::source()`
  skips the error it wraps, so finding it takes `get_ref()` at each level;
  without that, a certificate error read as `can't connect`. The tests run
  an HTTPS server with a CA made by trustme for it, so an unknown CA,
  `SSL_CERT_FILE`, `SSL_CERT_DIR`, a missing bundle and the `https`
  downgrade are all tested against real TLS. They're skipped on uv's musl
  Python 3.10 only, where cryptography, which trustme needs, can't be
  imported: that Python loads only `…-linux-musl.so` extensions, and cffi's
  musllinux wheel for 3.10 names its own `…-linux-gnu.so`, as CPython
  before 3.13 did on musl (found in CI, 2026-10-27; 3.14t and 3.15 there
  are fine).
- **alai-core reads and writes the files** (`alai_core.index`, 2026-10-28).
  That's `config.json`, `collections.json` and each index file, with the
  versions, requirements and `requires_ansible` parsed in Rust as each line
  is read. This module adds the URLs, the scheme checks and alai's wording,
  such as "needs a newer alai", around it. The messages are the old
  reader's, apart from two inputs no index writes. A `v` of `1.0` is no
  longer taken as 1, and a vertical tab no longer makes a line blank. On the
  132 index files alai's cache had (6,375 lines), core reads them in 19.3 ms
  against 22.1 ms for the Python reader. The 708 KB `collections.json`
  takes 3.2 ms.
- **Index lines are judged one by one.** A line that can't be read at all
  (bad JSON, a missing or wrongly typed required field, a `name` that isn't
  the file's, a `vers` that isn't SemVer, a duplicate version) is skipped,
  and its reason kept in `IndexFile.invalid` for the caller to warn about. A
  line with a newer `v` is skipped and counted in `IndexFile.newer`, for
  "N versions need a newer alai". A readable line whose dependency
  requirement or `requires_ansible` can't be parsed is kept with
  `CollectionVersion.problem` set, so the resolver can skip it with a warning
  only when it would have considered it (see [versions.md](versions.md)).

- **The plugin index is fetched only by `alai find`** (2026-10-21), with
  `IndexClient.plugins()`, cached and revalidated like any index file.
  `IndexClient(prefetch="plugins")` starts the plugin index with
  `config.json` instead of `collections.json`, which `find` doesn't use,
  guessed the same way (`<root>/plugins.jsonl` on a first run), so its two
  requests cost one round trip (2026-10-23; they'd been one after the
  other). Searching it is in [find.md](find.md). `sync`'s downloads pass
  `prefetch=None`, as they read only `config.json`, for the artifact URLs
  (2026-10-23): with the default, each sync that downloaded also fetched a
  `collections.json` it never read, on a connection of its own.

## What `index.alai.sh` does today

- Served over HTTP/2 by Cloudflare from R2, with `ETag` and 304 on
  `If-None-Match`, as the spec asks. `config.json` offers format 1 with a
  plugin index and the template
  `index/{sha256-prefix}/{sha256-checksum}.jsonl` (since galaxy-sync's sync
  of 2026-10-25). Index files are served with
  `public, max-age=31536000, immutable`; `config.json`, `collections.json`,
  `plugins.jsonl` and the conflict records with
  `public, max-age=600, stale-while-revalidate=86400`, and nothing is
  purged, so a change reaches clients up to 10 minutes after the sync that
  made it. Checked on 2026-10-26: every file `collections.json` names
  exists and has the immutable header, `community.general`'s hashed to its
  listed `sha256` and was a `HIT` from its second request.
- **A cold resolve after a pause is fast now** (2026-10-26, a cold
  `alai -vv add community.general` from a home with nothing cached):
  resolving took 567 ms more than 10 minutes after the last request, where
  it had taken about 3 s, and about 0.5 s straight after another run. The
  first run against files never fetched through that Cloudflare location
  still took 2.3 s, as each was a `MISS`; being immutable, they stay cached
  there until Cloudflare evicts them.
- **`stale-while-revalidate` didn't take effect alongside `s-maxage`.**
  Past the 10-minute `s-maxage`, `config.json` and `collections.json` came
  back `REVALIDATED` with 0.64 to 0.68 s to the first byte, so Cloudflare
  waited for R2 rather than answering from its copy (`UPDATING`): it treats
  `s-maxage` as `proxy-revalidate`, which never serves an expired copy
  ([Cloudflare's Cache-Control directives](https://developers.cloudflare.com/cache/concepts/cache-control/)).
  As alai fetches the two at once, that was one round trip on the first
  resolve after a pause. galaxy-sync moved to the header above
  (2026-10-26), and the files already written were copied onto themselves
  to take it, as `config.json` is rarely written. It also stopped purging,
  which removed Cloudflare's copy after nearly every sync, so the next
  request waited for R2 anyway. Checked afterwards (2026-10-26): 701 s
  after Cloudflare cached them, `config.json` and `collections.json` came
  back `UPDATING` in 0.30 to 0.37 s to the first byte, from Cloudflare's
  copy, then `HIT` with `age: 0` once it had fetched them again behind it,
  and a cold `alai add community.general` resolved in 484 ms.
- The old index files by name, `index/<namespace>/<name>.jsonl`, are still
  served until galaxy-sync's pruning deletes them, 7 days after the
  migration; nothing reads them.
- **It returns 403 to urllib's default `User-Agent`** (`Python-urllib/3.x`),
  most likely Cloudflare's bot protection; httpx, urllib3, requests and curl
  defaults get 200. alai sends its own `User-Agent`, but scripts reading the
  index with `urllib` are blocked. Worth a note in the spec, or a rule on the
  zone.
- All 10 popular collections fetched in the smoke test (community.general,
  ansible.netcommon, amazon.aws and others) parsed with no invalid or
  unusable lines.
- **`collections.json` carries `sha256`** for every collection (4,610 on
  2026-10-20), and a sample of index files hashed to the listed values.

## Not done yet

- **Credentials** for private indexes, and for tarball URLs
  ([lockfile.md](lockfile.md#tarballs)). The spec leaves authentication to
  the host; a `401` already reports "credentials required". An index with
  no listing, as one that shows each user what they can see needs, is read
  already. The extension
  follows redirects itself, sending each hop the request's own headers,
  which today are only `If-None-Match`, as urllib3's loop did. reqwest's
  own following drops `Authorization` and cookies on a redirect to another
  host; with credentials, the loop must too, and not send them from
  `https` to anything else either.
- **Prefetching dependencies.** Index files that changed still cost one round
  trip per wave of dependencies. Prefetching every dependency of each file as
  it arrives, in slice 3, turns those into roughly one.
- **Progress output** while fetching, such as with Rich; see
  [output.md](output.md).
- **`Retry-After` as a date.** Only the seconds form is read; urllib3 read
  both.
- **`MAX_CONNECTIONS`** was swept over HTTP/1.1 (16, in
  [performance.md](performance.md#a-cold-sync-profiled-again)); over HTTP/2
  it's only the downloads' concurrency and the idle connections kept, so
  it's to measure again, or to go with the downloads' pool
  ([install.md](install.md)).
