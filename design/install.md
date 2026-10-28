# Installing

How `alai sync` gets each locked collection onto disk: the artifact cache
is `src/alai/_artifacts.py`, installing into `collections_dir` is
`src/alai/_install.py`, and the command is `src/alai/_commands/sync.py`
with its logic in `src/alai/_sync.py`. The user documentation is
`docs/managing-collections.md#installing`.

## Decisions

- **Artifacts are fetched by the lock's `sha256`, from the index's `dl`
  template** (`artifact_url`), streamed to a file and hashed as they arrive
  (`Fetcher.download`). A download stops once it passes the lock's `size`,
  so a wrong or hostile response can't fill the disk, and the SHA-256 and
  size must both match the lock, as the index spec requires ("MUST verify
  the sha256 of every download against `sha256`"). Transport compression is
  undone first, so the hash is over the file itself.
- **The cache holds extracted collections, keyed by `sha256` alone**:
  `<cache_dir>/collections-v1/<sha256>/`, as the spec's client rules
  suggest, so one artifact from two indexes is stored once and moving an
  index invalidates nothing. The tarball is downloaded into a temporary
  directory beside it and deleted once extracted, as uv keeps unpacked
  wheels rather than the archives. The extracted directory is renamed into
  place, so it exists only when complete; if another alai renamed the same
  artifact in first, theirs is used. `-v1` changes if what's extracted, or
  how, ever does, rather than reusing a layout older alai wrote. Nothing
  is fsynced before the rename (decided with the user, 2026-10-23): on a
  power loss the cache can keep a renamed-in entry whose files are
  truncated, trusted from then on, as uv's cache can; an fsync of every
  extracted file would cost more than extraction on a real disk, and the
  remedy is deleting the entry, or the cache, by hand until a cache
  command exists.
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
  - The last member of a repeated name is the only one read and
    extracted, as ansible-galaxy's `getmember` finds it: some published
    collections replace a file, `MANIFEST.json` included, by appending it
    with `tar -r`, which leaves the original in place before it. A
    replaced `FILES.json` decides what's extracted, so a member it lists
    that the first didn't is extracted, and one it drops isn't. A member
    of a name written earlier isn't replaced in place, as a file written
    through an earlier symlink of the replaced name would stay where that
    led: only the last member of each name is ever extracted.
  - `FILES.json` listing a name twice is the same entry if both agree, and
    refused if they don't, as one of them can't match. Listing
    `MANIFEST.json` or `FILES.json` is refused: a build never does
    (ansible-core 2.22.0rc1's `_build_files_manifest_distlib` excludes both,
    as does its directory walk), and neither could be listed with a checksum
    that matches, as `FILES.json` would hold its own, or `MANIFEST.json`'s,
    which holds `FILES.json`'s. `install_artifact` would extract one again
    and fail on the checksum. A name that isn't UTF-8, which JSON can spell
    as a lone surrogate, makes `FILES.json` invalid JSON to `serde_json`,
    which parses it, so it's refused as that.
  - A member tar readers don't agree on makes the whole tarball corrupt
    (decided with the user, 2026-10-23), listed or not, so alai sees the
    members ansible-galaxy would. Two kinds, tested with Python 3.15's
    `tarfile`, GNU tar and the `tar` crate on 2026-10-23: a name ending in
    `/` that isn't a directory (`tarfile` reads an old-style V7 regular
    file, type `'\0'`, named `d/` as the directory `d`, but not one of type
    `'0'`, nor one after a GNU long name, and it drops the `/` from a pax
    name of any type), and a directory, link or device whose header
    declares content (`tarfile` and GNU tar read what follows as the next
    member, while the `tar` crate skips it, so a member could be shown to
    one and hidden from the other). So is a pax global header that sets
    anything but `comment`, whose keys `tarfile` applies to every later
    member and the `tar` crate ignores; one of only `comment`, as
    `git archive` writes, is allowed ([performance.md](performance.md#the-native-extension)).
    A build never makes any of them, as ansible-galaxy writes directories
    as type `'5'` with no content, and no global header. Refused
    rather than matched to `tarfile`, whose reading changes between Python
    versions; relax it if a published collection turns out to need it.
  - Only gzip: a collection build is always gzipped, and URL and local
    tarball sources are refused unless they start with gzip's magic bytes.
  - Files are `0644`, or `0755` when the tarball marks them executable for
    the owner; directories are `0755`. ansible-galaxy sets those modes
    exactly; here they're masked by the process umask, as the kernel
    applies it to each `open` and `mkdir` and as the cache note in
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
  with `realpath` before writing, and every link is resolved again once all
  exist, since a later link can change where an earlier one leads. A parent
  that must be made is resolved before it's made as well, unless its own
  parent is already known to be plain: making `s/sub` for `s/sub/x` would
  otherwise create `sub` beside the collection before the check refused
  it. A member named `./s` is a different name from `s`, so it can't
  replace an escaping `s`, and `FILES.json` can't list it. Device
  files, FIFOs and anything but files, directories, hardlinks and symlinks
  are refused.
  - Where alai and ansible-galaxy differ on links, tested on 2026-10-23
    with ansible-galaxy 2.21.5: a symlink or hardlink to a member
    `FILES.json` doesn't list is refused, where ansible-galaxy hashes or
    copies that member from the tarball (installing the symlink dangling,
    as its target isn't extracted); a symlink through another, such as
    `f -> d/t` with `d -> sub`, is read on disk and allowed, where
    ansible-galaxy looks for a member named `d/t` and fails; an absolute
    target is refused even inside the collection, which ansible-galaxy
    allows; and a hardlink to a member stored after it is copied (below).
    A build never makes any of these: it stores a symlink as a direct
    relative path to its real target, and a hardlink only to a file stored
    before it.
- **The cache is trusted after extraction**, as uv trusts its own. Tested
  with uv 0.13 and a scratch `UV_CACHE_DIR` on 2026-10-20: after editing a
  cached package's file, `uv sync`, `uv sync --reinstall` and
  `uv sync --reinstall-package` all installed the edited copy; only
  clearing the cache helps. The docs already say alai trusts its cache and
  that a `cache_dir` outside the home directory is the user's to restrict
  (see [index-client.md](index-client.md)).
- **Downloads run in parallel, extraction up to four at once**: up to the
  fetcher's 16 connections download at once, on a pool of their own that
  hands each tarball to the extraction pool, so a download never waits for
  an extraction. Up to four extract at once (`_common.MAX_THREADS`, or one
  per CPU if fewer), as the extension
  ([performance.md](performance.md#the-native-extension)) releases the GIL
  and more only contend for the disk. Every artifact is attempted; when
  several fail, the first by name is reported. The measurements are in
  [performance.md](performance.md#extraction).
- **Extraction reads the tarball twice**, front to back: once for its
  headers and its `MANIFEST.json` and `FILES.json`, checked before anything
  is written, then to write the last member of each listed name; how is in
  [performance.md](performance.md#the-native-extension). A symlink's
  checksum, and a hardlink's content, are read from disk once every member
  is written and every link checked again, as what it points to can come
  later; a hardlink is a copy of a listed file, never of an unlisted one or
  of anything outside. That's laxer than `tarfile` in one way, left so
  (decided with the user, 2026-10-23): a hardlink to a member stored after
  it is copied, where `tarfile`, and so ansible-galaxy, fails with
  "linkname not found", as tar means a hard link to an earlier member. It
  can't reach anything a listed file can't, and the copy is checked
  against its checksum; refusing it is a small change to `write` if
  matching ansible-galaxy matters. A parent directory with no
  symlink in its path is checked once (one that has a link is checked
  every time, as a later link could redirect it), with one `lstat` when
  its own parent is known to have none, and compared with where it would
  be under the resolved destination, as the cache's own path may go
  through a link (`~/.cache` on another disk, or macOS's `/var`), which
  until 2026-10-23 meant no parent was ever taken as plain.
- **A tarball may unpack to 512 MiB** (`MAX_UNPACKED_SIZE`), counting
  every byte decompressed, extracted or not (decided with the user,
  2026-10-22), and every byte a hard link's copy or a symlink's checksum
  reads back from disk (2026-10-23). A listed file can be any size, as its
  checksum is the artifact's own to give, so without a cap a tarball of a
  few megabytes, well under the 1 GiB download cap, could unpack to enough
  to fill the disk, and unlisted padding could take hours to read through.
  A link costs the tarball a 512-byte header, so without counting what it
  copies or reads, a tarball under the cap could write the same large file
  thousands of times over, or read it for hours: measured before the
  change, an 8.7 KB tarball of one 4 MiB file and 200 hard links to it
  wrote 804 MiB under an 8 MiB limit. ansible-galaxy has no cap, and
  copies a hard link and reads a symlink's target the same way. 512 MiB is about 20 times the largest of
  sivel.acd's 97 collections (cisco.dnac 6.31.3, 25 MB), and a power of
  two like the download cap; the user suggested 500 MB. It costs nothing
  measurable (97 collections: 1.78 s on one thread, 0.44 s on six).
  `ALAI_MAX_UNPACKED_SIZE`, in bytes, raises it, or `0` lifts it, as an
  escape hatch if a genuine collection ever passes it; read when the
  cache is made, so a bad value fails before anything downloads. An
  environment variable only, with no `alai.toml` key for now. uv 0.13.0
  has no such limit; its nearest escape hatch is
  `UV_INSECURE_NO_ZIP_VALIDATION`.
- **What a hostile artifact can't do is tested** in
  `tests/test_extract_safety.py`, with a file
  beside and above the collection that must stay untouched: appended
  members in every order, unlisted members and symlinks, paths and links
  leading out by name or through chains on disk, circular and dangling
  links, hardlinks, special files, and setuid, setgid and sticky modes,
  which are dropped. `tarfile` itself crashed on a dangling link
  (`KeyError`) or a circular one (`RecursionError`) listed as a file,
  which reading from disk replaced.

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
  record file mapping names to `sha256` was considered, to tell two
  artifacts of one version apart and to know which directories alai made,
  and dropped: uv does without one, and installing custom content is what
  path and git sources are for. A `uv pip install`-style command,
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
  nothing from the C library but the `ioctl` call. The number is
  `_IOW(0x94, 9, int)`, which architectures encode differently
  (`0x40049409` on most, `0x80049409` on PowerPC, MIPS and SPARC, whose
  direction field is wider), so it's the `libc` crate's `FICLONE`, encoded
  for the target, rather than a constant (until 2026-10-23 it was
  `0x40049409`, so a clone there would have failed and `auto` hardlinked).
  Alpha and PA-RISC, the other two, have no Rust standard library for
  Linux. Anything else fails with
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
  cached; with nothing to fetch, the fetcher isn't imported at all. A sync with nothing to do took 0.02 s for the whole process.
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
  still read as for any command: it marks the project, and its settings
  (the indexes, `collections_dir`, `link_mode`) apply, as uv's `pip`
  commands still read `uv.toml` (a `uv.toml` index URL was used by `uv pip
  install`). A failure names the requirement as `(from the command line)`.
- **Uninstall removes only the collections named**; what they depend on
  stays, as uv left `idna`, `certifi` and the rest after `uv pip uninstall
  requests`. One that isn't installed is a warning, and with none left it's
  "no collections to uninstall", exit 0, as uv's "Skipping six as it is not
  installed" and "No packages to uninstall".
- **Where: the project's collections directory, or an explicit one.** uv
  won't install without an environment ("No virtual environment found;
  run `uv venv` … or pass `--system`"). Here that's a project's
  `alai.toml`, even an empty one. Without one, `collections_dir` has to be
  set (`--collections-dir`, `ALAI_COLLECTIONS_DIR`, a config file), or it's
  an error, rather than a `collections/` in whatever directory alai runs
  from. Until 2026-10-27 an `ansible.cfg` or an existing
  `collections/ansible_collections/` counted as a project too (see
  [configuration.md](configuration.md#decisions)); now the error's hint
  names the nearest `ansible.cfg` or `galaxy.yml` instead. uv 0.13.5's
  `uv pip` walks up to the nearest `.venv` whatever the project, as it used
  one a directory above a nested `pyproject.toml` without one, and fails
  with "No virtual environment found" rather than create one. Finding an
  existing `collections/ansible_collections/` the same way is left as a
  question ([user-questions.md](user-questions.md#finding-a-collections-directory-without-a-project)):
  next to `alai.toml` it needs a precedence rule, since `collections_dir`
  can be anywhere, and there's no `uv venv` to create one.
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
  (above). The extracted cache is checked against the lock's `sha256` when
  it's made and trusted after, as uv trusts its own, so nothing is
  re-extracted. uv's summary says `Prepared 5 packages` even then, since
  `--reinstall` implies its `--refresh`; here `Prepared` counts downloads,
  and the index files are revalidated every run anyway
  ([index-client.md](index-client.md)), so a reinstall from the cache has
  no `Prepared` line.
- **An edit in place under `hardlink` or `symlink` survives a reinstall**,
  as it's in the cache too, so the docs say so wherever they offer
  `--reinstall-collection` for an edited collection, and point at
  `link_mode`'s warning: delete `<cache_dir>/collections-v1/`, then
  reinstall (2026-10-26). That matters by default, as `auto` hardlinks on
  filesystems without copy-on-write, such as ext4. uv does the same: tried
  with uv 0.13.5 and `UV_LINK_MODE=hardlink`, an installed `six.py` edited
  in place was edited in uv's cache too (`archive-v0/<id>/six.py`, the same
  inode), and stayed edited through `uv sync --reinstall` and
  `uv sync --reinstall --refresh`; only `uv cache clean six` and a
  reinstall gave a clean copy. Checking the cache against `FILES.json` on a
  reinstall was considered and left out, to match uv; the fix to come is
  `alai cache clean` (Not done yet).
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
an editable link replaces it. A git source records its URL, commit and
`subdirectory` the same way (`git_record` in `_sync.py`), so another commit
at the same version is installed in its place.

### A built directory

Implemented on 2026-10-22. Which file a directory has decides what it is,
rather than one file taking precedence over the other
(`read_collection_dir` in `_galaxy.py`, `install_dir` in `_build.py`):

| The directory has | It's | Metadata from | Installed by |
|---|---|---|---|
| `galaxy.yml` | A collection's source | `galaxy.yml` | Building it ([above](#copying-a-directory-as-its-artifact-would-be)) |
| `MANIFEST.json` | A built or installed collection | `MANIFEST.json`'s `collection_info` | Copying it as it is |
| Both | Unclear | An error, saying which to remove | |
| A tarball | A built artifact | The archive's `MANIFEST.json` | Extracting it, as an index's ([Tarball sources](#tarball-sources)) |

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
- **The checks follow the file**: the lock's `collections` snapshot comes
  from `MANIFEST.json`'s dependencies, `lock --check` names the file
  (`MANIFEST.json dependencies: added d.d`), and the install record's
  `cache_keys` hashes `galaxy.yml`, `MANIFEST.json` and
  `meta/runtime.yml`, whichever exist. `MANIFEST.json` holds `FILES.json`'s
  checksum, so a new build changes it.
- **A collection project still needs a `galaxy.yml`** beside its
  `alai.toml`: an installed collection's `MANIFEST.json` never makes one
  ([collection-development.md](collection-development.md#what-todays-code-must-keep-possible)).

A git checkout goes through the same reader, so a repository that commits
a built collection works without more code. A tarball needs no cache key:
its `cksum` pins it.

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

## Tarball sources

Implemented on 2026-10-22 for `[project.sources]` `{ path = "<file>" }`
and `{ url = "<url>" }` ([lockfile.md](lockfile.md#tarballs)), and
`alai collection install <file>.tar.gz` and `<url>`.

- **Installed as an index's artifact is**: extracted into
  `collections-v1/<cksum>/` with the same checks
  ([Decisions](#decisions)), then linked into place. Locking extracts it
  (`ArtifactCache.add` for a file, `ArtifactCache.download` for a URL),
  since its metadata is inside, so `sync` usually finds it cached. When it
  isn't, as with `--frozen` on a fresh cache, it's fetched from its URL,
  or its file as a `file://` URL, and checked against `sha256` and `size`
  like any download.
- **URLs are downloaded in parallel while locking**, up to the fetcher's
  16 connections, as `sync` downloads artifacts, through one
  `ArtifactCache`, which limits how many extract at once. The
  results are taken in order by name, so when several fail, the first by
  name is reported (`read_archives`).
- **A URL with no size known** stops at 1 GiB (`MAX_ARCHIVE_SIZE`), for the
  same reason the lock's `size` stops an index's download: a wrong or
  hostile response can't fill the disk. One that doesn't start as a gzip
  file stops at its first chunk ([lockfile.md](lockfile.md#tarballs)).
- **It carries an install record**, `{"source": {"path": "<absolute>"},
  "cksum": "…"}` or `{"source": {"url": "…"}, "cksum": "…"}`, as uv's
  `direct_url.json` records an archive's URL. So a collection that moves
  between an index and a tarball, or to a different tarball of the same
  version (after `-P`), is replaced, as uv's sync replaced the rebuilt
  archive (`~ lib==0.1.0`).
- **`collection install` leaves the same tarball installed**: tried with uv
  0.13.0, a second `uv pip install ./lib-0.1.0.tar.gz`, or of the same URL,
  said `Checked 1 package`. Here the record matches, so it's `Checked`; a
  different tarball at the same path replaces it. A directory is copied
  every time instead, as uv rebuilt one. A URL is still downloaded each
  time, to learn which collection it holds.
- **Reported as `+ ns.col==1.0.0 (from <path or URL>)`**, as uv shows an
  archive's source.

## Git sources

Implemented on 2026-10-22 (`_git.py`); the manifest and lock shapes are in
[lockfile.md](lockfile.md#git-repositories). `alai add` and `alai
collection install` take `git+<url>[@<rev>][#subdirectory=<dir>]`, as
`uv add` and `uv pip install` do (`parse_git` in `_manifest.py`); `uv add`
wrote `{ git, subdirectory, rev }` in that order, and so does `alai add`.

**A git source is a URL** (decided with the user, 2026-10-22; `_git_url`
in `_manifest.py`), as uv requires: git's scp-like `git@github.com:org/repo.git`,
which GitHub offers to clone over ssh, is refused with the `ssh://` URL to
write instead, and a bare path with a pointer to `file://`. uv 0.13.0
refused the scp form in `[tool.uv.sources]` ("relative URL without a
base") and on the command line (`git+git@github.com:…` failed on "a
supported file extension"), and took `git+ssh://git@github.com/…` in both.
alai had taken it in `alai.toml`, passed to git, but not on the command
line. The scp path is relative to the user's home on the server, so its
exact URL is `ssh://host/~/path`; the suggestion is `ssh://host/path`,
what GitHub, GitLab and the like document, with `/~/` given beside it for
a server of one's own. A scheme with its slashes mistyped, `https:/host`,
isn't read as an scp host named `https`. One form means one way to match a
source to a URL (connection sharing, `alai remove`'s pointer), and the
docs' `insteadOf` example rewrites `ssh://git@github.com/`, as one written
for `git@github.com:` doesn't match an `ssh://` URL (checked with `git
ls-remote`).

**`version` goes after the `#` too, joined with `&`** (decided with the
user, 2026-10-22): `#subdirectory=chocolatey&version=1.5.0`, written to
`[project.sources]` after `rev`, so a collection with a placeholder version
can be added without editing `alai.toml` or adding a flag. pip and uv join
the keys after the `#` with `&`: uv 0.13.0 took
`#subdirectory=.&egg=markupsafe` in either order, and read
`#subdirectory=.,version=1` as the subdirectory `.,version=1`. A comma
would also read as ansible-galaxy's `#/<dir>,<rev>`, where what follows it
is the git revision, not the collection's version. `@<rev>` stays, as
every tool writes it. uv ignores a key it doesn't know (`#foo=bar` was
accepted and dropped); alai refuses one, so `subdirectroy=` isn't lost
without a word.

alai runs the `git` CLI, as uv and ansible-galaxy do, so a user's SSH
config, agent, `known_hosts`, credential helpers and `insteadOf` rewrites
work as they do for `git` itself, which a library such as dulwich or
pygit2 wouldn't give.

Tried with uv 0.13.0 on 2026-10-22, with a `git` on `PATH` that logged its
arguments and environment:

- **uv keeps a repository per URL and a checkout per commit**:
  `git-v1/db/<url hash>` is fetched into (`git init`, then `git fetch
  --force --update-head-ok <url> +<sha>:refs/commit/<sha>`), and
  `git-v1/checkouts/<url hash>/<short sha>` is made from it with `git clone
  --local`, `git reset --hard <sha>` and `git submodule update --init
  --recursive`, then marked done with a `<short sha>.ok` file. The fetch
  isn't shallow: markupsafe's repository held all 795 commits behind
  `3.0.2`, back to 2010, and no `shallow` file, though only that commit's
  history, not other branches. A file lock
  per URL, `git-v1/locks/<url hash>`, guards the shared repository.
- **`GIT_TERMINAL_PROMPT=0` on the fetch**, even with a terminal, so git
  fails rather than asking for an HTTPS username or password; credential
  helpers and credentials in the URL still work. `GIT_LFS_SKIP_SMUDGE=1`
  on the checkout.
- **Nothing for SSH**: no `GIT_SSH_COMMAND` or `BatchMode`, so ssh's own
  prompts, for a passphrase or an unknown host key, reach the terminal.
  With no agent, the fetch failed at once (`Permission denied
  (publickey)`), shown as git's stderr under "process didn't exit
  successfully: `/usr/bin/git fetch …`".

ansible-galaxy (ansible-core 2.22.0rc1, `_extract_collection_from_git`)
runs `git clone`, shallow only for `HEAD`, into a temporary directory, then
`git checkout <rev>`, with no environment of its own.

How alai does it, differing from uv in keeping no shared repository:

1. **Resolve `rev`** (`resolve`) with `git ls-remote <url> <rev>
   <rev>^{}`: an annotated tag's commit is only listed when its `^{}` is
   asked for. A tag wins over a branch of the same name, as git's own
   lookup order has it. A full hash is taken as it is, with no network; an
   abbreviated one can't be found without the history, so it's an error
   saying to give the full hash. With no `rev`, `HEAD`.
2. **Use the cache** if `git-v1/<url hash>/<commit>` exists.
3. **Otherwise fetch into a new temporary directory** beside it (`fetch`):
   `git init`, `git fetch --depth=1 <url> <commit>`, check out, update
   submodules when there's a `.gitmodules`, remove every `.git`, then
   rename it into place, as an extracted artifact is
   ([Decisions](#decisions)): if another alai renamed the same commit
   first, ours is removed. Commits are fetched in parallel, eight at a
   time, each once (`fetch_all`). Then the collection, at `subdirectory` if
   given, goes through the directory reader and is copied
   ([A built directory](#a-built-directory)), with its install record.

When locking, a git source locked from the same `[project.sources]` entry
is taken from the lock with no network, as a URL tarball is; one whose
`version` changed keeps its commit, read again from the cache; `-U` or
`-P` resolves `rev` again (`read_git_sources`). `--offline` works from the
lock and the cache, and is an error when a commit isn't cached or a `rev`
needs resolving.

A shallow fetch of a commit that's no branch's tip worked against
github.com (chocolatey-ansible's `1.3.1` tag, `d3144a8`: 1.3 s, a 520K
`.git`). Keeping no shared repository needs no lock, as git's own
`index.lock` makes a second fetch into one repository fail rather than
wait. What it gives up is uv's incremental fetch, small at depth 1, and
servers that won't serve a commit by its hash unless it's a branch or
tag's tip, which git's first protocol allows them to refuse (`Server does
not allow request for unadvertised object`; tested with `protocol.version=0`
and `uploadpack.allowAnySHA1InWant=false` on a local repository): on that
message alone, every branch and tag is fetched instead, and a commit none
of them leads to is an error.

**The cache keeps the tree, not `.git`** (decided with the user,
2026-10-22). uv's checkout keeps its `.git` (104K for markupsafe, objects
shared with the 1.1M full-history repository by `clone --local`), as a
Python build can read it: setuptools-scm takes the version from `git
describe`. Nothing in a collection's build reads it, and the copy leaves
`.git` out ([Copying a directory](#copying-a-directory-as-its-artifact-would-be)).
Kept, it would let someone deepen a cached checkout (`git fetch
--unshallow <url> <sha>` worked on a depth-1 one, 1 commit to 155), but a
commit's directory shouldn't change once it's in place; a clone of one's
own as a `path` source is the way to work with the history.

**Running fetches in parallel**: separate repositories don't share
anything, and `subprocess` is safe from threads. uv 0.13.0 runs them in
parallel: locking three github.com sources, the fetches of two overlapped
(the logging `git` above, with times). What parallel fetches
add is ssh prompts at the same time, which interleave on the terminal and
race to write `known_hosts`, and prompts under the progress display, which
can't capture them as ssh writes to the terminal itself.

**Fetches run in parallel, with ssh left alone** (decided with the user,
2026-10-22), as uv does. Not chosen:

- **One repository at a time, or one host at a time for its first
  connection**, which would avoid prompts colliding but slow every lock
  for what happens once per host or per agent session.
- **ssh's `BatchMode=yes`**, which disables every prompt, passphrase,
  password and host key alike: with an empty `known_hosts`, `ssh -o
  BatchMode=yes git@github.com` failed with "Host key verification
  failed." An agent and keys without a passphrase still work. alai would
  fail where `git clone` and uv work.

What makes up for it:

- **The progress display stops redrawing while a fetch runs** with a
  terminal (`_progress.Suspended`, around each git command that reaches
  the network), so a prompt isn't drawn over; git's stderr is captured and
  shown on failure, as uv does. uv doesn't stop: run in a pseudo-terminal
  with an empty `known_hosts`, its spinner went on redrawing two lines
  every frame and erased ssh's "Are you sure you want to continue
  connecting (yes/no/[fingerprint])?" as it was asked.
- **`Updating <collection> from <url> (<rev>)` and `Updated …
  (<commit>)`**, uv's lines for a git fetch with the collections it's for
  added (asked for 2026-10-22), printed before alai first reaches a
  repository and once its commit is cached (`_git.update`), so a paused
  display still says what alai is waiting on: a fetch that's slow, or
  waiting on a question, doesn't look like a hang, and an agent's request
  to use a key comes with the collection it's for on the line above.
  Several collections from one commit are named together; a repository
  given to `alai collection install` has no name until it's read, so its
  line has only the URL. An agent can't be given that context itself: its
  protocol carries the key and what to sign, and since OpenSSH 8.9 ssh
  adds the host the key is for (`session-bind@openssh.com`), but there's
  no field for a reason. A password manager names what's asking from the
  process that connected to the agent, which a forwarded agent can't see
  past the ssh session it's forwarded over. They stay on screen, as uv's do, so nothing
  has to be cleared from under a question ssh wrote below them; a status
  line that cleared itself would need to know how many lines ssh wrote.
  `-q` hides them. With nothing to fetch, nothing is printed.
- **A hint on ssh's failures** (`_hint`): when git's stderr has `Host key
  verification failed`, the error says to trust the host first, with `ssh
  -T` and the fingerprint check; for `Permission denied (publickey)`, to
  load a key with `ssh-add`; for an HTTPS username git wasn't allowed to
  ask for, to set up a credential helper. Without a terminal, as in CI,
  ssh can't prompt and fails with the first (`setsid ssh … </dev/null`,
  OpenSSH 10.2), so the hint is what a CI log shows.
- **One ssh connection per host** (asked for 2026-10-22; `_Sharing` in
  `_git.py`): each git command that reaches a repository over ssh is its
  own connection, and each authenticates, so with an agent that confirms
  each use of a key, resolving a revision and fetching its commit asked
  twice per repository, plus once per submodule. alai's
  `GIT_SSH_COMMAND` adds `-o ControlMaster=auto -o ControlPath=<dir>/%C
  -o ControlPersist=60`, so every command to a host shares the first
  one's connection. Checked on 2026-10-22 with three `git ls-remote`s to
  github.com through a forwarded agent that confirms each use: three
  requests without sharing, one with it, and git's captured output didn't
  wait on the backgrounded connection. The first connection to a host
  goes alone (`turn`), the rest waiting for it, so commands run in
  parallel don't each make, and authenticate, one; to another host they
  don't wait. The sockets are in `/tmp/alai-ssh-*` rather than `TMPDIR`,
  as a socket's path is limited to 104 bytes on macOS, and `TMPDIR` there
  takes about 50 before `%C`'s 40; they're closed with `ssh -O exit` when
  alai exits, and outlive a killed alai by `ControlPersist`'s 60 seconds.
  Left alone: a user's own ssh command (`GIT_SSH_COMMAND`, `GIT_SSH`,
  `core.sshCommand`), and a host whose ssh config shares connections
  already (`ssh -G`, which prints the config without connecting, has
  `controlmaster` other than `false`), whose connection may be open and
  approved already, which alai's `-o` would otherwise override. uv
  doesn't share connections; each of its fetches is one connection.
- **The docs' git section has a `!!! warning`** that ssh may prompt, for
  a passphrase or a new host's key, and that prompts from parallel fetches
  can collide, followed by the workarounds below
  ([collection-sources.md](../docs/collection-sources.md#a-git-repository)).

The workarounds, checked with OpenSSH 10.2 and git 2.56 on 2026-10-22,
all things alai inherits from the environment rather than settings of its
own:

- **Add a new host's key first**, so there's no prompt: `ssh -T
  git@<host>` once and accept it, or `ssh-keyscan -t ed25519 <host> >>
  ~/.ssh/known_hosts`. Either way, compare the fingerprint (`ssh-keygen -lf
  <file>`) with the one the host publishes (GitHub's ED25519 key is
  `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`), as `ssh-keyscan`
  checks nothing.
- **Load the key into an agent**: `ssh-add`, or `AddKeysToAgent yes` in
  `~/.ssh/config` so its first use does it.
- **Accept a new host's key, still refusing a changed one**:
  `StrictHostKeyChecking accept-new`, in a `Host` block of `~/.ssh/config`
  or through `GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"`;
  it printed "Permanently added 'github.com' (ED25519) to the list of
  known hosts." It trusts whatever answers first.
- **Disable the check** for a throwaway environment: `-o
  StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`. Anyone in the
  middle can answer instead of the host, so the docs say so.
- **Fail rather than prompt**, for CI: `GIT_SSH_COMMAND="ssh -o
  BatchMode=yes"`, with `known_hosts` provided.
- **Set these for git alone, without a file**: git reads `core.sshCommand`
  and any other setting from `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>` and
  `GIT_CONFIG_VALUE_<n>` (git 2.31+); `core.sshCommand` set that way took
  effect.
- **Use HTTPS instead**: `url.https://github.com/.insteadOf
  git@github.com:` rewrites an SSH URL, so a lock written with one works
  where only HTTPS and a credential helper are set up; through
  `GIT_CONFIG_*` it made `git ls-remote git@github.com:…` work with no key.
  The lock keeps the URL as written.
- **HTTPS certificates**: git uses its own TLS (libcurl), so
  `http.sslCAInfo` or `GIT_SSL_CAINFO` for a private CA, and
  `GIT_SSL_NO_VERIFY=true` to disable the check. uv 0.13.0 sets
  `GIT_SSL_NO_VERIFY=true` on the fetch for a host given to
  `--allow-insecure-host`, and nothing for `--system-certs`, as git
  already uses the system's store; ansible-galaxy adds `-c
  http.sslVerify=false` for `--ignore-certs`. alai verifies with the
  system's store too ([index-client.md](index-client.md#decisions)), and
  `SSL_CERT_FILE` reaches git only through its own reading of it; if alai
  gets an insecure-host setting, it sets `GIT_SSL_NO_VERIFY` as uv does.

## Not done yet

- **Streaming each download into its extraction**, rather than writing
  its tarball first: it would trim the end of a cold sync, as extraction
  ends within 0.1 s of the last download. The extractor reads the tarball
  twice, its headers checked before anything is written, so it would need
  to keep the whole tarball in memory or extract in one pass, and it would
  extract bytes before their SHA-256 is checked, a change to decide on its
  own, though nothing's renamed into place before it matches. Moving the
  downloading and extracting into one call to the extension, without
  streaming, was tried and gained nothing
  ([index-client.md](index-client.md#decisions)).
- **`alai cache clean [NAME…]`, and the rest of uv's `cache` commands**
  (`prune`, `dir`), so an edited cached collection, or a cache that's grown,
  can be cleared without knowing its layout. Until then the docs say to
  delete `<cache_dir>/collections-v1/` (see Reinstalling). Design it from
  uv's first.

- **Relaying ssh's questions under the progress display**, so it never
  pauses: each git command gets a pseudo-terminal of alai's as its
  controlling terminal, stdout and stderr staying pipes, and what ssh
  writes there is drawn in a section under the spinner, one command at a
  time, the others' questions waiting their turn, with keystrokes passed
  to it in raw mode. Checked on 2026-10-22 with OpenSSH 10.2: ssh's
  host-key question arrived on the pseudo-terminal and was answered
  through it, and `ssh-keygen -y`'s passphrase question turned its echo
  off, so the pseudo-terminal's own line discipline hides a secret and
  handles backspace, with nothing for alai to mask. What it needs: setting
  the controlling terminal between fork and exec (`setsid`, `TIOCSCTTY`),
  which Python warns isn't safe with threads running, so a small helper
  process per command; restoring the user's terminal from raw mode on
  every exit, Ctrl-C included; stripping control sequences from what's
  relayed; passing on the terminal's size; and a question waiting its
  turn can outlast sshd's `LoginGraceTime` (120 s by default). Shelved
  for its own branch (decided with the user, 2026-10-22).
- **Reading a tarball source's metadata without extracting it.** Every
  command that loads the project reads its path sources (`read_local`),
  so `stale()` can compare a tarball's checksum with the lock's; hashing
  it is needed for that, but `read_path_source` then also extracts it into
  the artifact cache to read its `MANIFEST.json`, so a first `alai env` or
  `alai run --no-sync` on a machine extracts every tarball source. Reading
  `MANIFEST.json` from the tar stream, as ansible-galaxy's
  `_get_meta_from_tar` does, would avoid that at the cost of a second
  reader to keep safe; once per checksum, it isn't worth one yet (decided
  with the user, 2026-10-23).
- **`alai sync --check`**, as `uv sync --check` (uv 0.13.5, 2026-10-23:
  "Check if the Python environment is synchronized with the project",
  exiting 1 when it isn't, without changing anything): what
  `alai env`'s warning works out, as an exit status for CI. Today
  `alai sync --check` is an argparse error.
- **uv's GitHub fast path**, reading `galaxy.yml` and `meta/runtime.yml`
  through GitHub's API to lock without running git
  ([lockfile.md](lockfile.md#git-repositories)).
- **`--rev`, `--tag` and `--branch` on `alai add`**, as `uv add` has, and
  uv's `tag` and `branch` keys beside `rev`; `@<rev>` in the URL covers
  them for now.
- **Git LFS files**, which uv fetches with `lfs = true`; a collection
  storing files in LFS gets their pointer files.
- **One cache entry per repository whatever its URL's spelling**: uv
  canonicalises a git URL before hashing it (no `.git`, lowercase host);
  alai hashes it as written, so two spellings fetch the same commit twice.
- **`--no-editable`** on `sync`, as uv's, to install editable sources as
  copies, for building an image say.
- **`collection install -r requirements.yml`**, ansible-galaxy's
  requirements file, as `uv pip install -r requirements.txt` takes pip's:
  shelved, see [requirements-file.md](requirements-file.md).

- **Speed**: what's left after the native extension, such as installing
  each collection as soon as it's extracted, and a parsed index cache, is
  in [performance.md](performance.md).
- **Verifying against `FILES.json` later.** Every published collection
  carries a SHA-256 for each file. An `alai cache verify` command, or
  `--verify` on `sync`, could check cached or installed collections against
  it, which uv has no equivalent for.
- **Progress** while downloading; see [output.md](output.md).
- **Remembering what a URL held**: a URL that isn't locked, or given to
  `collection install`, is downloaded every time, as the cache is keyed by
  checksum. uv's cache keeps a URL's download with its HTTP caching headers;
  a map from URL to checksum, revalidated with an ETag, would do the same.

- **Leftover `.tmp-*` directories** from a run killed mid-download stay in
  `collections-v1/` until a future `alai cache clean` or prune removes them;
  they're never read.
