# Index client

How alai reads an index: `src/alai/_fetch.py` fetches and caches files,
`src/alai/_index.py` reads the index format (galaxy-sync's
`docs/index-spec/README.md`) on top of it. No command uses it yet; `alai lock`
will be the first. Measurements are from 2026-10-19 against `index.alai.sh`,
from a machine ~250 ms (TCP plus TLS) from Cloudflare.

## Decisions

- **urllib3, on a pool of threads, with synchronous code.** Fetching 60 real
  index files in parallel took about the same with every option tried (median
  of 5 runs):

  | Library | Model | Median |
  |---|---|---|
  | stdlib `http.client` | 16 threads, HTTP/1.1 | 319 ms |
  | urllib3 2.8 | 16 threads, HTTP/1.1 | 300 to 500 ms |
  | httpx 0.28 | sync, HTTP/2, 8 threads | 513 ms |
  | httpx 0.28 | async, HTTP/2 | 589 ms |
  | aiohttp 3.14 | async | 480 ms |
  | niquests 3.21 | multiplexed HTTP/2 | 343 ms |

  Latency is round trips, so the choice came down to robustness. Over
  `http.client`, urllib3 gives a thread-safe pool, transparent retry when a
  kept-alive connection was closed by the server, retries with backoff and
  `Retry-After`, proxy tunnelling, and decompression, all of which we'd
  otherwise write. It's one pure-Python package with no dependencies, usually
  already installed beside requests. httpx's maintenance has stalled.
  niquests installs `urllib3-future`, which replaces the `urllib3` module for
  everything in the environment, a risk when alai shares one with
  ansible-core and requests. Async gained nothing and would make the resolver
  async too; threads let it stay plain code waiting on futures.
- **Import cost doesn't matter here.** urllib3 costs ~40 ms to import, and
  only network commands import `_fetch`, where downloads dominate.
- **No HTTP/3.** QUIC would save one round trip on the first connection of a
  run, but there's no stdlib support, many corporate networks block outbound
  UDP, and HTTP proxies only tunnel TCP, so the networks most likely to be
  slow would fall back anyway. HTTP/2 didn't beat pooled HTTP/1.1 either.
  urllib3's opt-in HTTP/2 can be tried later behind the same interface.
- **Compression: urllib3's default `Accept-Encoding`**, no optional packages.
  That's `gzip,deflate`, plus `zstd` on Python 3.14 and later, where urllib3
  uses the stdlib's `compression.zstd`. Cloudflare compresses on the fly,
  picks zstd whenever it's offered, and caches each encoding separately:

  | File | Raw | gzip | br | zstd |
  |---|---|---|---|---|
  | `collections.json` | 331,613 B | 57,226 B | 57,684 B | 62,115 B |
  | `index/community/general.jsonl` | 64,219 B | 13,613 B | 12,016 B | 12,339 B |

  br and zstd are ~10% smaller than gzip on index files and zstd is larger on
  `collections.json`: a few kilobytes per lock, not worth a C extension.
- **The cache is keyed by index URL**, under
  `<cache_dir>/index/<host>-<sha256 of the root, 16 hex>/`, with the index's
  own paths below that (`config.json`, `index/community/general.jsonl`), as
  Cargo keys `registry/index/<host>-<hash>`. Renaming an index in `alai.toml`
  keeps its cache, and two names for one URL share it. `file://` indexes
  aren't cached.
- **A cache file is one JSON line of metadata (`v`, `url`, `etag`), then the
  body** after decompression. It's written to a temporary file beside it and
  renamed into place, so a concurrent alai never reads half a file. An
  unreadable file, or one with another `v`, is a miss. The ETag is stored as
  received: Cloudflare weakens it (`W/"…"`) on compressed responses, and
  `If-None-Match` compares weakly, so it revalidates either way.
- **The cache is trusted, so who can write it matters** (2026-10-20). A
  cached `collections.json` whose `ETag` still matches is used after a 304,
  so anyone able to rewrite it, and the index files it hashes, chooses which
  `cksum` an artifact is checked against. Files are `0600` (`mkstemp`'s
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
  request. Offline reads only the cache and fails on a miss; it'll be
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
  included.
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
- **The publisher keeps `max-age=60` and purges what it changes** (decided
  with galaxy-sync on 2026-10-19). Purging Cloudflare after writing changed
  files, before writing the listing, shrinks the window in which the retry
  is needed from 60 s to the purge's propagation time. Raising `max-age`
  would also raise it for proxies and browsers after Cloudflare, which no
  purge reaches. A longer lifetime for Cloudflare alone, through a Cache
  Rule, would cut edge-to-R2 reads but needs purges that survive a failed
  run first; it's deferred until reads approach R2's free tier. None of this
  changes the client: the hash check and retry are correct with any
  lifetime.
- **404, 410 and 451 mean "not found"** and remove the cached copy. 401 is
  "credentials required". Anything else is an error. Connection errors, 429
  and 5xx are retried three times with backoff (0.5 s, 1 s, 2 s).
- **Redirects are followed by hand**, up to 10, so a redirect from `https` to
  anything else can be refused, as the spec requires. `moved-to` is followed
  the same way, with a warning to update the configured URL, and fails on a
  loop or more than 5 hops.
- **Proxies follow the usual environment variables**, as curl and requests
  read them: `HTTPS_PROXY` and `HTTP_PROXY` by scheme, else `ALL_PROXY`, in
  either case, with `NO_PROXY` bypassing hosts. Only the environment: the
  stdlib's `getproxies()` also reads macOS and Windows system settings,
  which would make behaviour differ between machines with the same
  environment. Custom CAs work through `SSL_CERT_FILE` and `SSL_CERT_DIR`,
  which OpenSSL reads.
- **The OS trust store, on by default** (2026-10-20).
  [truststore](https://github.com/sethmlarson/truststore) gives an
  `ssl.SSLContext` that verifies with the OS: the Security framework on
  macOS, CryptoAPI on Windows, OpenSSL's system paths on Linux. It's still
  the current answer (no stdlib equivalent), pure Python with no
  dependencies, ~9 ms to import, and pip has used it by default since 24.2.
  Tested with truststore 0.10.5 and urllib3 2.8:
  `urllib3.PoolManager(ssl_context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))`,
  and the same `ssl_context` (plus `proxy_ssl_context` for an HTTPS proxy)
  on `ProxyManager`. What it fixes: without it, urllib3 uses OpenSSL's
  default paths (not certifi), which on macOS depends on how Python was
  built and never sees roots pushed to the Keychain by MDM, and on Windows
  copies the root store into OpenSSL but skips Windows' own chain building
  and policy. That's the corporate TLS-interception case (Zscaler, a company
  root CA). On by default, as pip does, since the people it helps are the
  least able to diagnose a certificate error; off with the `system_certs`
  setting (`--system-certs`/`--no-system-certs`, `ALAI_SYSTEM_CERTS`, named
  after uv's `system-certs`). truststore is a dependency with only a floor,
  `>= 0.10`, the release tested: an upper bound would conflict in shared
  environments for a break that hasn't happened, and the part used is tiny.
  If building its context fails (an `ImportError` or `OSError`, such as an
  OpenSSL too old for it), alai falls back to OpenSSL's verification with a
  debug line rather than failing. An explicit
  `SSL_CERT_FILE` or `SSL_CERT_DIR` wins over it, since truststore bypasses
  OpenSSL's verification on macOS and Windows and would otherwise ignore
  them.
- **`SSLKEYLOGFILE` stays environment-only** (2026-10-20). The key
  log decrypts any captured session, credentials included, so it's never a
  config key or flag: a committed `alai.toml` could turn it on for everyone
  who clones the project, and a user file would be forgotten. That's also
  the convention (curl, browsers, Python's `ssl`, urllib3, uv). urllib3
  reads it only when it builds its own context, so with truststore's context
  `_fetch` must set `keylog_filename` from it itself (tested: without that,
  nothing is written), expanding variables as urllib3 does. While it's set,
  every run warns that TLS session keys are being written and where, so a
  leftover export doesn't leak keys unnoticed.
- **Errors name the URL and the cause**, unwrapped from urllib3's
  exceptions: `can't resolve the host name`, `can't connect: Connection
  refused`, `TLS error: …`, `timed out`, and `proxy error: …` with the proxy
  named.
- **Index lines are judged one by one.** A line that can't be read at all
  (bad JSON, a missing or wrongly typed required field, a `name` that isn't
  the file's, a `vers` that isn't SemVer, a duplicate version) is skipped,
  and its reason kept in `IndexFile.invalid` for the caller to warn about. A
  line with a newer `v` is skipped and counted in `IndexFile.newer`, for
  "N versions need a newer alai". A readable line whose dependency
  requirement or `requires_ansible` can't be parsed is kept with
  `CollectionVersion.problem` set, so the resolver can skip it with a warning
  only when it would have considered it (see [versions.md](versions.md)).

## What `index.alai.sh` does today

- Served over HTTP/2 by Cloudflare from R2, with `ETag`,
  `Cache-Control: public, max-age=60` and 304 on `If-None-Match`, as the spec
  asks. `config.json` offers format 1 with a plugin index.
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

- **Credentials** for private indexes. The spec leaves authentication to the
  host; a `401` already reports "credentials required".
- **Prefetching dependencies.** Index files that changed still cost one round
  trip per wave of dependencies. Prefetching every dependency of each file as
  it arrives, in slice 3, turns those into roughly one.
- **Progress output** while fetching, such as with Rich; see
  [output.md](output.md).
