# Performance

Where alai's time goes, what was done about it, and what's planned. The
measurements are on the development machine (aarch64, 6 cores, Linux 7.2,
XFS `/home`, a tmpfs `/tmp`), against `index.alai.sh` and `dl.alai.sh`,
on 2026-10-20 unless they say otherwise. Startup and import costs are in
[output.md](output.md) and [index-client.md](index-client.md).

## The benchmark: `sivel.acd`

`sivel.acd` is the Ansible community package as a collection: one
requirement that pins 96 others exactly, 97 collections, 45 MB of tarballs,
423 MB and 46,316 files once extracted, in 14,109 directories. It's the
worst ordinary case for every phase of `alai sync`:

| `alai sync`, cold (no cache, no lock) | Before | After the fixes below |
|---|---|---|
| Resolve | 4.9 s | 4.9 s |
| Prepare (download and extract) | 21.2 s | 6.6 s |
| Install (`auto`, cloning on XFS) | 0.5 s (hardlinks, tmpfs) | 3.6 s |
| Whole run | 26.8 s | 15.1 s |

Warm, it's under a second: 0.3 s to resolve with the cache and no lock,
4 ms with a lock, about 0.5 s to install. With nothing to change, a whole
`alai sync` run is 0.02 s, as it imports neither the fetcher nor urllib3.

## Against ansible-galaxy

`design/benchmarks/ansible_galaxy.py` runs this comparison, printing a table
to paste here; rerun it after a change that could affect it, and before the
landing page quotes new numbers:

```console
$ uv run python design/benchmarks/ansible_galaxy.py community.general
$ uv run python design/benchmarks/ansible_galaxy.py cisco.ios
```

The docs' landing page compares `alai sync` with ansible-core 2.21.5's
`ansible-galaxy collection install -r requirements.yml -p ./collections`
(2026-10-26, this machine, alai on Python 3.15, ansible-core on 3.14, after
`index.alai.sh` moved to index files by SHA-256). Each of five runs per
tool started with a new, empty `HOME`, so neither tool's cache carried
over: ansible-galaxy keeps everything under `~/.ansible` (its API response
cache in `galaxy_cache/api.json`), alai under `~/.cache/alai`. Per run: a
first install into a new project (ansible-galaxy with
`--clear-response-cache`), the same command again with nothing to change,
then a first install into a second project with the cache kept. alai ran
with a committed `alai.lock`, and again without one, which resolves as
ansible-galaxy always does. Medians, with each one's range:

| | `community.general` | `cisco.ios` (3 collections) |
|---|---|---|
| ansible-galaxy, first install | 14.60 s (13.85 to 16.92) | 20.70 s (20.09 to 21.56) |
| ansible-galaxy, again into another project | 9.52 s (8.98 to 11.19) | 13.40 s (12.86 to 14.71) |
| ansible-galaxy, nothing to change | 0.30 s (0.24 to 0.42) | 0.23 s (0.22 to 0.25) |
| alai without a lock, first install | 1.15 s (0.94 to 1.16) | 0.80 s (0.73 to 0.85) |
| alai with a lock, first install | 0.61 s (0.52 to 1.11) | 0.41 s (0.38 to 0.44) |
| alai without a lock, again into another project | 0.26 s (0.20 to 0.31) | 0.18 s (0.18 to 0.19) |
| alai with a lock, again into another project | 0.06 s (0.06 to 0.07) | 0.06 s (0.05 to 0.06) |
| alai, nothing to change | 0.04 s (0.04 to 0.04) | 0.04 s (0.04 to 0.05) |

The previous run (2026-10-21, ansible-core 2.20.0, the index by name with
`max-age=60`) had alai at 1.38 s and 0.88 s with a lock for
`community.general`, and `cisco.ios` resolving to 4 collections, against 3
now. Galaxy dominated ansible-galaxy's times and varied
widely then (15.7 to 78 s for a first install of `community.general`); it
was steadier this time, but they're still medians, and worth measuring
again before quoting new numbers. `--no-cache` wasn't run this time (43.6 s
for `community.general` on 2026-10-21).

## Extraction

Downloading all 97 tarballs takes 1.4 s; extracting them was the 21 s.

- **`tarfile.getmember` searches every member on each call**, so looking
  up each `FILES.json` entry was quadratic: 0.56 s of the 1.0 s
  community.general (4,343 entries) took. A dict built once replaced it,
  and members are now extracted in the order they're stored, as going back
  in a gzip stream means decompressing again from its start.
- **Threads made it slower.** It's CPU-bound Python, so under the GIL more
  extractions at once contend: 7.0 s on one thread, 7.7 s on two, 10.1 s
  on four, 12.6 s on eight and 13.3 s on sixteen. Extraction is now one at
  a time while downloads stay parallel.
- **Each parent directory is checked once** when its path has no symlink;
  the `realpath` check per entry had been 61k calls and 516k `lstat`s.
- **What's left**, profiled over all 97 on one thread: `tarfile`'s
  pure-Python header parsing (`_frombuf`, `_proc_pax`, `nts`, `nti`) about
  2.6 s of 7 s, zlib 1.0 s, and opening and closing 47k files 1.2 s. That's
  what [the native extension](#the-native-extension) replaces.

## Cloning

`auto` clones first, as uv does on Linux and macOS, and on XFS that's the
slowest way to install many small files. Installing the 97 collections:

| | Time |
|---|---|
| alai `install()`, clone | 2.15 s |
| alai `install()`, hardlink | 0.65 s |
| `cp -r --reflink=always` | 1.00 s |
| `cp -r -l` | 0.40 s |
| Walking the tree and making directories, in Python | 0.43 s |

Of the 2.15 s, opening source and destination (92,632 `open`s) is 0.60 s,
the `FICLONE` ioctl 0.42 s, `mkdir` 0.17 s and `chmod` 0.09 s; the rest,
about 0.7 s, is Python: path joins, `lstat`s, `os.walk`. `cp` makes the
same ioctl from native code, so native code would about halve it, but not
reach hardlinks: each clone is two opens and an extent update where a
hardlink is one directory entry. Cloning is kept first anyway, as uv keeps
it, because editing a hardlinked file edits the cache.

A cold `sync` took 3.6 s to install against 2.15 s for the same files
here, probably because freshly extracted files still had dirty pages for
XFS to write out before sharing their extents; not verified.

## A cold sync, profiled

`sivel.acd` from nothing (no cache, no environment), on the development
machine, 2026-10-22, with the extension built. That machine is a VM with
7 GB of RAM, about 1 GB of it free for the page cache, on a virtual disk
at 92% full: writes are throttled almost at once (creating 46k 9 KB files
from a Python loop takes 2.6 s), so disk costs here are larger than on a
typical workstation, and run to run they vary by a second or more. The
scripts are in the session's scratchpad, not the repository.

| Phase | With the extension | Pure Python |
|---|---|---|
| Resolve, no lock (network) | 1.1–5.3 s | 1.1–5.3 s |
| Prepare (download and extract) | 3.7 s | 7.0 s |
| Install (clone) | 4.2 s | 3.9 s |

- **Resolve is the network.** 132 requests, about 1.4 s cold of which
  the CPU is 0.33 s parsing 5,950 index lines (`_parse_line`, with
  `_semver` 0.16 s of it) and 0.2 s of TLS handshakes on 16 connections.
  Each connection also loads the CA store again
  (`set_default_verify_paths`, 16 calls, 55 ms), which one shared
  `SSLContext` would avoid. Index parsing is a candidate for the
  extension, worth a few tenths of a second cold and most of the 0.3 s
  warm resolve.
- **Index lines parse the same strings again and again.** Over
  sivel.acd's 130 index files, `_parse_line` took 72.5 ms, of which
  dependency requirements were 36 ms (14,523 parsed, 711 distinct) and
  `requires_ansible` 11.6 ms (5,273 parsed, 96 distinct). Each distinct
  text is now parsed once and the result shared, as it's never changed:
  26 ms for one pass from empty, and a warm offline `alai lock` went from
  167–178 ms to about 100 ms. What's left there is mostly checking each
  field (`_field`, 41,650 calls) and the resolver filtering versions.
- **Downloads wait on extraction.** Alone, all 97 download in 1.3–1.4 s
  (16 threads, to XFS or tmpfs). In a sync they end at 4–5 s: each of
  `get_all`'s 16 threads downloads then extracts, so a thread waiting for
  its turn to extract isn't downloading. Separate pools, downloads never
  waiting, end them at 1.2–1.4 s; then extraction is the limit, and on this
  disk that's writing the files.
- **Cloning a freshly written file waits for it to reach disk.** XFS
  flushes a file's dirty pages before sharing its blocks, so installing
  straight after extracting pays for writing everything extracted:

  | Install all 97, one at a time | Cache extracted long ago | Just extracted |
  |---|---|---|
  | `Linker.tree`, clone (today) | 2.61 s | 4.01 s |
  | `Linker.tree`, hardlink | 0.94 s | 0.64 s |
  | `Linker.tree`, copy (`copy_file_range`, which clones on XFS) | 2.41 s | |

  Tried and dropped: starting each file's writeback as it's extracted
  (`sync_file_range`) moved the cost rather than saving it (clone 2.2 s to
  1.3 s, extraction 2.3 s to 4.3 s); and installing a plain read and write
  copy, never cloning, was no faster (2.1–2.4 s), as the extra dirty pages
  hit the same throttling, and it doubles the disk used.
- **A tree linker in the extension** (prototype `alai_native.link_trees`,
  using `libc` for `FICLONE`): walk once, make directories and symlinks,
  then clone each file with `open`, `fstat`, `open(O_EXCL)`, `fchmod` and
  the ioctl. Threads per file contend, as they create in the same
  directories (clone 1.6 s on one thread, 4.2 s on six); threads per
  collection scale:

  | Install all 97 | 1 thread | 2 | 3 | 6 |
  |---|---|---|---|---|
  | clone, cache extracted long ago | 1.33 s | 0.77 s | 0.73 s | 1.08 s |
  | hardlink | 0.43 s | 0.23 s | 0.19 s | 0.29 s |
  | clone, just extracted | 3.4–4.3 s | | 2.1 s | 2.4–2.8 s |

  One large collection, just extracted, one thread, `Linker.tree` against
  `link_trees`: a10.acos_axapi (3,754 members, 60 MB) 0.85 s and 0.12 s,
  cisco.catalystcenter (5,888, 59 MB) 0.71 s and 0.14 s, oracle.oci 0.38 s
  and 0.05 s. Its trees are the same as `Linker.tree`'s. macOS's
  `clonefile` clones a whole directory in one call, which would replace
  the walk there.
- **Prepare and install together**, all 97 from nothing, the middle two of
  four runs each:

  | | Total |
  |---|---|
  | Today: `get_all`, then `Linker.tree` one at a time | 5.9–7.6 s |
  | `get_all`, then `link_trees`, a collection per thread (3) | 4.9–5.0 s |
  | Separate download, extract and link pools, each collection linked once extracted (6 extracting, 3 linking) | 5.7–5.8 s |
  | The same with 3 extracting and 2 linking | 4.4–4.9 s |

  On this disk fewer writers at once is faster; on one with more cache it
  likely isn't, so the counts want measuring elsewhere before choosing.
  The outliers by plugin count on the index, for testing large trees:
  chillancezen.fortimanager (3,624 plugins), a10.acos_axapi (3,168),
  community.general (3,019), cisco.catalystcenter (2,797), cisco.dnac
  (2,424) and oracle.oci (2,104).

What it suggests, in order of what it's worth here:

1. Install with the extension's tree linker, a collection per thread, with
   `Linker.tree` as the fallback: 2.6 s to 0.7 s warm, 4.0 s to 2.1 s
   cold, 5–7 times faster for one large collection. Done, below.
2. Download on its own pool, never waiting on extraction, in Python.
   Done, below.
3. Install each collection as it's extracted, once 1 and 2 are in.
4. Thread counts: one per CPU is too many on this disk; measure on
   others. Done: four at most, below.
5. One `SSLContext` for every connection, in Python; then index parsing
   in the extension. Parsing done, and the `SSLContext` left, on
   2026-10-23 ([below](#a-cold-sync-profiled-again)).
6. A parsed index cache, deferred until the tar work is done (decided
   with the user, 2026-10-22). Index files, and `collections.json`, kept
   parsed under the SHA-256 `collections.json` lists for each, so an entry
   never needs invalidating; written with `marshal` (plain values only,
   unlike `pickle`, which runs code on load) to a directory named for the
   layout and marshal's format version, as uv keeps its index responses
   parsed in versioned buckets. Measured over sivel.acd's 130 files: 1.3 MB,
   loaded and rebuilt in 12.9 ms against 38 ms to parse, about a quarter of
   a warm resolve (about 100 ms). Lazy parsing (about 16 ms at best, and
   bad lines found late) and cheaper field checks (about 5 ms) weren't
   worth it. Since the extension reads index lines (2026-10-23), the 94
   files of `scratch/acd` parse in 14.7 ms, so a cache would save less than
   it did.

### Done: the tree linker and separate download pool

- **`_native.link_tree(source, dest, mode)`** recreates one tree,
  `clone` (`FICLONE` on Linux, `clonefile` on macOS), `hardlink`, `copy`
  (`copy_file_range` through `io::copy`) or `symlink`, with the GIL
  released; an error names the cached file. `Linker.tree` calls it, keeping
  `auto`'s fallback (clone, hardlink, copy) in Python, under a lock, as
  threads share it, so several finding clone unsupported move on once; a
  partial tree is removed before the next mode tries. The Python walk and
  its per-file functions went when the extension became required. `libc`
  is a new crate, for the two calls std doesn't wrap.
- **Installs run on a pool** (`_sync._install_all`), a collection per
  thread, the first failure by name raised once all have finished, as
  downloads are. Namespaces are made, and any namespace link replaced,
  first, on one thread (`_install.namespace_dir`), so no two threads race
  for one. Up to four at once (see the thread counts below); the Python
  walk, which held the GIL, was slower on several threads than on one
  (6.9 s on six against 3.9 s), before it was removed.
- **Downloads have their own pool** (`get_all`): each hands its tarball
  to the extraction pool and takes the next, so none waits on extraction.
- **Switches**, for measuring elsewhere while the extension was optional,
  shown by `alai --version`: `_ALAI_IGNORE_NATIVE` (none of the extension),
  `_ALAI_NO_NATIVE_LINK` (Python linker), `_ALAI_SHARED_DOWNLOADS` (the old
  combined pool), `_ALAI_INSTALL_THREADS` and `_ALAI_EXTRACT_THREADS`.
  Removed with the fallback; the measurements below were made with them.

A cold `alai sync` of `sivel.acd` with its lock (no resolve), three runs
each, on the development machine:

| | Prepare | Install | Total |
|---|---|---|---|
| Before: shared pool, Python linker, one install at a time | 2.9–3.5 s | 3.5–4.2 s | 6.5–7.5 s |
| Defaults: 6 extracting, 6 installing | 2.9–3.1 s | 2.9–3.4 s | 6.1–6.4 s |
| 3 extracting, 3 installing | 2.5–2.8 s | 1.9–2.5 s | 4.6–5.2 s |
| Pure Python (`_ALAI_IGNORE_NATIVE`), one install at a time | 6.2–7.6 s | 4.1–5.1 s | |

So on this machine half the CPUs beats all of them, for both pools. The
default became four at most after a second machine was measured, below.
`benchmarks/cold_sync.py` times a cold sync under configurations of
environment variables, for measuring elsewhere.

### On a 16-thread machine with btrfs

Measured by the user on 2026-10-22 with `cold_sync.py`, five runs each,
medians; a machine with 16 hardware threads, btrfs, and tmpfs for
comparison (the rest of its specification wasn't recorded). `sivel.acd`,
cold unless it says warm:

| | Prepare | Install | Total |
|---|---|---|---|
| Before (shared pool, Python linker, one install at a time) | 4.12 s | 4.28 s | 8.49 s |
| Defaults (16 extracting, 16 installing) | 4.11 s | 1.98 s | 6.19 s |
| 2 extracting, 2 installing | 3.32 s | 1.76 s | 5.11 s |
| 3 extracting, 3 installing | 3.38 s | 1.49 s | 4.94 s |
| Pure Python | 8.95 s | 4.20 s | 13.32 s |

- **Fewer threads than CPUs win on a real disk here too.** Extraction,
  with 16 installing: 4.43 s on 1 thread, 3.26 s on 2, 3.55 s on 3,
  3.82 s on 8, 3.90 s on 16, 4.15 s on 32. Install, with 16 extracting:
  2.88 s on 1, 1.75 s on 2, 1.52 s on 3, 1.64 s on 8, 1.98 s on 16, 2.01 s
  on 32.
- **On tmpfs more threads win**, as nothing waits on a disk (hardlinks,
  as tmpfs can't clone): 16 extracting and installing 1.30 s and 0.09 s,
  8 1.28 s and 0.10 s, 3 1.60 s and 0.21 s. Prepare there is about the
  downloads alone. So one per CPU suits a fast store and two to four a
  real disk, on both machines measured.
- **The extension's linker** against the Python one, cold on btrfs, with
  16 installing and 1: clone 2.00 s and 4.31 s, hardlink 1.03 s and
  2.06 s, copy 1.96 s and 5.25 s. Warm (cache kept): clone 1.90 s with 16
  installing, 1.31 s with 3, hardlink 0.90 s, the Python linker 4.24 s.
- **The extension's extraction**: prepare 8.95 s in pure Python against
  4.11 s on btrfs, and 8.04 s against 1.30 s on tmpfs.
- **The four largest collections** (a10.acos_axapi, cisco.catalystcenter,
  oracle.oci, chillancezen.fortimanager): cold, 1.70 s against 2.07 s
  before and 3.15 s in pure Python; warm, installing 0.36 s against
  0.85 s with the Python linker.
- **Four threads**, measured the same way afterwards. The machine: an
  AMD Ryzen 7 3700X (8 cores, 16 threads), 125 GB of RAM (57 GB in use by
  other work, swap full), a Samsung 850 PRO SATA SSD with btrfs compressing
  with zstd, Linux 7.2, Python 3.14.8:

  | Medians | 4/4 | Best of the rest | 16/16 |
  |---|---|---|---|
  | Cold, clone | 5.15 s | 5.05 s (2/2) | 6.09 s |
  | Cold, hardlink | 4.51 s | 4.29 s (2/2) | |
  | Warm | 1.28 s | 1.38 s (3/3) | |
  | The largest collections, cold | 1.53 s | 1.60 s (2/2) | 1.72 s |
  | tmpfs, hardlink | 1.65 s | 1.96 s (3/3) | 1.45 s |

  Install alone was fastest at 4 (1.43 s, against 1.48 s at 3 and 1.77 s
  at 2), and extraction alone was flat from 2 to 4 (3.40 to 3.57 s).
  **Decided** (with the user, 2026-10-22): both pools default to four, or
  one per CPU if there are fewer (`_native.MAX_THREADS`), the best or
  within the runs' noise of it on a real disk everywhere measured, at a
  cost of 0.2 s on tmpfs. uv's default is one per CPU.
- **One cold run took 186 s** to prepare, against 1.1 s otherwise: three
  of the fetcher's 60 s read timeouts and their 0.5, 1 and 2 s backoff
  (183.5 s) on top of a normal run, so a download that stalled three times
  before it was served, most likely a rarely fetched artifact the
  download host had to fetch from its origin, rather than a hang. Three
  minutes with nothing said is long: uv's default `UV_HTTP_TIMEOUT` is
  30 s. The four-thread runs had another, one 60 s timeout (a hardlink
  run's prepare, 60.75 s), so the download host stalls now and then.
  **Changed** (with the user, 2026-10-22): the read timeout is now 30 s,
  as uv's, so a stall that would have cost 186 s costs about 96 s; the
  connect timeout stays 15 s.

## A cold sync, profiled again

`scratch/acd` from nothing, 2026-10-23: every collection in the ansible 15
package, nothing pinned, 92 collections and 43,938 files once extracted,
on the development machine (XFS `/home`, 94% full by then), sampled on
every thread with Python 3.15's `profiling.sampling` (wall, CPU and
GIL modes), with `cProfile`, `strace -c` and timers around each
download, extraction and install. The scripts are in the session's
scratchpad, not the repository.

| Phase | Time | Bound by |
|---|---|---|
| Resolve, no lock | 1.0–1.3 s, 9–13.6 s on bad runs | `index.alai.sh`: 96 requests, already prefetched in parallel; the slow runs were the host taking up to 8 s for one file |
| Resolve, warm | 75–89 ms | Parsing index lines; PubGrub is 4 ms |
| Prepare, with the lock | 2.0–3.0 s | Downloads: extraction, four at once, ends within 0.1 s of the last |
| Install (clone) | 2.4–4.4 s | XFS, not alai's code |
| Nothing to change | 0.04–0.05 s | |

- **Python is about 0.2 s of a cold sync**: in GIL mode, about 1,060
  samples at 5 kHz in an 11 s run, which is HTTP, JSON and imports. The
  rest of the CPU is the extension's extraction and linking, so moving
  more Python to Rust can save tenths of a second at most.
- **Cloning on XFS waits for what was just written.** A clone flushes its
  source's dirty pages first, so installing straight after extracting
  writes the cache out a file at a time. With `cp`, all 92: cloning them
  just extracted took 4.1–7.2 s, against one `sync` (0.43–0.62 s) and then
  cloning clean files (1.3–1.4 s). The linker itself is as fast as
  `cp --reflink=always` (0.34–0.42 s against 0.37 s for the six largest
  collections from a clean cache, with the same system calls).
- **Install threads, here**, linking all 92 from a clean cache with
  `link_tree`: clone 1.0–1.3 s on one thread, 0.86–0.90 s on two,
  1.9–2.3 s on three, 2.4 s on four and 3.4–3.6 s on six; hardlinks
  0.45 s on one and 0.62–0.86 s on four. The note above found three best
  on this machine, so it moves with the machine's state.
- **Tried and rejected: flushing before installing, and two threads.** On
  this machine, a cold sync with the lock, three runs each: 5.4–7.1 s as
  it is, 5.3–5.5 s flushing first, and 3.8–4.0 s flushing first with two
  threads; for the six largest collections 3.4–3.6 s, 2.7–2.8 s and
  1.9–2.2 s. On the user's 16-thread btrfs machine (`cold_sync.py`, five
  runs each, medians) both made it slower:

  | | Prepare | Install | Total |
  |---|---|---|---|
  | As it is (four threads) | 3.41 s | 1.18 s | 4.76 s |
  | Flushing first | 3.47 s | 1.51 s | 5.03 s |
  | Two threads | 3.36 s | 1.39 s | 4.86 s |
  | Flushing first, two threads | 3.30 s | 1.84 s | 5.31 s |

  btrfs evidently doesn't wait for each file's writeback when it clones,
  as XFS does, so a flush only brings into the run the writing the kernel
  would otherwise do after it. Kept as it is (decided with the user,
  2026-10-23): the gain is one VM's, short of memory and on a nearly full
  virtual disk, which isn't enough to justify filesystem-specific code.
- **Left: skipping each cloned or copied file's `fchmod`** when the umask
  doesn't mask its mode (43,938 calls, about a tenth of the install's
  system calls; decided with the user, 2026-10-23). `link_tree` sets each
  mode exactly, with no umask to pass, for simplicity since the review in
  [the native extension](#the-native-extension), and a default ACL on the
  collections directory would mask a new file's mode instead.
- **Gone: truststore loading the CA store for every connection.** Its
  Linux backend called `set_default_verify_paths` in each `wrap_socket`,
  under the context's lock: 3.3 ms each, about 52 ms of CPU for 16
  connections, mostly while waiting on the network. The extension's client
  loads the system's certificates once per client, and makes one connection
  per host over HTTP/2 ([below](#http-in-the-extension)).
- **Done: one round trip for `config.json` and what's read next.** On a
  first run the listing is now guessed at `<root>/collections.json` and
  requested with `config.json`, and `alai find` requests the plugin index
  with it rather than after it ([index-client.md](index-client.md#decisions)):
  about 65 ms each on `index.alai.sh`. Connections are pooled, up to 16 per
  host: a cold sync of `scratch/acd` made 98 requests to `index.alai.sh` on
  18 TLS connections and 92 downloads on 16. Two of the 18 were the
  downloads' own client, which needs only `config.json` but also started
  `collections.json`; it no longer does (97 on 17). Reusing the resolve's
  fetcher for the downloads would save the other, and a round trip, only
  when a run both resolves and downloads; not done then (decided with the
  user, 2026-10-23), and done since by the extension's client, which every
  fetcher with the same settings shares
  ([below](#http-in-the-extension)).
- **Connections per host, swept** (`MAX_CONNECTIONS`, 16 since the client
  was written, chosen while comparing libraries, not measured as a count).
  On this machine, three rounds, each count in turn:

  | Connections | Resolve, cold `alai lock` | Prepare, cold sync with the lock |
  |---|---|---|
  | 4 | 1.02–1.09 s (one 10.4 s) | 2.06–2.38 s |
  | 8 | 0.81–0.90 s | 2.14–2.41 s |
  | 16 | 0.92–0.94 s (one 2.81 s) | 2.35–2.59 s |
  | 32 | 1.34–2.90 s | 2.89–2.96 s |

  On the user's 16-thread btrfs machine, six runs of each (prepare's range
  and median; resolve's fastest, and how many runs the index host stalled
  past 2 s):

  | Connections | Resolve, fastest | Stalled | Prepare | Median |
  |---|---|---|---|---|
  | 4 | 0.76 s | 3 of 6 | 3.03–3.22 s | 3.11 s |
  | 8 | 0.69 s | 2 of 6 | 3.16–3.40 s | 3.29 s |
  | 16 | 0.66 s | 1 of 6 | 3.20–3.49 s | 3.38 s |
  | 32 | 0.95 s | 1 of 6 | 3.75–3.93 s | 3.86 s |

  On both, 32 is worse at everything and fewer download faster, likely as
  more downloads only wait on the four extracting and the disk; 16 is as
  good as any for the index, and on the user's machine the least hurt by a
  stalling host, perhaps as the other requests keep moving past a slow
  file. **Kept at 16** (decided with the user, 2026-10-23): a separate,
  smaller download pool, 8, would save 0.1 to 0.3 s of a 4.8 s cold sync,
  and fewer connections could cost a link with more latency than either
  machine's, which wants more in flight. That was over HTTP/1.1; over
  HTTP/2 it's only the downloads' concurrency, and is to measure again
  ([index-client.md](index-client.md#not-done-yet)).
- **Done: index lines and the plugin index read by the extension**, with
  `serde_json`, and `memchr` to search the plugin index. A warm resolve of
  `scratch/acd` went from 74–89 ms to 62–72 ms (its parsing from 22 ms to
  14.7 ms; the "60 ms" `cProfile` gave was mostly the profiler's), and
  `alai find` from 0.13 to 0.10 s for a narrow search and from 1.9 to
  0.6 s for the broadest. The details are in
  [index-client.md](index-client.md#decisions) and
  [find.md](find.md#reading-the-file).

## HTTP in the extension

`alai._native.HttpClient`, reqwest over rustls and aws-lc-rs, replaced
urllib3 and truststore on 2026-10-27, for HTTP/2; the decisions are in
[index-client.md](index-client.md#decisions). Measured on the development
machine, 12 to 50 ms from Cloudflare (`ping`), against `index.alai.sh` and
`dl.alai.sh`, with two projects: `sivel.acd` and eight collections
(`community.general`, `ansible.posix`, `community.docker`,
`kubernetes.core`, `amazon.aws`, `community.crypto`, `ansible.windows` and
`community.vmware`, 10 collections locked). Each run had an empty cache, in
a new directory on tmpfs, so these are the network, not the disk; the
configurations took turns in a shuffled order. The scripts were in the
session's scratchpad.

**Whether HTTP/2 helps at all**, before writing any of it: curl 8.18 with
nghttp2, `--parallel`, every URL at once, medians of 7 runs (5 for
`sivel.acd`'s 130 files, as its resolve fetches every dependency of every
version):

| URLs | 16 connections, HTTP/1.1 | HTTP/2, one connection |
|---|---|---|
| 15 index files | 0.37 s | 0.30 s |
| 15 tarballs, 9.8 MB | 0.85 s | 0.70 s |
| 130 index files | 0.69 s | 0.46 s |
| 130 tarballs, 69 MB | 3.25 s | 2.65 s |

The Python libraries didn't keep it (in
[index-client.md](index-client.md#decisions)): urllib3's HTTP/2 doesn't
multiplex and deadlocked, and httpx's first request cost about 0.3 s more
than urllib3's.

**The extension's client, against urllib3**, medians of 7 runs (5 for a
`sivel.acd` sync with its lock), whole runs:

| | urllib3 | reqwest, 16 threads | reqwest, 64 threads |
|---|---|---|---|
| Eight collections, cold `alai lock` | 0.65 s | 0.44 s | 0.49 s |
| `sivel.acd`, cold `alai lock` | 1.13 s | 0.85 s | 0.67 s |
| Eight collections, cold `alai sync` with the lock | 0.96 s | 0.84 s | 0.75 s |
| `sivel.acd`, cold `alai sync` with the lock | 2.54 s | 1.94 s | 1.97 s |

The threads are the fetcher's, for index files (`_ALAI_FETCH_THREADS`),
64 by default since (below); downloads keep their own 16. Connections to
the two hosts, counted with `ss` through a cold `sivel.acd` lock and sync:
16 and 17 with urllib3, 1 and 2 with the client. More than curl's numbers
predicted, as the client also drops urllib3's work in Python and
truststore's per connection.

**Sharing the client, and connecting early**, then went on top
(2026-10-27), medians of 7 runs (5 with the lock):

| Cold `alai sync` | urllib3 | Client | Shared, connecting early |
|---|---|---|---|
| Eight collections, no lock | 2.32 s | 1.67 s | 1.45 s |
| `sivel.acd`, no lock | 4.94 s | 3.46 s | 3.13 s |
| `sivel.acd`, with the lock | 2.86 s | 2.13 s | 2.16 s |

Without a lock the downloads find connections to both hosts ready, and the
worst runs went too (1.84 s and 3.73 s, against 4.39 s and 8.85 s); with
one, there's no resolve to overlap, so nothing changes.

**What it costs**:

| | Before | With the client |
|---|---|---|
| `_native` `.so`, release | 1.7 MB | 6.7 MB |
| abi3 wheel | 0.94 MB | 3.6 MB (3.56 MB manylinux2014, 3.65 MB musllinux, aarch64) |
| Clean release build, this machine | 19 s | 63 s |
| Build in the musllinux 1.2 aarch64 container | 33 s | 87 s |
| `import alai._native`, release | 0.69 ms | 0.78 ms |

aws-lc-rs built with no changes in CI's manylinux2014 (glibc 2.17) and
musllinux 1.2 aarch64 containers, with no cmake; the wheel built on this
machine was tagged `manylinux_2_38` only because of the host's glibc. Both
musl wheels bundle `libgcc_s`, as before. The x86_64 and macOS wheels built
and passed their tests in CI too (2026-10-27).

**Moving more of the work into the extension**, considered on 2026-10-27,
with Python about 0.2 s of a cold sync:

- **Done instead: 64 threads for the fetcher** (2026-10-27,
  [index-client.md](index-client.md#decisions)). Fetches started in the
  extension, and one call to download, check and extract every artifact,
  were both built first, and neither beat the threads they'd replace once
  there were 64 of them. Medians of 7 runs, interleaved, each with an empty
  cache, whole runs:

  | `sivel.acd` | 16 threads | 64 threads | Both built |
  |---|---|---|---|
  | Cold `alai lock` | 1.06 s | 0.94 s | 0.91 s |
  | Cold `alai sync` with no lock | 2.77 s | 2.44 s | 2.59 s |

  Preparing with its lock took as long either way (1.97 s with the pools,
  2.03 s with the one call, nine runs each, the ranges overlapping), and
  the runtime's worker threads, which hashed and wrote each tarball there,
  made no difference at four rather than two. Streaming each download into
  its extraction is still open, in [install.md](install.md#not-done-yet).
- **Linking every collection in one call** would be simpler, not faster,
  as it's the filesystem's time.
- **Kept in Python**: the index cache and its rules, and building the
  resolver's table (`_index.py` and `_resolve.py`, about 1,250 lines,
  mostly behaviour and error wording), for a warm resolve of 62–72 ms all
  told.

## The native extension

`src/rust/` is a Rust crate, built with pyo3 and maturin into the alai
package as the private module `alai._native` (`src/alai/_native.pyi`
types it). It's laid out as `cryptography` lays out its own: Python in
`src/alai/`, Rust in `src/rust/`, a Cargo workspace in the root
`Cargo.toml`, which holds the release profile, and `Cargo.lock` beside it
(decided with the user, 2026-10-22). maturin's default puts the Rust in a
root `src/` and the Python in `python/`, which would have moved every tool
path for alai's src layout; the Rust inside `src/alai/` would ship in the
wheel. It resolves versions, makes HTTP requests and places installed
files, with the GIL released. Extracting collection tarballs is
alai-core's, `alai_core.artifact`, since 2026-10-28 (`src/rust/src/extract.rs`
and `paths.rs` before), moved as it was. It's required: alai has no pure-Python
fallback (see [Shipping](#shipping)).

- **One call, two passes, all in Rust** (decided with the user,
  2026-10-23): `alai_core.artifact.extract(tarball, dest, *, limit,
  expected)` extracts a collection and returns the namespace, name and version its
  `MANIFEST.json` gives. A gzip stream can't be seeked, a tar has no index
  (members one after another, each a header and its data), and `tar -r`
  appends a replacement after the original, whose last copy is the one
  that counts. So:
  1. **Pass 1** reads only the headers: where each name's last member is,
     its type and link target, and the content of the last
     `MANIFEST.json` and `FILES.json`.
  2. **Checks, before anything is written**: `MANIFEST.json` names
     `expected`, compared as text (or, without it, gives three strings,
     which `_artifacts.extract_any` checks with `collection_name` and
     `Version` once it's extracted, into a directory the caller discards on
     an error); `FILES.json` is valid, with `serde_json`; and, in its order,
     every entry it lists has a last member, of that type, with a symlink
     not leading out by name.
  3. **Pass 2** writes only those last members, each file checked against
     its SHA-256 as it's written; a path resolving outside on disk stops
     it. Then every link is checked again, as a later link can change
     where an earlier one leads, and symlinks' checksums (of the file each
     points to, which can come later) and hardlinks' copies are taken from
     disk.

  Every error is raised where it's found, as an `ExtractError` whose
  `kind`, `entry` and `detail` say what and whose message core words, so
  the wording lives in one place; filesystem errors are `OSError`s.
  `_artifacts` adds where the artifact came from, the hint to raise
  `ALAI_MAX_UNPACKED_SIZE` for `too_large`, and "isn't a built collection"
  for a tarball without a `MANIFEST.json`. Python makes one call.
- **On Windows** (core's, decided with the user, 2026-10-28), which alai
  doesn't run on but alai-doc may: modes aren't set; a name Windows can't
  hold as named (a device such as `NUL.txt` or `COM¹`, a trailing `.` or
  space, or `<>:"|?*`) is refused as `path`, rather than renamed or
  skipped, as a renamed plugin isn't the plugin and core has no way to
  warn; a symlink target with `\` or `:` is refused as `link`; and a
  symlink is made as a directory or file link as `FILES.json` lists it,
  inline as on Unix, or, where Windows won't make links (no Developer
  Mode), copied from what it points to once everything is written. A
  `\\?\` path could create the refused names, but other programs reading
  the tree would then see a device or a different name.
- **Where case doesn't matter** (macOS and Windows by default), names are
  still compared exactly, as ansible-galaxy compares them, and every file is
  made with `O_EXCL`, so of two names differing only in case the second is
  refused (`FileExistsError`) rather than replacing the first, where
  ansible-galaxy's `'wb'` would. So a `manifest.json` that `FILES.json`
  lists can't shadow the `MANIFEST.json` that was checked; core's tests
  pin it where the filesystem doesn't tell case apart.
- **It replaced a split across the languages** (2026-10-22 to 23), whose
  logic was hard to follow: Python read the first `MANIFEST.json` and
  `FILES.json`, checked them and handed Rust a list and a "watch" set;
  Rust extracted in one pass and returned a replacement if a watched name
  repeated, for Python to check and retry, or restarted itself when any
  other name repeated, and held a member's errors until the end in case a
  later copy replaced it. A replaced `MANIFEST.json` went through the
  tarball three times and wrote the collection twice. Measured before the
  change: the headers-only pass costs about a third of each extraction
  (a10.acos_axapi 53 ms on 161 ms, cisco.catalystcenter 75 ms on 226 ms,
  33 to 44% across the eleven largest collections), and afterwards
  extraction took the old time plus that (a10.acos_axapi 246 ms,
  cisco.catalystcenter 318 ms, cisco.dnac 175 ms). It's hidden in a sync:
  a cold `alai sync` of sivel.acd with its lock took 5.26 to 6.28 s with
  it (prepare 2.9 to 3.2 s), as before; simulated beforehand, 5.50 to
  6.12 s against 5.95 to 6.65 s, and for the four largest collections
  2.02 to 2.25 s against 1.88 to 2.74 s. Holding each tarball decompressed
  in memory would read it once, at the cost of its whole size (60 MB for
  a10.acos_axapi, up to four at once); not needed while the second pass is
  hidden.
- **Reviewed on 2026-10-23**, for anything missed on speed or safety,
  with these changes (decided with the user):
  - Pass 1 set aside a `MANIFEST.json`'s or `FILES.json`'s claimed size
    before reading it, so a header claiming 1 TiB aborted the process
    (`memory allocation of 1099511627776 bytes failed`) rather than
    failing; now at most the read buffer's 256 KiB is, as the `tar` crate
    caps its own at 128 KiB, and the tarball is refused as corrupt.
  - A hard link's copy and a symlink's checksum read from disk outside
    the capped stream, so neither counted against the limit
    ([install.md](install.md#decisions)). They now spend the same budget
    as pass 2.
  - A pax global header that sets anything but `comment` is refused with
    the other members tar readers disagree on: the `tar` crate yields it as
    a member and ignores its keys, where tarfile applies them to every later
    member. One of only `comment` is allowed, and not taken as a member, as
    tarfile doesn't (decided with the user, 2026-10-23): it describes the
    archive, not its members, and it's all `git archive`, and so GitHub's
    source tarballs, writes, holding the commit.
  - Pass 2 wrote each member from its own read of the headers; it now
    writes from what pass 1 recorded and checked (kind, link target,
    executable bit), taking only a file's content from the second read.
  - The parent-directory cache compared a directory's resolved path with
    its literal one, so it never hit when the destination's own path went
    through a symlink: 108k `statx` calls against 18k for 10k files in 2k
    directories, 20 to 40% slower on tmpfs. It now compares with the
    resolved destination, and takes one `lstat` when the directory's own
    parent is known plain.
  - `umask` is no longer a parameter: the kernel masks each `open` and
    `mkdir` with the process umask, which is what Python read and passed,
    so the `fchmod` after every file (one syscall per file, 10k of 38k in
    the probe) went. `link_tree` keeps its `fchmod`, as a cached file's
    mode is to be kept exactly, as a hardlink keeps it.
  - The gzip decoder reads the 256 KiB buffered file directly
    (`flate2::bufread`), rather than through a second buffer of its own,
    and a listed directory is made without a `stat` first.
  - Considered and left: an optimistic single pass (the second pass is
    hidden in a sync, and the split flow it would bring back was just
    removed); `openat2` with `RESOLVE_BENEATH`, which would replace the
    name and `realpath` checks with the kernel's for a Linux-only path
    (the cache is the user's own, so the window it closes has no other
    writer); a cap on directory depth (a 2000-deep tree extracted on a
    128 KiB thread stack, as musl gives; only Python 3.10 and 3.11's
    recursive `shutil.rmtree` would fail to remove one deeper than
    about a thousand); and tarfile's joining of the ustar prefix field
    under GNU magic, which the `tar` crate doesn't, as alai is consistent
    with itself either way and nothing can escape through it.
- **Until it became required** (2026-10-22), a pure-Python backend,
  `_tar`, did the same, and every test ran against both. Keeping the two
  in step found three places they'd drifted, and with TOML editing to come
  the two couldn't have edited a file alike; the reasons are in
  [Shipping](#shipping).
- **Crates** (core's since 2026-10-28, but `sha2`, which alai's HTTP
  still uses): `tar`, `flate2` with the `zlib-rs` backend
  (pure Rust, so no C toolchain) and `MultiGzDecoder`, as Python's gzip
  reads every member of a multi-member stream, and `sha2`, which uses the
  CPU's SHA-256 instructions, and `serde_json` for `MANIFEST.json` and
  `FILES.json`: 0.91 ms for the largest `FILES.json` (a10.acos_axapi,
  850 KB) into typed structs, against Python's `json.loads` at 2.5 ms,
  and since 2026-10-23 for index lines and the plugin index, which it
  searches with `memchr` (already built, as `serde_json` depends on it);
  `config.json`, `collections.json` and alai's own caches stay in Python,
  whose `json` reads one large document as fast. It reads gzip only, as every build is gzipped. The crate needs Rust 1.92, the highest of the dependencies'
  own minimums (`pubgrub` 0.4 1.92, `sha2` 0.11 1.88, `pyo3` 0.30 1.85).
- **In development** maturin is alai's build backend, so `uv sync` builds
  the extension into `src/alai/` (an editable install) and every tox
  environment builds a wheel with it; both need `cargo`. uv's
  `cache-keys` include the crate and the workspace's `Cargo.toml` and
  `Cargo.lock`, so a change to the Rust rebuilds it.
  The sanity run checks it with `cargo fmt` and `clippy`.

Measured on the 97 `sivel.acd` tarballs, extracting each into a new
directory with `_artifacts.extract`, the downloads already done
(2026-10-22; XFS runs `sync` first, so the previous run's deletes aren't
timed):

| | tmpfs, 1 thread | tmpfs, 6 threads | XFS, 1 thread | XFS, 6 threads |
|---|---|---|---|---|
| `_tar` (pure Python) | 4.0–4.7 s | 8.4–8.6 s | 5.2–5.7 s | 9.2–9.5 s |
| `alai_native` | 1.6–1.7 s | 0.39–0.40 s | 2.3–2.8 s | 2.1–2.2 s |
| GNU `tar xzf`, one at a time, no hashing | 2.0 s | | 3.1 s | |

Reading every tarball through first, as an earlier version of this did
for last-wins, added 0.73 s on one thread (2.5 s, and 0.53 s on six).
On XFS it's bound by creating 46k files, not the CPU, and varies from
run to run: 1.3 to 2.2 s on six threads across the day's runs. A cold `alai sync` of `sivel.acd` with its lock, on XFS,
was 8.9 s in pure Python and 6.5 s with the extension; preparing
(downloading and extracting) went from 6.15 s to 3.47 s, and most of
what's left is installing, by clone ([Cloning](#cloning)).

### Shipping

- **Built into alai** (decided with the user, 2026-10-22): one
  distribution, `alai`, built with maturin as a mixed Python and Rust
  project, the extension a private module of the package, rather than a
  separate `alai-native`. Its API is shaped by what alai needs and changes
  with it, and one package is one thing to test. alai's wheels become
  platform wheels, and its sdist needs Rust.
- **Wheels for Linux x86_64 and aarch64, glibc and musl, and macOS arm64**
  (decided with the user, 2026-10-22): the platforms GitHub has runners
  for, so every wheel is built and tested natively. Not 32-bit x86 or ARM,
  ppc64le, s390x, riscv64 or Intel macOS, which build from the sdist (maturin's
  cross-compiling containers could build them, even armv7 at
  manylinux2014, if they're wanted).
- **manylinux2014, glibc 2.17** (decided with the user, 2026-10-22):
  Debian 8, Ubuntu 13.10, Fedora 19 and RHEL 7 or later (the manylinux
  project's list). Rust's
  standard library needs 2.17 on those, the extension nothing newer, and
  nothing is gained from a later tag: `manylinux_2_28` (AlmaLinux 8) would
  drop RHEL 7, Amazon Linux 2, SLES 12 and 15 before SP4 and Ubuntu 18.04,
  where people still run a newer Python from uv. The wheels must be built in the manylinux containers (maturin's GitHub
  action does by default) or with maturin's `--zig` for a given glibc: one
  built on the development machine (glibc 2.43) needed 2.34, as the linker
  takes each symbol's newest version (`stat64` at 2.33, `pthread_key_create`
  at 2.34); `statx`, `copy_file_range`, `getrandom` and `gettid` are weak,
  so the standard library finds them at run time. musl (Alpine) takes
  `musllinux_1_2` wheels; macOS arm64 needs macOS 11, Rust's minimum
  there, which `clonefile` fits.
- **Free-threaded builds: three wheels per platform** (tried on
  2026-10-22, maturin 1.15.0, pyo3 0.30 with `abi3-py310` and
  `abi3t-py315`, the latter pyo3's support for PEP 803's stable ABI for
  free-threaded builds). Built with a CPython from 3.10 to 3.14, maturin
  makes one `cp310-abi3` wheel, for CPython 3.10 and later; with 3.15 or
  3.15t, one `cp315-abi3.abi3t` wheel, for 3.15 and later whether
  free-threaded or not (installers prefer it on 3.15, its tag being the
  more specific); and with 3.14t, which has no stable ABI, a
  `cp314-cp314t` wheel, maturin warning that it's version-specific. Each
  free-threaded wheel loaded on its interpreter with the GIL left off (the
  module declares `gil_used = false`; Python turns the GIL back on, with a
  warning, for one that doesn't), and `test_artifacts.py`,
  `test_extract_safety.py` and `test_install.py` passed under each (207
  tests). tox runs the whole suite on 3.14t and 3.15t too
  (`py3{14,15}t-tests`), each building the extension as its wheel does
  (`cpython-314t` and `abi3t`), with `tests/test_native.py` checking, in a
  new interpreter, that importing it leaves the GIL off; none of alai's
  dependencies turns it on either (checked on 3.14t and 3.15t: PyYAML
  6.0.3's C loader, before YAML moved to alai-core, argcomplete, distlib, resolvelib, truststore, urllib3 (since replaced by the extension's HTTP),
  coverage, pytest and pytest-xdist). The `abi3.abi3t` wheel covers 3.16
  and later as `abi3` does, with no new wheel per release, by PEP 803's
  promise rather than a record yet, which the tox runs on each new release
  would catch. 3.13t, experimental, would need a `cp313t` wheel too; like
  `cryptography`, whose free-threaded wheels are `abi3t` and `cp314t`,
  alai needn't. PyPy has no stable ABI, so it builds from the sdist unless
  its own wheels are added. uv 0.13.0 installed the `abi3.abi3t` wheel;
  which pip releases understand the `abi3t` tag wasn't checked.
- **Rust required, no pure-Python fallback** (decided with the user,
  2026-10-22, reversing the fallback kept while measuring). The fallback
  was a second implementation of every rule (`_tar.py`, about 350 lines
  for extraction, every safety test run twice), and testing both found
  three places they had drifted; with `toml_edit`, a fallback can't give
  the same edits without reimplementing its formatting, so a file would be
  edited differently depending on how alai was installed. Nothing is
  released, so there's nothing to stay compatible with. Who'd need Rust:
  platforms PyPI takes no wheels for (FreeBSD, OpenBSD, NetBSD, illumos,
  AIX), Linux older than glibc 2.17, and anyone whose installer can't use
  the wheels; but ansible-core depends on `cryptography`, which needs Rust
  wherever it has no wheel either, and uv, ruff and pydantic made the same
  choice. So, the conditions:
  - Wheels for the common platforms, glibc and musl, and macOS, so the
    sdist is rarely used.
  - A declared `rust-version`: 1.92 since the resolver moved to `pubgrub`
    0.4 (2026-10-23, [resolver.md](resolver.md)); 1.88 before that (decided
    with the user, 2026-10-22), set by `sha2` 0.11.1; `pyo3` 0.30 needs 1.85. The lowest floor without
    known problems was 1.83, as `cryptography` declares, with `pyo3` 0.29
    and `sha2` 0.10: every `pyo3` before 0.29 has advisories fixed only from
    0.29 (RUSTSEC-2026-0176, an out-of-bounds read in list and tuple
    iterators, and RUSTSEC-2026-0177, a missing `Sync` bound), and the
    crate built unchanged with both, at edition 2021 (2024 itself needs
    1.85). But `sha2` 0.10.9 has the AArch64 code 0.11.1 fixed
    (RustCrypto/hashes#920): it loads four round constants through a
    reference to one, undefined behaviour under Stacked Borrows though the
    read stays in the table and the compiled code is correct, and nothing
    is planned for 0.10. With `pyo3` likely to need as much soon, 1.88 it
    is. Distributions' own Rust is older (Debian 12 1.63, Ubuntu 24.04 1.75
    by default). CI should build with 1.88 itself, so it stays the floor.
  - A `compile_error!` for anything that isn't Unix, so a Windows install
    from the sdist says why rather than failing to compile. Done. Other
    Unix systems, such as the BSDs, aren't blocked: only cloning is
    platform code, so there it fails with `EOPNOTSUPP` and `auto` moves on
    to a hardlink.
  - The sdist and every wheel published in one step, so nobody installs in
    the window between them and builds from source.
  - Maybe the crates vendored into the sdist, for offline builds, and a
    lighter release profile there than link-time optimisation with one
    codegen unit, which is slow on a Raspberry Pi or under emulation.
  - Documenting `pip install --only-binary alai` and
    `uv tool install --no-build-package alai alai` for failing rather than
    building.
- **CI** (`.github/workflows/ci.yml`, 2026-10-22):
  - Sanity runs `cargo fmt` and `clippy` with the rest; a job checks the
    crate with the `rust-version` `Cargo.toml` declares, read from it.
  - The sdist is built, the release tag checked against its version (from
    its `PKG-INFO`, as `uv version` can't read a dynamic version), and
    alai installed from it, compiling the extension.
  - Each platform's three wheels are built with maturin's action, pinned,
    maturin pinned to 1.15.0, with `--locked --compatibility pypi`: in the
    manylinux2014 and musllinux_1_2 containers on Linux, which have every
    interpreter, and on the host on macOS, with uv's Pythons. maturin
    audits each against the manylinux or musllinux rules itself (its
    `--auditwheel`), and `abi3audit` checks the `abi3` wheel uses only the
    stable ABI, as cibuildwheel would run it. Not the `abi3.abi3t` wheel:
    `abi3audit` 0.0.26 takes `PyModExport__native`, 3.15's module export
    hook (PEP 793), for a violation. maturin's action rather than
    cibuildwheel, whose FAQ points Rust projects to it; cibuildwheel would
    call maturin through the build backend in each container, after
    installing Rust there, with the free-threaded and `abi3t` builds to set
    up again.
  - Each wheel is tested where it runs: tox with every Python on x86_64
    glibc, and 3.10, 3.15, 3.14t and 3.15t on aarch64 glibc and macOS,
    each installing the wheel that Python would choose (`--installpkg`);
    on musl, in uv's Alpine image (`ghcr.io/astral-sh/uv:0.13.0-alpine`,
    which brings musl Pythons, free-threaded ones included), the same four
    with pytest. Run with docker rather than as the job's container, as
    JavaScript actions such as checkout can't run in an Alpine container
    on arm64. All of this was tried locally on aarch64: the musllinux
    wheels built, and the suite passed on musl with each.
  - **Releasing is two steps, for GitHub's immutable releases** (decided
    with the user, 2026-10-23, as in their PowerShell-RemoteWrench). Pushing
    a `v*` tag runs `ci.yml` for it, whose last job, once everything has
    passed, creates the tag's GitHub release as a draft with the sdist and
    every wheel as its assets, or replaces a draft's assets, refusing a
    release already published. Publishing the draft runs `publish.yml`,
    which downloads those assets and uploads exactly them to PyPI in one
    step, through trusted publishing in the `pypi` environment: what's on
    PyPI is what's attached to the release, which is then immutable. Every
    job asks only for the permissions it needs (`permissions: {}` at the
    top); only the draft can write to the repository. The draft job also
    attests each file's build provenance (`actions/attest`, SLSA, signed
    with Sigstore through the run's identity, stored in the repository's
    attestations by the file's SHA-256), so a wheel from anywhere can be
    checked as built by `ci.yml` from the tag, with `gh attestation verify
    <file> --repo alai-sh/alai-cli --signer-workflow
    alai-sh/alai-cli/.github/workflows/ci.yml --source-ref
    refs/tags/<tag>`. With the first release, the install docs get a short
    section on it, and that it works on a file from PyPI too, as
    attestations are keyed by the file's SHA-256; not before, as `docs/`
    describes only what works. GitHub's attestation of an immutable release and
    PyPI's publish attestations say what was released and how it reached
    PyPI; only this says CI built it, as a file uploaded to the draft by
    hand would pass both. So `publish.yml` verifies each file this way
    before uploading any: GitHub has no setting that stops someone with
    write access creating a release by hand or changing the draft's files
    before it's published, so the check makes what's attached trustworthy
    however it got there. A required reviewer on the `pypi` environment
    would gate publishing itself; it's left out while only the maintainer
    can write to the repository, and worth adding when anyone else can.
  - **What builds the release is pinned** (decided with the user,
    2026-10-23), so the same commit builds the same wheels and a new
    release of a tool can't change one unseen. The tests take the newest
    patch release of each Python that the pinned uv offers (uv's list of
    Pythons is built into each release), so they move when `UV_VERSION`
    does, not on their own. The pins, and how to move each:
    - The actions, by commit: Dependabot.
    - The runners: `ubuntu-26.04` and `ubuntu-26.04-arm`, one LTS release
      for both architectures, as arm64 has no `-latest` label to follow
      `ubuntu-latest` (decided with the user, 2026-10-23); moved together.
      actionlint 1.7.12 doesn't know the 26.04 labels yet and reports them;
      GitHub's runner-images list has both.
    - uv: `UV_VERSION` in `ci.yml` and the version in `docs.yml`. The musl
      tests' Alpine image is uv's, tagged with `UV_VERSION`
      (`ghcr.io/astral-sh/uv:<version>-alpine`), so it follows it, with no
      digest of its own: it's for testing, not building.
    - maturin: `MATURIN_VERSION` in `ci.yml`, which each build step's
      `maturin-version` takes.
    - Rust: `rust-toolchain.toml`'s `channel`, a stable release, which
      rustup and maturin's action in its containers both read; it isn't
      in the sdist, so a source build still needs only `rust-version`.
    - The build containers, by digest, which pins their Pythons
      (`MANYLINUX2014_*` and `MUSLLINUX_1_2_*` in `ci.yml`, each named by its
      platform's `container-env`, as a matrix can't read `env`): each
      image's `:latest` digest, from the registry,
      `curl -sI -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.v2+json" https://quay.io/v2/pypa/<image>/manifests/latest`
      (its `docker-content-digest`); uv's image the same from ghcr.io,
      with a token from `https://ghcr.io/token?scope=repository:astral-sh/uv:pull`.
    - The macOS build's Pythons (`MACOS_PYTHONS`), to the newest patch
      release of each that
      the pinned uv offers (`uv python list --all-versions --only-downloads`).
      The patch release doesn't change the ABI: CPython keeps it within a
      minor release, and pyo3 declares the C API per minor version, so a
      wheel built with 3.14.9t runs on 3.14.0t.

    All but the actions and Rust are in `ci.yml`'s `env` block. Renovate
    could move all of them; Dependabot moves only the actions.
  - uv installs tox without alai (`uv sync --no-install-project`, then
    `uv run --no-sync`), as tox builds or installs alai itself; `uv run`
    alone would compile it first in every job.
  - setup-uv's cache holds Python packages only, never alai, which every
    job skips, installs from the wheel, or rebuilds, so its key needn't
    follow the Rust; it's off for tag pushes, so a release starts clean.
    Every job names its entry (`cache-suffix`: `sanity`, `sdist`,
    `wheels-<platform>`, `test-<python>`, and `docs` in `docs.yml`), as
    setup-uv keys it by the runner's platform and Python only, which jobs
    on one runner share (glibc and musl wheels included, as setup-uv runs
    on the host, not in the build container), so they'd restore whichever
    job's packages were saved first and fail to save their own (decided
    with the user, 2026-10-23). Cargo isn't cached: a clean
    release build is about 12 s and clippy 5 s, not worth another action
    to pin, and the wheels build in containers.
  - basedpyright doesn't report `_native` having a stub but no module
    (`reportMissingModuleSource`): the built module is only in `src/alai/`
    after a local `uv sync`, and sanity failed on CI without it.
  Every contributor needs `cargo`.
- **TOML lives there too** ([below](#toml-in-the-extension)).

### TOML in the extension

Decided with the user, 2026-10-23: every TOML file alai reads, writes or
edits goes through `toml_edit` 0.25 (TOML 1.1), cargo's and uv's, in
`alai_core.toml` (alai-core's, since 2026-10-28, and `src/rust/src/toml.rs`
before):

- **Reading**: `read` (and `Document.from_path`, for editing) reads
  the file with the GIL released and gives what `tomllib.load` gives,
  datetimes as `datetime` objects, for `alai.toml`, the config files and
  `alai.lock`. Every caller goes through `_config.read_toml` or
  `read_toml_document`, so a file that can't be read, isn't UTF-8 (`not
  UTF-8 (at line 2, column 7)`, placed as a syntax error is) or isn't
  valid TOML gets the same error wherever it's read. `loads` and
  `Document(text)` take text, for the tests.
  Every Python now reads the same TOML, so `tomli` is gone as a dependency
  for 3.10, and TOML 1.1's multi-line inline tables can be used in
  `alai.toml`: before, a `[project.sources]` entry spread over lines was
  read by tomli 2.5.0 (3.10) and 3.15's `tomllib` but refused by 3.11's to
  3.14's (checked 2026-10-22), and uv 0.13.0 locked the same layout in
  `[tool.uv.sources]`. A syntax error keeps `tomllib`'s one-line form,
  `... (at line 3, column 7)`, the column in characters, with
  `toml_edit`'s wording. On the 25 KB lock of `sivel.acd` it was 0.36 ms
  against `tomllib`'s 1.78 ms, giving the same data; importing `_native`
  costs 1.8 ms, and is only done once a file is read.
- **Writing `alai.lock`**: `dumps` builds the document from a mapping
  `render_lock` lays out, with fixed rules for tables, inline tables,
  dotted keys and wrapped arrays, in place of the emitter in `_lock.py`
  (decided with the user, 2026-10-23, so there's one TOML writer). It
  writes every existing lock byte for byte as the emitter did, `sivel.acd`'s
  in 0.44 ms; only a string with a `"` or `\` in it is written differently,
  as a literal string where `toml_edit` picks one ([lockfile.md](lockfile.md)).
- **Editing `alai.toml`**: `TomlDocument`
  ([manifest-editing.md](manifest-editing.md#the-editor)).
- **GIL**: parsing releases it; turning the document into Python objects,
  and the edits, hold it, as they're mostly building or reading them.

### YAML in the extension

Decided with the user, 2026-10-28: `galaxy.yml` and `meta/runtime.yml` are
read with `alai_core.yaml.loads` (`_galaxy.yaml_mapping`), and PyYAML is no
longer a dependency. Core parses with libyaml-safer, a Rust port of the
libyaml parser ansible-core uses through PyYAML. It read 108,966 of the
108,968 docs YAML documents in the latest collections exactly as PyYAML
does. The YAML 1.2 parsers each refused 842 to 2,061 that Ansible reads.
Its values differ from PyYAML's on purpose: booleans follow YAML 1.1
(`yes`, `no`), and a number is converted only when its `str()` is the
text, so an unquoted `version: 1.10` stays `"1.10"` (refused as a version)
rather than PyYAML's float `1.1`. A duplicate key warns, naming the file,
where PyYAML keeps the last silently. An editor that keeps `galaxy.yml`'s
formatting, for `alai add` in a collection project, can build on core's
parser marks later
([collection-development.md](collection-development.md#not-done-yet)).

## JSON output

Measured 2026-11-01 on `alai find '*' -a --limit 0 --output-format json
--offline`, 35 MB of output from the 106k-line plugin index, on Python 3.15.
The machine's speed drifted through the day, so each row is against the one
above it, run back to back:

| | `_print_json` | Whole command |
|---|---|---|
| `json.dump` | 818 ms | 1.66–1.72 s |
| `json.dumps`, then one write | 441 ms | 1.30–1.32 s |
| `json.dumps` (a later run) | 403–440 ms | 1.16–1.31 s |
| and each plugin built from its fields | 201–248 ms | 0.94–1.16 s |

- `json.dump` always uses the pure Python encoder. `json.dumps` uses the C
  one, which handles `indent` since 3.13, so it's about eight times faster
  there. On 3.10 it's slower (161 ms against 118 ms on a 6.4 MB test).
  `json.dumps` holds the whole string, about 70 MB more peak memory for
  this output.
- Each plugin was its index line parsed again with `json.loads`, 158 ms for
  106k lines. Built from the fields core already read, it's ~200 ms less
  in `_print_json` ([find.md](find.md)). A `PluginEntry.raw` in core,
  turning core's parse into a dict, saved only 60–75 ms.
- Writing straight to the file descriptor from Rust gave `json.dump`'s
  memory at near `json.dumps`'s speed (79 ms against 60 ms to encode).
  It wasn't kept, as it gains nothing on speed.
- Every key is now always set, `null` where it doesn't apply, which adds
  ~236k `null`s to this output, 35 MB to 41 MB, with no change in time.
- The indent barely matters. Compact output is 27 MB against 35 MB and
  encodes within 10% of `indent=2`.
- Joining every line into one `json.loads` was slower in place, by about
  100 ms, though faster in a micro-benchmark.
