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

The docs' landing page compares `alai sync` with ansible-core 2.20.0's
`ansible-galaxy collection install -r requirements.yml -p ./collections`
(2026-10-21, this machine, alai on Python 3.15, ansible-core on 3.14). Each
of five runs per tool started with a new, empty `HOME`, so neither tool's
cache carried over: ansible-galaxy keeps everything under
`~/.ansible` (its API response cache in `galaxy_cache/api.json`), alai under
`~/.cache/alai`. Per run: a first install into a new project (ansible-galaxy
with `--clear-response-cache`), the same command again with nothing to
change, then a first install into a second project with the cache kept.
alai ran with a committed `alai.lock`, and again without one, which
resolves as ansible-galaxy always does. Medians:

| | `community.general` | `cisco.ios` (4 collections) |
|---|---|---|
| ansible-galaxy, first install | 16.5 s | 27.8 s |
| ansible-galaxy, again into another project | 12.8 s | 15.1 s |
| ansible-galaxy, nothing to change | 0.23 s | 0.25 s |
| ansible-galaxy `--no-cache`, first install | 43.6 s | not run |
| alai without a lock, first install | 1.38 s | 0.87 s |
| alai with a lock, first install | 0.88 s | 0.56 s |
| alai without a lock, again into another project | 0.23 s | 0.20 s |
| alai with a lock, again into another project | 0.08 s | 0.07 s |
| alai, nothing to change | 0.03 s | 0.03 s |

Galaxy dominated ansible-galaxy's times and varied widely (15.7 to 78 s for
a first install of `community.general`), so they're medians, and worth
measuring again before quoting new numbers.

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
  what a native extension replaces.

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

## Planned: a native extension

Open (raised 2026-10-21): whether the extension should parse YAML and TOML
too, for `galaxy.yml`, `meta/runtime.yml`, `alai.toml` and `alai.lock`.
PyYAML is used for now, with its C loader where it's built with libyaml,
and only imported in a collection project.

A separate library, `alai-tar` say, in Rust with pyo3, doing what's slow
above: reading the tarball, decompressing, hashing, writing files, and
cloning or linking them into place, with the GIL released so it can use
every core. alai depends on it unconditionally and stays one pure-Python
wheel itself.

- **Rust only, no pure-Python fallback.** A fallback is a second
  implementation of the same rules, the path and symlink checks included,
  to keep in step for ever; with Rust alone there's one. Today's `tarfile`
  code goes when the extension arrives.
- **Wheels for every platform PyPI takes, and an sdist that needs Rust.**
  PyPI accepts wheels for manylinux and musllinux (x86_64, i686, aarch64,
  armv7l, ppc64le, s390x, riscv64, and manylinux ppc64), macOS, Windows,
  iOS, Android and Emscripten (warehouse's `is_valid_platform_tag`,
  `warehouse/utils/wheel.py`). It takes none for FreeBSD, OpenBSD,
  NetBSD, illumos or AIX, so those, and any Linux with a glibc older than
  the manylinux baseline built for, install from the sdist and need
  `rustc` and `cargo` (FreeBSD: `pkg install rust`). pip and uv only use
  the sdist when no wheel matches, so that's automatic.
- **Building from source anywhere is opt-in** with the installers' own
  flags: `pip install --no-binary alai-tar …`, `uv pip install
  --no-binary-package alai-tar …`, `uv tool install --no-binary-package
  alai-tar alai`, `[tool.uv] no-binary-package = ["alai-tar"]` or
  `UV_NO_BINARY_PACKAGE`. Publishing a pure-Python `py3-none-any` wheel
  as well, as charset-normalizer does, was considered and dropped: it
  would be the second implementation, and a platform without a native
  wheel would silently get the slow one unless its user knew to pass those
  flags.
- **`abi3` wheels, plus free-threaded ones.** One `abi3-py310` wheel per
  platform covers CPython 3.10 to 3.15, but free-threaded builds (3.13t,
  3.14t) can't load the stable ABI, so they need their own per-version
  wheels (`cp313t`, `cp314t`); pyo3 supports them since 0.23 with the
  module declared `gil_used = false`, and cibuildwheel builds them once
  enabled. PEP 803 proposes a stable ABI for free-threaded builds, which
  would fold those into one wheel; its status wasn't checked. PyPy has no
  `abi3` either and would need its own wheels or the sdist.
- **TOML editing could live there too.** `alai add` and `alai remove` edit
  `alai.toml` with a small checked editor of our own
  ([manifest-editing.md](manifest-editing.md)) because the mature
  format-preserving editor, `toml_edit` (cargo's and uv's), is Rust with no
  maintained Python binding. Exposing it from the same extension would cover
  edits beyond one requirement array, such as `[project.sources]` entries;
  worth deciding when the extension is designed.
- **Until then**, cheap wins in today's Python: a process pool for
  extraction (every core, at the cost of shipping errors and progress back
  from the workers); for clones, raw file descriptors (1.42 s to 1.20 s in
  a loop test), creating each file with its mode instead of `copymode`,
  `os.scandir`'s cached types instead of `islink`, and a thread pool, as
  the opens and the ioctl release the GIL.
