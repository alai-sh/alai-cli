# Editing alai.toml

How `alai add` and `alai remove` change the project's requirements, in
`src/alai/_edit.py` (the commands) and `src/rust/src/toml.rs` (the
editor, `alai._native.TomlDocument`). The user documentation is
`docs/managing-collections.md#adding-and-removing-collections`.

## The commands

Tested against `uv add` and `uv remove` (uv 0.13) on 2026-10-20, with a
`pyproject.toml` holding comments:

- **A name alone is written as `>=` the version locked for it**
  (`uv add six` wrote `"six>=1.17.0"`); one with a requirement is written
  as given, without its spaces (`"six<1.17"`), and replaces the entry
  already there. A collection already listed, given by name alone, is
  left as it is. With `--frozen` nothing is resolved, so a name alone
  stays bare (`uv add --frozen chardet` wrote `"chardet"`).
- **`--group NAME` and `--dev`**, as uv's: `add` creates the group's table
  when it doesn't exist and installs that group with the default ones
  (`uv add --group test` installed it though `test` isn't a default
  group); `remove` leaves an emptied group as `collections = []`, as uv
  leaves `test = []`. `remove --group` syncs the default groups alone, so
  the exact sync uninstalls what's left of the group too (uv 0.13.5,
  2026-10-23: after `uv add --group test six idna`, `uv remove --group test
  idna` uninstalled six as well, with `six>=1.17.0` still in the group).
  What to ask users about it is in
  [user-questions.md](user-questions.md#what-remove---group-leaves-installed).
- **Then lock and sync**: the edit is resolved first, keeping the locked
  versions that fit, and `alai.toml` and `alai.lock` are only written once
  that succeeds, so a requirement nothing satisfies leaves both untouched
  (uv's "Failed to add dependencies" left `pyproject.toml` as it was).
  `--no-sync` locks without installing and `--frozen` only edits
  `alai.toml`, as uv's flags do. The sync is exact, so `remove`
  uninstalls what nothing needs any more.
- **`-U` and `-P`** are on both, as uv has them, upgrading while they lock;
  with `--frozen` they're an error, as nothing is resolved.
- **`remove` of something not there is an error, with a hint** where it is
  (uv: "The dependency `certifi` could not be found in
  `project.dependencies`", then "hint: `certifi` is in the `test` group
  (try: `uv remove certifi --group test`)"). Here it's one line:
  `d.d isn't in project.collections; it's in group 'test' (try: alai remove
  d.d --group test)`.
- **`remove` takes names, never the source given to `add`** (2026-10-22),
  as `uv remove` does: uv 0.13.0 refused `uv remove git+https://…` ("URL
  requirement must be preceded by a package name"). Given a source,
  `alai remove` says which collections `[project.sources]` takes from it
  (`_by_source`: a git URL matched by repository and `subdirectory`,
  whatever its `rev`; a path by where it resolves), as `uv pip uninstall`
  looks a URL up among what's installed ("Skipping `git+https://…` as it
  is not installed"). `collection uninstall` does the same from the
  install records alai writes, and an editable link's target.
- **A new collection goes in order when the list is sorted**, else at the
  end, as uv keeps a sorted list sorted.
- **The array is then laid out as uv lays it out** (decided with the user,
  2026-10-23): one entry per line with a trailing comma, on any `add`,
  `remove` or replaced requirement, a one-line array included; see
  [the editor](#the-editor).
- **`alai.toml` keeps its newlines and permissions**, and is written beside
  itself and renamed into place. A file whose every line ends in `\r\n`
  keeps them (decided with the user, 2026-10-23), where uv 0.13.5 rewrote
  the same `pyproject.toml` with `\n`; one with mixed line endings gets
  `\n` throughout, as `toml_edit` writes it.
- **`--locked`** (asked for 2026-10-21), as on `sync`: the edit is
  resolved, and if `alai.lock` would change, nothing is written and it
  exits 1. Tried with uv 0.13.0 on 2026-10-21:
  - `uv add --locked six` (new) and `uv remove --locked requests`
    resolved, then failed with "The lockfile at `uv.lock` needs to be
    updated, but `--locked` was provided", exit 1, with `pyproject.toml`
    left as it was. It's the lock as a whole that's compared, so `uv add
    --locked idna`, already listed, failed too when the lock was stale for
    another reason. Here it's `alai.lock would change, and --locked was
    given`, without uv's "hint: To update the lockfile, run `uv lock`",
    which can't add the collection; `sync --locked`'s message has `run
    alai lock` because there it's the fix. Before the error it says what
    would change, as `lock --check` does ([lockfile.md](lockfile.md)).
  - `uv add --locked six`, already listed, went on to sync ("Checked 1
    package"), exit 0. So does alai: an `add` that's already been done is
    a no-op that installs, which is what a setup script wants.
  - With no lock, uv failed before resolving: "Unable to find lockfile at
    `uv.lock`, but `--locked` was provided", exit 1. Here it's `sync
    --locked`'s message, also before resolving.
  - `uv add --locked 'six>=1.17'` with `six>=1.17.0` listed: exit 0,
    `pyproject.toml` rewritten to `six>=1.17`, `uv.lock` untouched. alai
    writes `alai.toml` the same way and never writes `alai.lock` with
    `--locked`, which keeps its own text as `sync --locked` does.
  - `--locked` with `--frozen` is an argument error in uv; here it's the
    same argparse error `sync --locked --frozen` gives, exit 2, raised by
    hand since `--frozen` is already in a group with `--no-sync`. With
    `--no-sync` it's accepted, and checks without installing.
  - uv also has `UV_LOCKED=1`; alai has no environment variable for
    `--locked` on any command yet.

- **A directory adds the collection it holds** (2026-10-22), as `uv add
  ../lib` does. Tried with uv 0.13.0 on 2026-10-22:
  - `uv add ../lib` wrote `"lib"`, with no version bound, to
    `dependencies` and `lib = { path = "../lib" }` to `[tool.uv.sources]`;
    `--editable` added `editable = true`. Here the name goes in
    `project.collections` (or the group's) alone, and the entry in
    `[project.sources]`, at the end of that table, or in a new
    `[project.sources]` at the end of the file.
  - From a subdirectory, `uv add ../../lib3` wrote `path = "../lib3"`,
    relative to the project root; an absolute path stayed absolute. alai
    does both.
  - Adding the same directory again replaces the entry: `uv add ../lib3`
    after `--editable` dropped `editable = true`.
  - **`--editable DIR`, per directory, where `uv add` has a flag** (decided
    with the user, 2026-10-23). `uv add --editable` applies to every requirement
    on the line, whatever its position, as its other modifiers (`--group`,
    `--rev`) do: `uv add --editable ./pkg six` and `uv add ./pkg --editable
    six` both fail with "`six` did not resolve to a local directory, but the
    `--editable` flag was provided" (uv 0.13.5). alai takes `--editable DIR`,
    which can be repeated, as `pip install -e` and `uv pip install -e` do
    (`uv pip install -e ./pkg six` installed pkg editable and six normally),
    so one line can add a checkout to work on beside ordinary requirements,
    and `alai add` and `alai collection install` read the option the same
    way. As with those, `--editable lib2` takes `lib2` as the directory.
    Only the long spelling, as `uv add` has no `-e` (uv 0.13.5); `-e` is
    `alai collection install`'s, as it's `uv pip install`'s.
  - What's a directory: one with a `/`, `.`, `..`, or a leading `~` (`uv
    pip install lib2` looked for the package `lib2`, `lib2/` installed the
    directory). A collection name never has a `/`, so `community.general`
    is always the name, as ansible-galaxy reads it even beside a directory
    of that name.
  - An absolute path is written normalised, as uv writes it: `uv add
    /x/libabs/` and `uv add /x/libabs/../libabs` both wrote `path =
    "/x/libabs"` (uv 0.13.5, 2026-10-23), so a directory has one spelling
    in `alai.toml`, `alai.lock` and the install record; a relative one is
    written relative to the project root, normalised the same way.
  - `uv remove lib3` removed its source too. alai removes a `{ path = ...
    }` entry once nothing in `project.collections` or any group lists the
    collection; an `{ index = ... }` entry stays, as it also applies to what
    other collections depend on and `alai add` never writes one.
- **A tarball adds the collection it holds** (2026-10-22), by path or URL,
  as `uv add` takes an archive. Tried with uv 0.13.0 on 2026-10-22, with an
  sdist served by `python -m http.server`:
  - `uv add ../dist/lib-0.1.0.tar.gz` wrote `lib = { path =
    "../dist/lib-0.1.0.tar.gz" }`, and `uv add lib-0.1.0.tar.gz`, with no
    `/`, `{ path = "lib-0.1.0.tar.gz" }`: a name ending in an archive
    extension is a file. alai takes `.tar.gz` that way, the only extension
    a collection build writes; `x.tar.gz` can't be a collection name, which
    has one dot.
  - `uv add http://…/lib-0.1.0.tar.gz` wrote `lib = { url = "…" }`, and
    `uv add file:///…/lib-0.1.0.tar.gz` the absolute path, `{ path = "/…"
    }`. alai does both: `http://`, `https://` and `file://`, in any case,
    are URLs, checked before the `/` that makes a path. A URL is downloaded
    to read its name, and that download handed to the lock, so it isn't
    downloaded twice.
  - `--editable` with a tarball is an error, as nothing can be linked.
  - `uv remove lib` removed the archive's source as it does a directory's;
    alai removes a `{ url = ... }` entry as it does a `{ path = ... }` one.
    uv also removed `[tool.uv.sources]` once empty; alai keeps the empty
    `[project.sources]` header (decided with the user, 2026-10-22), so
    `remove_key` only ever touches the one line.

## The editor

- **`toml_edit`, cargo's and uv's, in the native extension** (decided with
  the user, 2026-10-23, replacing `_toml_edit.py`, a scanner of our own
  that spliced each edit into the text). It's the mature format-preserving
  editor, with no maintained Python binding, so it's exposed from
  `alai._native` as `TomlDocument`. It reads every valid file tried, moves
  nothing it isn't asked to (an out-of-order array-of-tables sub-table and
  a three-level one, which tomlkit moves or can't read, stay as they are),
  and edits arrays and sources inside inline tables and dotted keys, which
  `_toml_edit` refused. Reading `alai.toml`, the config files and
  `alai.lock` moved to it too, with `toml_loads` in place of `tomllib`, so
  every Python reads TOML 1.1 the same way and `tomli` is no longer a
  dependency ([performance.md](performance.md#toml-in-the-extension)).
- **uv's layout for a requirement array**, ported from `uv-project-edit`
  in uv 0.13.5 (`add_dependency`, `remove_dependency_at` and
  `reformat_array_multiline`), since `toml_edit` leaves layout to the
  caller: one entry per line, a trailing comma, indented as the first entry
  is, else four spaces. A comment at the end of a line stays on that line,
  one on a line of its own stays there, one on the bracket's line stays on
  it, and a removed entry's comment stays on a line of its own. Tried
  against `uv add --frozen` and `uv remove --frozen` on 2026-10-23: the
  layouts in `tests/test_toml.py` are what uv wrote for the same arrays,
  `"a",  # one` keeping its two spaces, with one difference: after
  removing the first entry of a one-line array, uv indented by one space,
  the one after the bracket, where alai indents by four.
- **Checked.** Each edit is also made to a plain copy of the document's
  data; the new text is read back and must equal it, or the edit raises
  `TomlEditError`, the document keeps its last good text, and `alai add`
  says which array to edit by hand. With `toml_edit` making structural
  edits this is a guard rather than something known to fire: nothing tried
  trips it.
- **New tables**: a group's array is created under a header of its own,
  `[project.groups.test]`, with the tables above it implicit; inside dotted
  keys or an inline table, it's a dotted key or an inline table too.
  `toml_edit` puts a new table after the last table before it in the
  document's order, so a new `[project.sources]` goes after `[project]` and
  its sub-tables, before `[tool.x]`, where `_toml_edit` put it at the end
  of the file; uv's `[tool.uv.sources]` lands after `[tool.x]` by the same
  rule.
- **`[project.sources]` entries**: `set_table` writes `{ path = "../b",
  editable = true }`, on one line however long, as uv writes one, keys in
  the order given. A replaced entry is written the same way, even if it was
  spread over lines (decided with the user, 2026-10-23): what alai writes
  is alai's to lay out, and what it keeps is the rest of the file. It
  stays where it is, with any comment after it; uv 0.13.5 removed the old entry and
  appended the new one at the end of `[tool.uv.sources]`, dropping its
  comment. `remove_key` removes the key with the comments on its line and
  above it, as uv's `sources.remove` does, and leaves an emptied table's
  header (decided with the user, 2026-10-22), where uv removes
  `[tool.uv.sources]`.
- **What `toml_edit` changes**: a byte-order mark is dropped, and a missing
  final newline is added once the file is edited; the file is untouched
  until then. uv does both. Inside a multi-line string it keeps `\r\n`,
  which `tomllib` reads as `\n`; TOML allows either, and nothing alai
  reads is a multi-line string.
- **tomlkit was considered and not used** (0.15.2, 2026-10-20 and
  2026-10-22): it re-serialises the document and moves a sub-table of an
  array of tables written out of order into the array's last element on
  any edit, fails on a three-level one (`[[tool.x.item]]`, `[tool.y]`,
  `[tool.x.item.sub]` raises `Key "item" already exists`), drops a removed
  item's comment, is pre-1.0, and costs ~15 ms to import.
- **Speed**: `toml_edit` made an add, read to write, in 0.003 ms on a small
  `alai.toml` and 1.0 ms on a 91 KB file (2026-10-22, from Rust), against
  `_toml_edit`'s 0.06 ms and 24 ms and tomlkit's 0.16 ms and 58 ms.
