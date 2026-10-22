# Editing alai.toml

How `alai add` and `alai remove` change the project's requirements, in
`src/alai/_edit.py` (the commands) and `src/alai/_toml_edit.py` (the
editor). The user documentation is
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
  leaves `test = []`.
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
- **`alai.toml` keeps its newlines (`\n` or `\r\n`) and permissions**, and is
  written beside itself and renamed into place.
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
    after `--editable` dropped `editable = true`. uv 0.13.0 has no `-e` on
    `uv add`, only `--editable`, so alai has only `--editable` there.
  - What's a directory: one with a `/`, `.`, `..`, or a leading `~` (`uv
    pip install lib2` looked for the package `lib2`, `lib2/` installed the
    directory); with `--editable`, every argument is one, as `uv pip
    install -e lib2` installed the directory `lib2`. A collection name never
    has a `/`, so `community.general` is always the name, as ansible-galaxy
    reads it even beside a directory of that name.
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

- **Our own, surgical, and checked.** A small scanner reads enough TOML to
  find the exact text of one key's value (comments, the four string forms,
  arrays, inline tables, scalars including datetimes with a space, headers,
  dotted and quoted keys); `insert`, `replace`, `remove` and `create`
  splice one change into that array's text and touch no other byte. Each
  edit is then parsed with `tomllib` and must equal the original document
  with only that array changed, or it's refused with an error and nothing
  is written: a mistake can stop an edit, never write a wrong file.
  Random sequences of edits over several layouts, CRLF included, are in the
  tests.
- **`[project.sources]` entries** (2026-10-22): `set_table` sets a key to
  an inline table, in place, or as a new line after its table's last key,
  or under a new header at the end of the file; `remove_key` removes a key
  written on a line of its own, with that line and any comment on it, and
  leaves an emptied table's header, as uv leaves `[tool.uv.sources]`. Both
  are checked the same way. A table written with dotted keys under
  `[project]` (`sources."a.b" = ...`) can't gain a header, so the check
  refuses that.
- **What it refuses**: an array inside an inline table (`project = {
  collections = [...] }`), whose keys it doesn't record, and anything the
  check rejects, such as a new `[project.groups.test]` header for a table
  dotted keys already define. The error names the array to edit by hand.
- **A removed item's comment stays**, on its own line, as uv keeps it.
- **Layout inside the array isn't always kept** (checked 2026-10-21; the
  docs say to check the diff):
  - A comment on its own line above an entry: a new entry sorting before
    that entry goes between the comment and it, so the comment then sits
    above the new entry. uv 0.13.0 treats the comment as the next entry's:
    `uv add --frozen certifi` put `"certifi"` above `# networking`, which
    stayed above `"requests"`. Following uv here is the fix if it matters.
  - A list with no trailing comma gains one when an entry is appended at
    the end, or the last one is removed. uv goes further: it gave every
    entry a trailing comma on any `uv add`, even inserting at the front.
  - Two entries on one line in a multi-line array: removing the second
    joins the next line's entry onto that line (`"b.b", "f.f",`). Not
    checked against uv.
- **tomlkit was considered and not used** (0.15.2, 2026-10-20). It
  round-trips an untouched file exactly and handled every form tried, but
  it re-serialises the document, and its own docs give one case where that
  moves text: a sub-table of an array of tables written out of order, such
  as `[fruit.texture]` after `[[fruit]]` and another table, is moved into
  the array's last element. `alai.toml` can hold any tool's settings under
  `[tool.<name>]`, so that could happen to a file alai only meant to add a
  line to. It also drops a removed item's comment, is pre-1.0, and costs
  ~50 ms to import.

  Looked at again on 2026-10-22 (tomlkit 0.15.2, against `_toml_edit` and
  `tomllib`):
  - **Untouched, it round-trips** comments, alignment, CRLF and mixed line
    ends, tabs, no final newline, every string and number form, dates,
    quoted and dotted keys, and tables out of order, byte for byte. The
    one exception is still the array-of-tables sub-table above, which any
    edit moves, even one elsewhere in the file.
  - **It fails on some valid files**: a sub-table of a three-level array
    of tables written after another table (`[[tool.x.item]]`, `[tool.y]`,
    `[tool.x.item.sub]`) raises `Key "item" already exists`, where
    `tomllib` reads it; two levels parse. That's the shape another tool's
    settings under `[tool.<name>]` could take, and `alai add` would then
    fail on a file it never meant to touch.
  - **TOML 1.1**: it reads and round-trips multi-line inline tables, a
    trailing comma in one, `\e` and `\xHH`, and times without seconds, as
    Python 3.15's `tomllib` does and 3.11 to 3.14's don't. Otherwise it
    accepts and refuses what `tomllib` does.
  - **The edits alai makes**: adding and removing requirements matched
    `_toml_edit` byte for byte in every layout tried, except that removing
    an entry drops its trailing comment, which `_toml_edit` keeps on its
    own line. A new source is written `{path = "../b", editable = true}`,
    without the spaces inside the braces the rest of a file usually has. A
    new `[project.sources]` goes after `[project]`, where `_toml_edit` puts
    it at the end of the file, but with no blank line before the next
    table. It handled sources written as dotted keys in `[project]`
    (`sources."a.a" = ...`), adding and removing, which `_toml_edit`
    refuses.
  - **Speed**: an add on a small `alai.toml`, read to write, 0.16 ms
    against `_toml_edit`'s 0.06 ms; on a 91 KB file, 58 ms against 24 ms.
    Importing it was 14.5 ms here, against 5.4 ms for `_toml_edit` with
    `tomllib`.
- **`toml_edit` is the mature format-preserving editor** (cargo's and uv's),
  but it's Rust with no maintained Python binding; it could come with the
  native extension if editing needs grow, for `[project.sources]` say
  ([performance.md](performance.md#the-native-extension)).

  Tried on 2026-10-22 (`toml_edit` 0.25.17, TOML 1.1, from a scratch Rust
  program, on the tomlkit cases above), and uv 0.13.0 on the same layouts
  with `uv add --frozen` and `uv remove --frozen`:
  - **It parses every valid file tried**, the three-level array of tables
    tomlkit fails on included, and moves nothing: the out-of-order
    array-of-tables sub-table stays where it is. It accepts and refuses
    what Python 3.15's `tomllib` does, TOML 1.1 included, except a
    byte-order mark, which it accepts and drops.
  - **Its output normalises the whole file**: CRLF and mixed line ends
    become LF, a missing final newline is added, a byte-order mark is
    dropped, and spaces in a dotted table header can change (`[a . c]`
    became `[a. c]`). uv does the same with the first three (it kept
    `[tool . x]`), so these are uv's behaviour too; `_toml_edit` keeps
    CRLF and a missing final newline.
  - **Its editing API leaves the layout to the caller.** Inserting into a
    multi-line array puts the new item on the previous one's line
    (`"a.a", "b.b>=1.0",  # first`), and removing the first item leaves
    `[ "d.d"]`, or moves its comment onto the bracket's line. uv has its
    own code on top: it keeps comments, writes one entry per line with a
    trailing comma, and turns a one-line array into a multi-line one on any
    add (with a one-space indent after removing from a one-line array).
    Adopting it means writing that layer, as uv did.
  - **Tables**: a new `[project.sources]` goes right after `[project]`,
    with a blank line before the next table; sources written as dotted keys
    are added to and removed from in place, which `_toml_edit` refuses.
  - **Speed**: an add, read to write, 0.003 ms on a small `alai.toml` and
    1.0 ms on a 91 KB file, against `_toml_edit`'s 0.06 ms and 24 ms and
    tomlkit's 0.16 ms and 58 ms, with no import beyond the extension.
