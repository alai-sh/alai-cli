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
  own for an index's collections; one copied from a directory has one
  ([Directory and editable sources](#directory-and-editable-sources)).
  A locked collection already at its locked version, from the same source,
  is left alone, another version is replaced, and nothing else is
  compared, so an edited
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
- **Collections from a source other than an index carry an install record**
  (2026-10-22), as uv's do: see
  [Directory and editable sources](#directory-and-editable-sources).
  Tested with uv 0.13: an editable path dependency
  installs only `mylib.pth` (the checkout's path) and `mylib-1.0.0.dist-info/`
  with the usual metadata plus `direct_url.json`
  (`{"url": "file:///…/lib", "dir_info": {"editable": true}}`); `uv sync`
  matches that against the lock ("Checked 1 package") and reinstalled it
  once the `dist-info` was deleted. The source itself is never in
  `site-packages`, so replacing or removing an editable install only removes
  the pointer.
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

## The project's own collection

In a collection project (see
[collection-development.md](collection-development.md)), `sync` links it
into the tree rather than installing it (`link_editable`, 2026-10-21):

- **An absolute symlink** to the checkout at
  `ansible_collections/<namespace>/<name>`, as uv's editable installs record
  an absolute path, so the tree's own location doesn't matter.
- **Left as it is when it already points there**, and replaced, through a
  temporary link renamed over it, when it's missing or points elsewhere. A
  directory there is removed first. A namespace that's a link is replaced
  with a real directory, as for any install.
- **Its place is always alai's** (decided with the user, 2026-10-22), even
  with `collections_dir` pointed somewhere the user set up: whatever is at
  `<namespace>/<name>`, a copy, an installed release or another link, is
  replaced by the link to the checkout, as it's the project's own collection.
  Refusing a directory without a `MANIFEST.json`, as a hand-made copy might
  be, was considered and rejected: collections aren't meant to be edited in
  place, and keeping edits is `alai run`'s inexact sync's job for collections
  the lock doesn't list, not the project's own.
- **Never removed by an exact sync** (`plan(..., keep=...)`), though it's
  not in the lock's index entries. `--only-group` leaves it out, as uv's
  `--only-group` leaves out the project.
- **Reported as `Linked`**, with ` + ns.col==1.0.0 (from <checkout>)`, as
  uv shows an editable install's source.

## Directory and editable sources

Implemented on 2026-10-22 for `[project.sources]` path entries
([lockfile.md](lockfile.md#other-sources)) and `alai collection install
<dir>` / `-e <dir>` (`_build.py`, `_install.py`, `install_locked` in
`_sync.py`).

### Copying a directory, as its artifact would be

**A directory is installed as its collection would be once built and
installed** (decided with the user, 2026-10-22): the same result as
`ansible-galaxy collection build` on the directory, then
`ansible-galaxy collection install` of that tarball, made straight into
place without the tarball (`_build.build`). That's how a collection
normally reaches anyone, so a directory installed by alai is what users of
the published collection get, and needs nothing outside it. uv does the
same in its way, building a wheel from a directory and installing that.

ansible-galaxy can also install a directory directly, `collection install
<dir>` (`install_src` in ansible-core `616e7c05e0`,
`lib/ansible/galaxy/collection/__init__.py`), with the build's file
selection, `MANIFEST.json` and `FILES.json`, but not its handling of
symlinks: it copies them as they are. That shortcut isn't what alai
matches. The one place the result differs:

| Symlink | Built and installed (and alai) | `collection install <dir>` |
|---|---|---|
| To something inside the collection | A relative link to it | Copied as it is, target text unchanged |
| To a file outside the collection | Replaced by that file | Copied as it is: a relative one then leads somewhere else, or nowhere |
| To nothing | An error | Copied |
| To a directory outside the collection | Skipped, with a warning | Skipped, with a warning |

**Following ansible-galaxy is where this starts, not a requirement**
(decided with the user, 2026-10-22). `_build` is meant to be shared with a
future `alai build` ([collection-development.md](collection-development.md#open-questions)),
so a directory installed by alai and the artifact `alai build` makes come
from one file selection and one `MANIFEST.json` and `FILES.json`. Once
`alai build` is designed, either may move away from ansible-galaxy, and the
user docs say the copy may change.

What it does:

- **The files**: with `build_ignore`, `_build_files_manifest_walk`'s
  defaults (`MANIFEST.json`, `FILES.json`, `galaxy.yml`, `galaxy.yaml`,
  `.git`, `*.pyc`, `*.retry`, `tests/output`, `<ns>-<name>-*.tar.gz`, and
  the directories `CVS`, `.bzr`, `.hg`, `.git`, `.svn`, `__pycache__`,
  `.tox`) plus its patterns, matched with `fnmatch` on the relative path;
  a directory link inside the collection listed but not walked, one leading
  outside skipped with a warning. With `manifest`, the same default
  directives and distlib's `Manifest`, as `_build_files_manifest_distlib`
  uses it, which needs distlib (added as a dependency, 2026-10-22, imported
  only then). The plugin directories its defaults name come from
  ansible-core's loaders and `DOCUMENTABLE_PLUGINS`, worked out once and
  written down (`_DOCUMENTABLE`). The two keys together are an error, as in
  ansible-galaxy.
- **`MANIFEST.json` and `FILES.json`** as `_build_manifest` writes them:
  `galaxy.yml` normalised as `_normalize_galaxy_yml_manifest` does (lists
  for list keys, `{}` for `dependencies`, None for the rest),
  `json.dumps(..., indent=True)`, and `FILES.json`'s sha256 in
  `MANIFEST.json`. A missing mandatory key (`readme`, `authors`) is an
  error, as ansible-galaxy refuses to build without them; reading the
  project only needs the name and version.
- **Modes**: files `0644`, or `0755` if executable by the owner (for a link
  replaced by its file, the file's), directories `0755`, masked by the
  umask as extraction is.
- **Symlinks** as the table says. Where a link leads is judged from the
  source's real path, where ansible-galaxy compares it with the path as
  given, so a source reached through a symlink isn't taken as all outside.
- **Checked against the lock**: `galaxy.yml` must still name the locked
  collection and version, or it's an error asking for `alai lock`, which
  only `--frozen` can reach.

Checked on 2026-10-22 against ansible-core 2.21.5 (PyPI, Python 3.14),
installing with `ansible-galaxy` and with `_build.build` and comparing
paths, types, modes, link targets, `FILES.json` and `MANIFEST.json` (but
for its time-free checksum of `FILES.json`):

- `collection build`, then `collection install` of the tarball, for a
  source with links inside the collection (relative, absolute and to a
  directory) and outside it (relative and absolute, one to an executable):
  the same. A link that climbed out of the collection by mistake failed in
  both, with the same path.
- `collection install <dir>`, for one source using `manifest` directives
  and one using `build_ignore`, with an executable, `.git` and `docs/`: the
  same files and manifests, which the build shares (this was before the
  symlinks followed the build).

Kept as ansible-galaxy has them: with `manifest`, `FILES.json` lists `.`
twice, as distlib returns the collection's own directory, and a symlinked
file isn't included at all, as distlib only collects regular files. 2.21.5
lists `FILES.json` in directory order; devel sorts it by name, which alai
does.

### When a copy is made again

As `uv sync` decides, with uv's three checks (tried with uv 0.13.0,
2026-10-22):

1. **The version, through the lock.** `sync` re-reads the directory's
   `galaxy.yml`; a new version makes the lock stale, so it locks again, and
   the installed `MANIFEST.json` no longer matches.
2. **Where it came from, through the record.** uv's `direct_url.json`
   (`{"url": "file:///…/lib", "dir_info": {"editable": false}}`) is
   compared with the lock's source. Here a copy from a directory has a
   record, `ansible_collections/.alai/<ns>.<name>.json`, holding `{"source":
   {"directory": "<absolute path>"}, ...}`; an index's copy has none. So a
   collection whose source changes, from an index to a directory, from one
   directory to another, or back, is replaced, even at the same version.
3. **A cache key.** uv's `uv_cache.json` records `pyproject.toml`'s
   modification time (its default `cache-keys`, plus the `src` directory's
   own for uv_build); touching `pyproject.toml`, or changing only its
   `description`, made `uv sync` reinstall (`~ lib==0.3.0`) at the same
   version, while editing a file under `src/lib/` didn't. uv's docs
   (`concepts/cache.md`) call this default a heuristic that "may lead to
   fewer re-installs than desired". Here the record holds the sha256 of
   `galaxy.yml` and `meta/runtime.yml` (`cache_keys`, decided 2026-10-22),
   not their modification times. Hashing two small files costs no more than
   `stat`ing them, and a hash neither misses an edit that keeps the time
   (`cp -p`, two edits within the timestamp's resolution) nor copies again
   for a `touch` or a checkout that rewrote the file unchanged. No reason
   for uv's choice of times was found; for its globs over whole trees,
   reading every file would cost more. `meta/runtime.yml` is a key too: a
   change to its `requires_ansible` makes the lock stale but leaves the
   version, so without it the copy kept the old file, and it also holds the
   plugin routing Ansible reads. Editing any other file needs
   `--reinstall-collection`, or an editable source.

   **No `cache-keys` setting** (decided 2026-10-22). uv needs one because
   building a package can read anything (setuptools-scm's version from
   git, `requirements.txt` through dynamic metadata), and only the
   package's author knows what, so it's in the package's own
   `pyproject.toml`. A collection has no build: its metadata is those two
   files, and the copy is the files `build_ignore` or `manifest` select, so
   they're every input. Seeing edits is what an editable source is for. If
   users ask, the shape would be uv's, in the source collection's own
   `alai.toml`; see [user-questions.md](user-questions.md#editing-a-copied-directory).

`collection install <dir>` copies again every time, as `uv pip install
./lib` rebuilt and reinstalled (`~ lib==0.3.0`) on every run, including
with nothing changed.

The record is beside the collections, not in one: ansible-galaxy writes no
`.info` for a directory install, and a file inside the collection would be
one `FILES.json` doesn't list. `.alai` starts with a dot, so neither
Ansible nor `installed()` reads it as a namespace. It's written when the
copy is renamed into place and removed with it (`write_record`), and when
an editable link replaces it. A git source will record its URL and commit
the same way.

### A built directory

Implemented on 2026-10-22. Which file a directory has decides what it is,
rather than one file taking precedence over the other
(`read_collection_dir` in `_galaxy.py`, `install_dir` in `_build.py`):

| The directory has | It's | Metadata from | Installed by |
|---|---|---|---|
| `galaxy.yml` | A collection's source | `galaxy.yml` | Building it ([above](#copying-a-directory-as-its-artifact-would-be)) |
| `MANIFEST.json` | A built or installed collection | `MANIFEST.json`'s `collection_info` | Copying it as it is |
| Both | Unclear | An error, saying which to remove | |
| A tarball (planned) | A built artifact | The archive's `MANIFEST.json` | Extracting it, as an index's |

`meta/runtime.yml` gives `requires_ansible` for all of them, as
`MANIFEST.json` doesn't carry it.

- **Both is an error**, as ansible-galaxy makes it (`from_requirement_dict`
  in `galaxy/dependency_resolution/dataclasses.py`, ansible-core
  `616e7c05e0`). Either choice is wrong half the time: a `MANIFEST.json`
  left in a source is usually from an old build, and a `galaxy.yml` in a
  built collection isn't what was built.
- **`MANIFEST.json` is the truth for a built collection.** `copy_built`
  copies what its `FILES.json` lists, checking each file's sha256, and
  `FILES.json`'s own sha256 against the one `MANIFEST.json` records, and
  keeps both files byte for byte, as extracting an artifact does. Symlinks
  are made as for a build. ansible-galaxy instead walks such a directory
  with no ignores and writes both files again (`install_src`, which sets
  `build_ignore = []` when there's no `galaxy.yml`), which lets in files
  the build didn't have and passes off edited ones under the original
  checksums. A `MANIFEST.json` without a `FILES.json`, as one made by hand
  might be, a file that doesn't match, or one listed that's missing, is an
  error; such a directory still works editable, which reads only its
  metadata.
- **The checks follow the file**: the lock's `requirements` snapshot comes
  from `MANIFEST.json`'s dependencies, `lock --check` names the file
  (`MANIFEST.json dependencies: added d.d`), and the install record's
  `cache_keys` hashes `galaxy.yml`, `MANIFEST.json` and
  `meta/runtime.yml`, whichever exist. `MANIFEST.json` holds `FILES.json`'s
  checksum, so a new build changes it.
- **A collection project's root is still only a `galaxy.yml`**: an
  installed collection's `MANIFEST.json` is never a project marker
  ([collection-development.md](collection-development.md#what-todays-code-must-keep-possible)).

A git checkout goes through the same reader, so a repository that commits
a built collection works without more code. A tarball needs neither the
snapshot's re-reading nor a cache key: its `cksum` pins it.

### Editable

An editable source is linked as the project's own collection is
(`link_editable`, [The project's own collection](#the-projects-own-collection)):
an absolute symlink to the directory, whose target is its record, left
alone while it points there. Nothing is built: Ansible sees the directory
as it is, without a `MANIFEST.json` or `FILES.json`, with what
`build_ignore` would leave out, and with its symlinks as they are, as uv's
editable installs point at the source rather than a built copy. `alai run` and `alai env` set
`PYDEVD_RESOLVE_SYMLINKS` and add each one to a module debugger's
`source_mapping` (`editable_links` in `_environ.py`), as for the project's
own, so breakpoints set in the directory hit. They're reported with the
project's own under `Linked`, ` + ns.col==1.0.0 (from <dir>)`. A copy
from a directory is reported under `Installed` the same way, as uv shows
both sources.

A symlink's version is still read through it, as before, but nothing
alai records is ever kept for one, so a copy never counts as installed by a
link to a directory, which has no `MANIFEST.json`.

### Where the directory may be

Not inside the collections directory, and not holding it
(`read_path_source` in `_project.py`), as the overlap check does for a
collection project's checkout ([configuration.md](configuration.md)):
linking or copying into the place where the directory itself is would
replace it, and an exact sync could remove it. A directory holding the
collections directory would also copy it.

## Not done yet

- **`--no-editable`** on `sync`, as uv's, to install editable sources as
  copies, for building an image say.
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
