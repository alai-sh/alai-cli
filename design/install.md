# Installing

How `alai sync` gets each locked collection onto disk: the artifact cache
is `src/alai/_artifacts.py`, installing into `collections_dir` is
`src/alai/_install.py`, and the command is `src/alai/_commands/sync.py`
with its logic in `src/alai/_sync.py`. The user documentation is
`docs/managing-collections.md#installing`.

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
- **Downloads run in parallel, extraction one at a time**: up to the
  fetcher's 16 connections download at once, but extracting is CPU-bound
  Python, so under the GIL more at once is slower. Every artifact is
  attempted; when several fail, the first by name is reported. The
  measurements are in [performance.md](performance.md#extraction).
- **Extraction looks members up once and reads forward**: a dict of
  members built once, rather than `tarfile`'s `getmember`, which searches
  every member on each call; members taken in the order they're stored, so
  the gzip stream only moves forward; and a parent directory with no
  symlink in its path checked once (one that has a link is checked every
  time, as a later link could redirect it). See
  [performance.md](performance.md#extraction).

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
  code the lock didn't choose in place. `collection install` does the same
  for the collections it installs, asked for or needed, and leaves every
  other one alone (2026-10-21); `collection install --keep-unversioned`
  leaves an unversioned one as it is instead, as if it were the version
  asked for, and doesn't resolve one that's asked for, so it needn't be on
  an index. A dependency kept that way is still resolved from the index,
  which decides what else it pulls in. Whether this should be the default
  is a question for users ([user-questions.md](user-questions.md)). uv 0.13.0 can't always read a version
  either: a `site-packages` directory with no `.dist-info` isn't a package
  to it, so `uv pip install` writes over it and leaves any other files mixed
  in, while a `.dist-info` whose `METADATA` has no version, or one that
  doesn't parse, makes `uv pip install` fail ("Failed to read metadata");
  `uv pip uninstall` still removes it, by the version in the directory's
  name. A collection has no separate record like `.dist-info`, so alai
  replaces the whole directory rather than merge files into it. A broken
  symlink is replaced or removed the same way, and a symlink is only ever
  unlinked, never followed, so what it points at survives.
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
  macOS and the `FICLONE` ioctl on Linux. `FICLONE` needs Linux 4.5 or later (before
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

## The command

All tested against uv 0.13 on 2026-10-20 with a project having a `dev` and
a `docs` dependency group.

- **`sync` locks first when it has to, as `uv sync` does**: when
  `alai.lock` is missing, or the no-network `_lock.stale()` check says it's
  out of date (`alai.toml` asks for something else, or a locked index isn't
  configured), it resolves, keeping locked versions that fit, and writes the
  lock before installing. Otherwise it uses the lock as it is, with no
  network, which is uv's fast path ("Resolved 4 packages in 2ms"). Unlike
  `alai lock`, it doesn't print the lock's change lines, as `uv sync`
  doesn't; the install lines say what changed.
- **`--locked` is `lock --check` before installing**: it resolves, and
  exits 1 without installing if the lock is missing or would change; it
  never writes the lock, even one re-resolved to the same collections.
  **`--frozen`** installs from the lock without checking it, and fails only
  if there's none, both with uv's flags' meanings. A `--frozen` lock entry
  whose index isn't configured any more is downloaded from the URL the lock
  records, as uv uses its lock's URLs.
- **`-U` and `-P` as uv has them on `sync`**: an upgrade always resolves,
  even with a current lock, then writes the lock and installs; with
  `--locked` it fails if the lock would change, and with `--frozen`, which
  resolves nothing, it's an error. The same flags are on `lock`, `add` and
  `remove`, all passing `upgrade` to the resolver.
- **Groups are uv's, with `dev` the default**: the project's collections
  and its `dev` group, if it has one; `--group` adds, `--all-groups` takes
  every one, `--no-group` (winning over both), `--no-default-groups` and
  `--no-dev` take away, and `--only-group` and `--only-dev` install only
  those groups without the project's collections, and can't be combined
  with `--group` or `--all-groups`. A group `alai.toml` doesn't have is an
  error ("Group `nope` is not defined", in uv), except `--no-group dev` and
  `--no-dev`, which a CI job should be able to pass to any project. What a
  selection needs is worked out from the lock (`Lock.select`): the named
  collections and everything their locked `dependencies` reach. Every group
  stays in one resolution, so switching groups never changes the lock.
- **Only what the plan installs is downloaded**, and only what isn't
  cached; with nothing to fetch, the fetcher and urllib3 aren't imported at
  all. A sync with nothing to do took 0.02 s for the whole process.
- **Output is uv's**, on stderr: `Resolved N collections in …` (not with
  `--frozen`, as uv), `Prepared N …` when anything was downloaded,
  `Uninstalled N …` counting removals and replacements (not an unlinked
  namespace, which isn't a collection), `Installed N …`,
  then one line per change, by name, a removal before its replacement:
  ` - ns.name==1.0.0`, ` + ns.name==2.0.0`, ` - ns.name (unknown version)`
  for a directory without a version, ` - ns (namespace link to …)` for an
  unlinked namespace. With nothing to change it's
  `Checked N collections in …`.

## Installing by hand

`alai collection install` and `alai collection uninstall`
(`src/alai/_collection.py`), as `uv pip install` and `uv pip uninstall`
are. Tested with uv 0.13 on 2026-10-20 in a venv:

- **Install resolves what's asked for and what it needs, preferring what's
  installed**: the resolver takes the installed versions as a preference by
  version alone, as it takes a lock's, and `-U` and `-P` drop it. uv
  installed `requests` and four dependencies; after `uv pip install
  'idna<3.10'` replaced `idna 3.20` with 3.9, installing `requests` again
  kept 3.9, and `-U requests` moved it back.
- **Already satisfied resolves nothing**: when every requirement is met by
  an installed version and nothing is to be upgraded, it's `Checked N
  collections` with no network, as uv's "Checked 1 package" for a second
  `uv pip install requests`. Like uv, that doesn't look at what the
  installed collections depend on.
- **Nothing is removed that wasn't asked for**: `install_locked` runs with
  `exact=False`. The project's `[project]` requirements and `alai.lock` are
  neither used nor written. An `alai.toml`, where the project has one, is
  still read as for any command: it's the first marker discovery looks
  for, and its settings (the indexes, `collections_dir`, `link_mode`)
  apply, as uv's `pip` commands still read `uv.toml` (a `uv.toml` index
  URL was used by `uv pip install`). Without one, an `ansible.cfg` or
  `collections/ansible_collections/` marks the project. A failure names the requirement as `(from the
  command line)`.
- **Uninstall removes only the collections named**; what they depend on
  stays, as uv left `idna`, `certifi` and the rest after `uv pip uninstall
  requests`. One that isn't installed is a warning, and with none left it's
  "no collections to uninstall", exit 0, as uv's "Skipping six as it is not
  installed" and "No packages to uninstall".
- **Where: the project's collections directory, or an explicit one.** uv
  won't install without an environment ("No virtual environment found;
  run `uv venv` … or pass `--system`"). Here a project is any of
  discovery's markers, so an `ansible.cfg` or an existing
  `collections/ansible_collections/` counts without an `alai.toml`; with
  no marker, `collections_dir` has to be set (`--collections-dir`,
  `ALAI_COLLECTIONS_DIR`, a config file), or it's an error, rather than a
  `collections/` in whatever directory alai runs from.
- **Which directory is said first**, dim, on stderr:
  `Using collections directory at: ~/project/collections`, unless it's
  `collections/` in the current directory. uv 0.13.0 (2026-10-21) prints
  `Using Python 3.15.0 environment at: <path>` first for `pip install` and
  `pip uninstall`, from a subdirectory of the project, with `--python` and
  with `VIRTUAL_ENV`, and not for `.venv` in the current directory; the
  path relative when under the current directory, else absolute. alai
  shows the absolute ones with `~` for home. Asked for after the
  recordings showed an install from `playbooks/` going to the project's
  `collections/` above it with nothing saying so.
- **`alai sync` undoes them**, being exact: what was installed by hand goes
  and what was uninstalled comes back, unless `--inexact`. The docs say so.

## Reinstalling

`--reinstall` and `--reinstall-collection NAME` (asked for 2026-10-21), as
uv's `--reinstall` and `--reinstall-package`, put a collection in the plan's
`install` even when its installed version matches, so one edited in place
can be put back, as ansible-galaxy's `--force` does. Tried with uv 0.13.0
on 2026-10-21 in a project with `requests` and its four dependencies:

- **On every command that installs**: `sync`, `add`, `remove` and
  `collection install`, as uv has them on `sync`, `add`, `remove` and
  `pip install`; not on `lock`, where uv rejects `--reinstall`. The long
  flag says "collection" for the reason `-P` does
  ([lockfile.md](lockfile.md)), and neither has a short flag, as uv's
  don't.
- **From the cache, not downloaded again**: `uv sync --reinstall --offline`
  reinstalled all five, and an edited cached file was installed as edited
  (above). The extracted cache is checked against the lock's `cksum` when
  it's made and trusted after, as uv trusts its own, so nothing is
  re-extracted. uv's summary says `Prepared 5 packages` even then, since
  `--reinstall` implies its `--refresh`; here `Prepared` counts downloads,
  and the index files are revalidated every run anyway
  ([index-client.md](index-client.md)), so a reinstall from the cache has
  no `Prepared` line.
- **Shown as `~`**, yellow, for one replaced by the same version (`~
  idna==3.20`), with `Uninstalled N` and `Installed N` counting it, as uv
  does. One that changes version as well is `-` and `+` as usual (`uv sync
  --reinstall-package idna` with 3.10 installed and 3.20 locked).
- **`--reinstall` is everything installed this run**: on `sync` every
  selected locked collection, on `remove` every one left
  (`uv remove --reinstall six` reinstalled the other five), and on
  `collection install` everything resolved, what's asked for and what that
  needs (`uv pip install --reinstall requests` reinstalled all five).
- **A name that isn't being installed is ignored**, with no warning:
  `uv sync --reinstall-package nothere` said "Checked 5 packages", and so
  did `uv pip install --reinstall-package certifi idna` with `certifi`
  installed but not in that resolution, and `uv sync --inexact
  --reinstall-package six` with `six` an extra. Names are compared
  normalised (`--reinstall-package IDNA` reinstalled `idna`); here an
  invalid name is an error, as for `-P`, and uv's is a parse error ("Not a
  valid package or extra name", exit 2).
- **`collection install` resolves when reinstalling**, rather than saying
  `Checked` because everything asked for is already there, as `uv pip
  install --reinstall-package idna requests` resolved first.
- **No error with `--frozen` or `--no-sync`**: `uv add --frozen
  --reinstall` and `uv add --no-sync --reinstall` were accepted and did
  nothing to install, where `--frozen` with `--upgrade` is an error.
  `sync --frozen --reinstall` reinstalls from the lock, as `uv sync
  --frozen --reinstall-package idna` did.
- **`--keep-unversioned` can't be used with either**, an error before
  anything is resolved: keeping a collection as it is and reinstalling it
  ask for opposite things. uv has nothing like it to compare with.

## Not done yet

- **`collection install -r requirements.yml`**, ansible-galaxy's
  requirements file, as `uv pip install -r requirements.txt` takes pip's:
  shelved, see [requirements-file.md](requirements-file.md).

- **Speed**: replacing `tarfile` with a native extension, and making
  clones cheaper; see [performance.md](performance.md).
- **Verifying against `FILES.json` later.** Every published collection
  carries a SHA-256 for each file. An `alai cache verify` command, or
  `--verify` on `sync`, could check cached or installed collections against
  it, which uv has no equivalent for.
- **Progress** while downloading; see [output.md](output.md).
- **Leftover `.tmp-*` directories** from a run killed mid-download stay in
  `collections-v1/` until a future `alai cache clean` or prune removes them;
  they're never read.
