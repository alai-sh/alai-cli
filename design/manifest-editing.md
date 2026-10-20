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
- **A new collection goes in order when the list is sorted**, else at the
  end, as uv keeps a sorted list sorted.
- **`alai.toml` keeps its newlines (`\n` or `\r\n`) and permissions**, and is
  written beside itself and renamed into place.

### Not done yet

- **`--locked` on `add` and `remove`** (asked for 2026-10-21), as `sync`
  has it: resolve the edit, and exit 1 without writing `alai.toml` or
  `alai.lock` if the lock would change. Tried with uv 0.13.0 on 2026-10-21:
  `uv add --locked six` (new) and `uv remove --locked requests` resolved,
  then failed with "The lockfile at `uv.lock` needs to be updated, but
  `--locked` was provided" and "hint: To update the lockfile, run `uv
  lock`", exit 1, `pyproject.toml` untouched; `uv add --locked requests`,
  already there, went on to sync ("Checked 5 packages"). `--locked` with
  `--frozen` is an argument error; with `--no-sync` it's accepted. Mostly
  useful in CI, where an `add` that's already been done should be a no-op.

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
- **`toml_edit` is the mature format-preserving editor** (cargo's and uv's),
  but it's Rust with no maintained Python binding; it could come with the
  planned native extension if editing needs grow, for `[project.sources]`
  say ([performance.md](performance.md#planned-a-native-extension)).
