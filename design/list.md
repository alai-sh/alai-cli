# Listing collections

`alai collection list`: what's in the collections directory, whatever put
it there, compared with `alai.lock` when the project has one. In
`src/alai/_list.py`, with the command's options in
`src/alai/_commands/collection.py`. The user documentation is
[docs/installing-by-hand.md](../docs/installing-by-hand.md#listing).
Designed with the user on 2026-10-22, from mockups of three layouts.

## What was compared

uv 0.13.0, in a scratch project with `requests` and `rich` added and `six`
installed with `uv pip install` (2026-10-22):

- **uv has no `uv list`.** Listing is split as alai's commands are: the
  project side reads the lock, `uv pip` reads what's installed.
- **`uv tree`** is the lock's dependency tree (`--depth`, `--invert
  --package`, `--outdated`). It didn't show `six`, which isn't in the lock.
- **`uv export`** converts the lock to `requirements.txt`, `pylock.toml`
  or CycloneDX, for tools that don't read `uv.lock`. Not a view.
- **`uv pip list`** is a table of name, version and an editable install's
  location, with `--format columns|freeze|json`, `--outdated`,
  `-e/--editable`, `--exclude-editable`, `--exclude`, `--strict`,
  `--target` and `--prefix`. It showed `six`. It prints nothing about the
  environment it read.
- **`uv pip freeze`**, **`uv pip show`** (name, version, location,
  requires, required-by; `warning: Package(s) not found for: nope` and
  exit 1 for one that isn't installed), **`uv pip tree`** (`six` a root of
  its own) and **`uv pip check`**.

## Decisions

- **`alai collection list`**, as `uv pip list`: `alai collection` is
  alai's `uv pip`. It reads the directory, so it lists what Ansible would
  load, whether `alai sync`, `alai collection install` or something else
  put it there.
- **Compared with `alai.lock`**, which `uv pip list` doesn't do: whether
  what's installed is what the lock says is what a user asks most, and
  `alai run` already warns about collections the lock doesn't list
  ([user-questions.md](user-questions.md#collections-alai-run-doesnt-remove)).
  A marker per row, as `alai find` has one:

  | Marker | Status (JSON) | When |
  |---|---|---|
  | `✓` green | `locked` | Installed as locked |
  | `?` yellow | `unlocked` | Not in `alai.lock` at all |
  | `≠` yellow | `different` | Installed, but not as locked |
  | `✗` red | `missing` | Locked, and a plain `alai sync` would install it, but it isn't installed |

  `?` is yellow, not red: a collection being worked on beside the locked
  ones is normal. A row not `✓` says how instead of nothing.
- **"As locked" is `sync`'s own test** (`_sync.expected`, shared with
  `sync`'s plan): the locked version, and the install record a collection
  from a directory, a tarball or a commit has
  ([install.md](install.md#when-a-copy-is-made-again)), none for an index's;
  an editable one is a link to the locked checkout, its version read from
  the checkout's `galaxy.yml`. So `≠` covers another version, another
  source (an index's copy where the lock has a directory, or the other way
  round), another commit of the same repository, and a directory whose
  `galaxy.yml`, `MANIFEST.json` or `meta/runtime.yml` changed since it was
  copied. The note says which: `alai.lock has 11.1.0`, `alai.lock has it
  from ~/src/corp.net`, `alai.lock has it at 9b1e0d4`, `alai.lock has 2.0.0
  from index 'alai'`, `its source has changed since it was installed`.
- **Missing means what a plain `alai sync` installs**: the project's
  collections, the default groups', and the project's own collection. A
  collection only another group needs isn't `✗` when it's absent, as
  syncing without `--group` wouldn't install it; when it is installed, the
  lock lists it, so it's `✓`.
- **A namespace that's a link** is a row of its own, `ns.*`, with where it
  points, as Ansible loads everything behind it and `alai run` warns about
  it.
- **Rows, as `alai find` draws them** (chosen over sections by role and
  over `uv pip list`'s table): marker, the namespace blue and the name
  bold, the version, then the note and the source. Sorted by name. Sections
  for the project's collections, dependencies and unlisted ones said more,
  but took more room and are harder to pipe; that split can come in a
  later `show` or `tree`.
- **The source only when it isn't an index**, as `uv pip list` shows
  nothing for a package from PyPI: a directory or tarball's path (`~` for
  home), a tarball's URL, `git+<url>@<commit>` with the commit cut to 7
  characters as git shortens one, and an editable link's target, tagged
  `editable` in cyan. JSON has every source in full, and the lock's for
  each collection, index included.
- **A note and a source that don't fit wrap**: on a terminal, a row too
  wide for it puts the source on its own line under the note, rather than
  cut it as `find` cuts a description, since the source says which
  checkout or file it is and a git source's commit is at its end.
  Something still too wide for its line is cut with `…`. Piped, every row
  is one line, whole, so `grep` sees all of it.
- **A legend of only the markers shown**, on a terminal and not with
  `-q`, as `find`'s; none when every row is `✓`. Then, on stderr, a hint
  that `alai sync` installs what the lock lists and removes the rest, when
  any row isn't `✓`.
- **Which directory is read is always said**, on stderr, in
  `collection install`'s words (`Using collections directory at: …`), even
  for `collections/` in the current directory, which `install` leaves
  unsaid; with no `alai.lock`, `No alai.lock to compare with` follows. uv
  names neither. A listing is only of that directory, so one of the wrong
  directory looks like an empty or partial listing: the user ran it beside
  a blank `alai.toml` in `~/dev`, whose collections are in
  `~/dev/ansible_collections`, and got none, as the project's
  `collections_dir` is `collections` (2026-10-22). The directory can also
  come from `--collections-dir` or `ALAI_COLLECTIONS_DIR`.
- **Without `alai.lock`**, in a project that has none or with
  `--collections-dir` outside a project, rows have no marker. No project and no
  `--collections-dir` is the same error as `collection install`'s.
- **Not listed**: `ansible.builtin`, which isn't in the directory and
  which alai doesn't manage, though `find` counts it as installed.
- **`--output-format text|json`**, as `find` has it, rather than uv's
  `--format columns|freeze|json`: `v`, the directory, the lock's path or
  null, each collection's name, version, absolute path, its install
  record's source read as the lock reads one, or null if this alai can't
  read it (`{"directory": ...}` for a link; decided with the user,
  2026-11-01, so a record's format doesn't decide the output), whether
  it's editable, and `lock` (null with no lock, else status, version and
  the lock's source table), then the namespace links.
- **No freeze format.** It would read as a `requirements.yml` that
  ansible-galaxy could install from, which it couldn't be relied on to be:
  alai's index isn't Galaxy's API, and other sources may follow. The same
  goes for an `alai export`. See
  [user-questions.md](user-questions.md#exporting-a-requirements-file).
- **No filters yet**, such as uv's `-e/--editable`,
  `--exclude-editable` or `--exclude`: added when someone asks.
- **Exit 0** whatever the comparison finds, as `uv pip list`; it's a
  listing, not a check.

## Not done yet

- `--outdated`, which needs the index.
- A check of dependencies and `requires_ansible`, as `--strict` or an
  `alai collection check`, as `uv pip check`.
- `alai collection show`, with where a collection came from and what
  requires it, and a lock-side `alai tree`.
