# Installing

How `alai sync` (slice 4) gets each locked collection onto disk. Step 1, the
artifact cache, is in `src/alai/_artifacts.py`; installing into
`collections_dir` and the command itself follow.

## Decisions

- **Artifacts are fetched by the lock's `cksum`, from the index's `dl`
  template** (`artifact_url`), streamed to a file and hashed as they arrive
  (`Fetcher.download`). A download stops once it passes the lock's `size`,
  so a wrong or hostile response can't fill the disk, and the SHA-256 and
  size must both match the lock, as the index spec requires ("MUST verify
  the sha256 of every download against `cksum`"). Transport compression is
  undone first, so the hash is over the file itself.
- **The cache holds extracted collections, keyed by `cksum` alone**:
  `<cache_dir>/collections-v1/<sha256>/`, as the spec's client rules
  suggest, so one artifact from two indexes is stored once and moving an
  index invalidates nothing. The tarball is downloaded into a temporary
  directory beside it and deleted once extracted, as uv keeps unpacked
  wheels rather than the archives. The extracted directory is renamed into
  place, so it exists only when complete; if another alai renamed the same
  artifact in first, theirs is used. `-v1` changes if what's extracted, or
  how, ever does, rather than reusing a layout older alai wrote.
- **Extraction is ansible-galaxy's** (`install_artifact` in ansible-core),
  so an installed collection is the same either way. Checked on 2026-10-20
  against `ansible-galaxy collection install` of the same tarballs for
  ansible.posix 2.2.2, community.general 10.7.3 and ansible.windows 2.8.0
  (296, 4,343 and 901 entries, 56 symlinks and 70 executables between
  them): the same paths, types, modes, link targets and contents.
  - Only `MANIFEST.json`, `FILES.json` and what `FILES.json` lists are
    extracted, each file checked against its `chksum_sha256` (a symlink's
    is its target's, as ansible-galaxy checks it). Anything else in the
    tarball is left out.
  - `MANIFEST.json` must name the locked namespace, name and version, so a
    wrong artifact behind the right checksum (an index error) is caught.
  - Files are `0644`, or `0755` when the tarball marks them executable for
    the owner; directories are `0755`. ansible-galaxy sets those modes
    exactly; here they're masked by the umask, as the cache note in
    [index-client.md](index-client.md) asks, so a `077` umask keeps the
    cache private. Cached files are hardlinked into `collections/` later,
    so these are the installed modes too; the cache's own index files stay
    `0600`.
- **Every path and symlink must stay inside the collection**, by name and
  on disk. A name that's absolute, has a backslash, or has an empty, `.` or
  `..` part is refused, as is a link whose target, joined to its own
  directory, is absolute or climbs out. That isn't enough on its own: links
  chain, so `a -> .` and `s -> a/..` each pass by name while `s` is the
  collection's parent on disk. So the parent of every entry is resolved
  with `realpath` before writing, as ansible-galaxy's `_is_child_path`
  does, and every link is resolved again once all exist, since a later link
  can change where an earlier one leads. Device files, FIFOs and anything
  but files, directories, hardlinks and symlinks are refused.
- **The cache is trusted after extraction**, as uv trusts its own. Tested
  with uv 0.13 and a scratch `UV_CACHE_DIR` on 2026-10-20: after editing a
  cached package's file, `uv sync`, `uv sync --reinstall` and
  `uv sync --reinstall-package` all installed the edited copy; only
  clearing the cache helps. The docs already say alai trusts its cache and
  that a `cache_dir` outside the home directory is the user's to restrict
  (see [index-client.md](index-client.md)).
- **Downloads run in parallel**, up to the fetcher's 16 connections, each
  extracted in its own thread. Every artifact is attempted; when several
  fail, the first by name is reported. Three artifacts (3.6 MB) took 1.6 s
  cold from `dl.alai.sh` and 1 ms warm.

## Not done yet

- **Installing into `collections_dir`**, with `link_mode` (hardlink by
  default, copy as the fallback), removing what the lock no longer lists,
  and choosing groups.
- **Verifying against `FILES.json` later.** Every published collection
  carries a SHA-256 for each file. An `alai cache verify` command, or
  `--verify` on `sync`, could check cached or installed collections against
  it, which uv has no equivalent for.
- **Progress** while downloading; see [output.md](output.md).
- **Leftover `.tmp-*` directories** from a run killed mid-download stay in
  `collections-v1/` until a future `alai cache clean` or prune removes them;
  they're never read.
