# Installing

How `alai sync` (slice 4) gets each locked collection onto disk: the
artifact cache is `src/alai/_artifacts.py`, installing into
`collections_dir` is `src/alai/_install.py`, and the command itself
follows.

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

- **What's installed is read from each collection's `MANIFEST.json`**, as
  uv reads each package's installed metadata, with no record of alai's
  own. A locked collection already at its locked version is left alone,
  another version is replaced, and nothing else is compared, so an edited
  file stays edited until a `--verify` exists. Tested with uv 0.13 on
  2026-10-20: `uv sync` left a package at its locked version alone ("Checked
  1 package"), replaced one pip had put there at another version
  (`- idna==3.6`, `+ idna==3.10`), and kept an edit to an installed file. A
  record file mapping names to `cksum` was considered, to tell two
  artifacts of one version apart and to know which directories alai made,
  and dropped: uv does without one, and installing custom content is what
  path and git sources will be for. A `uv pip install`-style command,
  outside the lock, could come later.
- **`sync` is exact, as `uv sync` is**: an installed collection the lock
  doesn't list is removed, whoever installed it, and `--inexact` keeps
  extras (uv's flag), for a shared `collections_dir` such as
  `/usr/share/ansible/collections`. The default `collections/` in the
  project is alai's as `.venv` is uv's. uv removed a package `uv pip`
  installed (`- six==1.17.0`) and kept it with `--inexact`.
- **Every `<ns>/<name>` directory or symlink is an installed collection**,
  named by its path as Ansible loads it, so one without a `MANIFEST.json`
  has no version, never matches the lock, and is replaced if the lock names
  it and removed if not. Leaving such a directory alone, as protection for
  a source checkout, was the first version (2026-10-20) and was dropped:
  `sync` makes `collections_dir` match the lock. ansible-galaxy instead
  names it from its path at version `*` (`from_dir_path_implicit`), which
  satisfies any requirement, so it skips it without `--force`; that leaves
  code the lock didn't choose in place. A broken symlink is replaced or
  removed the same way, and a symlink is only ever unlinked, never followed,
  so what it points at survives.
- **Nothing is written or deleted through a symlink in the tree**, only
  unlinked. Links inside a collection are deleted with it, never followed
  (`shutil.rmtree`, which works through file descriptors here:
  `avoids_symlink_attacks` is true), and a collection that's a link is
  unlinked. A namespace that's a link (`ansible_collections/ns ->
  /elsewhere`) isn't installed at all: what's behind it doesn't count, so a
  locked collection there is installed afresh, into a real `ns/` made after
  unlinking it, and with `sync` exact the link goes like anything else the
  lock doesn't name; `--inexact` keeps one no locked collection needs. The
  first version followed it, and removing `ns.coll` deleted
  `/elsewhere/coll`. Unlinking for a locked collection hides the extras
  that shared the link, even with `--inexact`, but deletes none of them.
  `collections_dir` and its `ansible_collections` are followed if they're
  links: where to install is the user's choice.
- **Path and git sources will carry their own install record**, as uv's
  editable installs do. Tested with uv 0.13: an editable path dependency
  installs only `mylib.pth` (the checkout's path) and `mylib-1.0.0.dist-info/`
  with the usual metadata plus `direct_url.json`
  (`{"url": "file:///…/lib", "dir_info": {"editable": true}}`); `uv sync`
  matches that against the lock ("Checked 1 package") and reinstalled it
  once the `dist-info` was deleted. The source itself is never in
  `site-packages`, so replacing or removing an editable install only removes
  the pointer. Here an editable source is a symlink to its checkout, whose
  target is the record, and a built git or path source is in the cache with
  a `MANIFEST.json`, needing a record of the commit or path beside it (like
  ansible-galaxy's `.info`, alai's `direct_url.json`); see
  [lockfile.md](lockfile.md#planned-other-sources).
- **Each collection is installed beside its place and renamed in**: built
  as `<ns>/.alai-new-<name>-<random>`, then the old copy renamed aside,
  the new one renamed into place, and the old one removed, so a failure
  leaves what was there and Ansible never loads half a collection.
  Leftovers from a killed run are removed the next time that collection
  is installed. ansible-galaxy's `<ns>.<name>-<version>.info` records of a
  copy alai replaced or removed go too, as ansible-galaxy removes its own
  on reinstall, since they'd describe a copy that's gone. A namespace
  directory left empty is removed.
- **`link_mode` `auto` is uv's: clone, then hardlink, then copy**, keeping
  to the first that works for the rest of the run, with a warning only
  when it comes to copying. uv 0.13's default is "`clone` … on macOS and
  Linux, and `hardlink` on Windows", and on tmpfs it warned "Failed to
  hardlink files; falling back to full copy". Clones use `clonefile` on
  macOS and the `FICLONE` ioctl on Linux; Windows has none, so `auto` starts
  at hardlink there in effect. `FICLONE` needs Linux 4.5 or later (before
  that the same number was btrfs's `BTRFS_IOC_CLONE`) and a filesystem with
  reflinks (btrfs, XFS with `reflink=1`, the `mkfs.xfs` default since
  xfsprogs 5.1, bcachefs, OCFS2, NFS 4.2 where the server clones), and
  nothing from the C library: `fcntl.ioctl` passes the number through. The
  number is `_IOW(0x94, 9, int)`: Python 3.12's `fcntl.FICLONE` is used
  when it exists, else `0x40049409`, as x86 and ARM encode it. alpha, mips,
  parisc, powerpc and sparc encode `_IOW` differently (`0x80049409`), so on
  Python 3.10 and 3.11 there the clone fails and `auto` hardlinks; that's
  best effort, fixed by a newer Python or another `link_mode`, rather than a
  table of architectures. Anything else fails with
  `EOPNOTSUPP`, `ENOTTY`, `EINVAL` or `EXDEV`, and `auto` moves on to
  hardlinks. Directories and symlinks are recreated, not
  linked. Measured on XFS for ansible.posix, ansible.windows and
  community.general (5,540 files): clone 0.25 to 0.83 s, hardlink 0.08 s,
  copy 0.28 to 0.37 s. Hardlinks are fastest, but share data with the
  cache, so editing an installed file edits the cached one, which clones
  don't; `auto` keeps uv's order for that. tmpfs can't clone, so `auto`
  hardlinked there (0.17 s).

## Not done yet

- **The `alai sync` command**: reading the lock, choosing groups,
  downloading only what the plan installs, `--inexact`, and its output.
- **The `sync` docs need a warning admonition on symlinks in
  `ansible_collections`**: a namespace that's a link is replaced by a real
  directory when a locked collection is in it (hiding its other collections,
  even with `--inexact`) and unlinked by an exact sync otherwise; a
  collection that's a link is unlinked, never followed; and nothing behind
  a link is ever deleted.
- **Verifying against `FILES.json` later.** Every published collection
  carries a SHA-256 for each file. An `alai cache verify` command, or
  `--verify` on `sync`, could check cached or installed collections against
  it, which uv has no equivalent for.
- **Progress** while downloading; see [output.md](output.md).
- **Leftover `.tmp-*` directories** from a run killed mid-download stay in
  `collections-v1/` until a future `alai cache clean` or prune removes them;
  they're never read.
