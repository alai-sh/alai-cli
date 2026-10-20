# Configuration design

The user-facing reference is [docs/configuration.md](../docs/configuration.md),
with project discovery and layout in [docs/projects.md](../docs/projects.md),
implemented in `src/alai/_config.py`. This note keeps why it works that way,
what's planned on top of it, and the questions still open.

## Decisions

- **Layering follows uv's**: flags, environment, project file, user file,
  system file, defaults. Scalars take the highest level, arrays concatenate
  highest first. `--config-file` and `--no-config` are uv's flags.
- **One file name, `alai.toml`, at every level.** A `pyproject.toml`
  `[tool.alai]` table was dropped: Ansible projects rarely have a
  `pyproject.toml`, and finding one meant parsing every `pyproject.toml` up the
  tree.
- **Keys are snake_case**, matching what Ansible users already read
  (`ansible.cfg`'s `collections_path`, `galaxy.yml`, `meta/runtime.yml`'s
  `requires_ansible`). Flags stay kebab-case. The index spec's `config.json`
  stays kebab-case: it's a wire format users never write.
- **`alai.toml` fixes the project root**, rather than only supplying settings
  as `uv.toml` does, because the project's requirements and lockfile will
  live next to it. One lockfile has to install to one place whichever
  directory alai runs from. Letting a nearer `ansible.cfg` choose the root
  would give one repo two collection trees.
- **Marker order**: `alai.toml`, then `ansible.cfg`, then
  `collections/ansible_collections/`. `ansible.cfg` is a weaker signal than
  alai's own file because repos often hold several (per environment, per
  playbook directory, in test fixtures), and Ansible itself only reads it from
  the current directory, never a parent. The home directory got no special
  case: a stray `~/ansible.cfg` is rare, and an exception is harder to reason
  about.
- **Strict loading.** Unknown keys are errors at every level, so typos fail
  loudly and alai can add keys without colliding with anything a file already
  uses; nothing needs reserving. `[tool]` is the one table left to other
  tools. Possible relaxations, if version skew hurts: warn rather than fail in
  user and system files (dotfiles synced across machines with different alai
  versions), an "it may need a newer alai" hint on unknown keys, and a
  `required_version` setting as uv has.
- **`collections_dir` is project-only, in `[project]`**: one directory
  shared by every project defeats the per-project layout. It's a fact about
  the project rather than how alai runs, so it lives in the `[project]`
  table, which only the project file can have. That makes project-only a
  rule about one table rather than a check per key, and avoids TOML's trap
  where a top-level key written below a `[table]` header silently joins that
  table. Settings every level can set (`cache_dir`, `link_mode`, `index`)
  stay top-level, so each maps one-to-one to its flag and environment
  variable. Installing to `~/.ansible/collections` will be a flag of its
  own.
- **`collections_dir` may point outside the project.** In `alai.toml` that's
  for layouts true of every checkout, such as `"../collections"` for a
  manifest kept in a subdirectory. Paths true of one machine or run go in
  `--collections-dir` or `ALAI_COLLECTIONS_DIR`: an image build installing to
  `/usr/share/ansible/collections`, a read-only checkout installing to a
  temporary directory, or a project on a network filesystem keeping its
  collections on local disk (hardlinks also need the cache and the
  collections on one filesystem). uv makes the same split for its project
  virtual environment: only `UV_PROJECT_ENVIRONMENT` moves it. Outside the
  project, `ansible-playbook` finds nothing without `alai run` or
  `ANSIBLE_COLLECTIONS_PATH`, and the installer's rule of never replacing a
  directory it didn't install matters most there.
- **`--config-file` holds settings only**, as uv's does: `[project]` there
  is an error, even when it names the project's own `alai.toml`. So there's
  one rule: `[project]` only ever comes from the project's `alai.toml`, found
  by discovery. A CI job that needs a
  different `collections_dir` sets `--collections-dir` or
  `ALAI_COLLECTIONS_DIR`. This also settles a path question: with no
  `[project]` in a `--config-file`, a relative `collections_dir` in a file is
  always relative to the project root, so no `{root}` placeholder is needed.
- **Everything under `[project]` is the project; everything else is a
  setting** (2026-10-20). Settings (top-level keys and `[[index]]`) can be
  set in any config file, a flag or an environment variable; `[project]` and
  its sub-tables (`[project.groups.<name>]`, `[project.overrides]`) only in
  the project's own `alai.toml`. Top-level `[groups]` and `[overrides]`
  tables, as `pyproject.toml` has `[dependency-groups]`, were considered, but
  then which tables were project-only would be a list to learn rather than
  visible in the file. TOML defines `[project]` from its sub-tables, so a
  collection repo's file can hold only `[project.groups.test]` and
  `[project.overrides]`, and the check catches either form. A top-level
  `[groups]` is just an unknown key, which the project file's suggestion
  points at `project.groups`. A collection repo's `[project] requires_ansible`
  or `collections`, if allowed, would override what `meta/runtime.yml` and
  `galaxy.yml` say (see
  [collection-development.md](collection-development.md#open-questions)).
- **Settings are defined once**, in `SETTINGS` in `src/alai/_config.py`:
  reading each level, merging, the argparse options and `config show` all
  loop over it. `Settings` stays a hand-written class so code using it is
  type-checked; a test checks it has an attribute for every definition.
- **Flags exist only on the commands that use them.** A flag a command
  doesn't use is an argparse error, never silently accepted. When commands
  need different subsets (`lock` the indexes, `cache clean` only the cache),
  each definition gets a group (`cache`, `resolve`, `install`) and a command
  asks for the groups it uses. Not done yet: `config show` uses every
  setting, so there's no second subset to test it against.
- **Config files and environment variables apply to every command**, and a
  command ignores the settings it doesn't use. They're set once, for all
  commands (`link_mode` in a user file, `ALAI_INDEX` for a CI job), so
  `alai lock` not using `link_mode` isn't a mistake to report. `config show`
  displays every value and where it came from.
- **Every setting is validated on every command**, used or not, so a typo in
  `alai.toml` fails on the next command run rather than the next install,
  as the strict loading above promises. The cost: a bad `ALAI_LINK_MODE` in
  CI breaks `alai lock` too, with an error naming the variable.
- **Boolean settings get a flag pair**, `--system-certs` and
  `--no-system-certs` (argparse's `BooleanOptionalAction`), so the command
  line can override a file or environment variable either way, as `--link-mode
  auto` undoes a file's `link_mode`. In a file the value must be TOML's
  `true` or `false`, not a string; the environment variable takes the words
  `ALAI_NO_CONFIG` takes (`1`, `true`, `yes`, `on`, `0`, `false`, `no`,
  `off`). `config show` prints `true` or `false`.
- **A default that can fail is worked out only when used.** `cache_dir`
  without a home directory has no default; `Settings.cache_dir` raises then,
  rather than `load_settings`, so commands that don't touch the cache still
  run, and `config show` prints the row as unknown.

## Open questions

- **`collections/` or `.alai/` as the default.** `collections/` next to the
  playbook is found by every runner with no setup: `ansible-playbook`,
  ansible-navigator and execution environments (the project is mounted in),
  AWX/Controller and ansible-runner. `.alai/` would mark the directory as
  generated, make `alai sync` cleanup simpler and leave room for other state,
  but nothing would find it without `alai run`, `ANSIBLE_COLLECTIONS_PATH` or
  an `ansible.cfg` line. Kept `collections/`; `collections_dir = ".alai"` plus
  `collections_path = .alai` in `ansible.cfg` gives the other layout. Revisit
  before the first release if AWX and execution environments matter less than
  assumed, since changing the default later moves everyone's installs.
- **`--project` naming a manifest file.** uv takes a directory or a path to a
  `pyproject.toml`; Cargo's `--manifest-path` must name a `Cargo.toml`.
  Neither allows another file name: the lockfile's name and place follow the
  manifest's, and discovery walking up from a subdirectory would never find a
  custom name, so every command would need the flag. Accepting
  `--project path/to/alai.toml`, as uv does, would be harmless and can be
  added later. Other file names are left out; groups or separate directories
  cover most reasons to want a second manifest.

## Error positions (planned)

Errors in a config file name the file, and the key path (`project.collections[2]`,
`index[1]`) for anything past TOML syntax, but only syntax errors carry a line:
`tomllib` reports positions for those, and returns plain dicts and lists with
none once a file parses. uv, ruff and Cargo show a line, column and snippet,
since Rust's `toml` crate keeps spans.

The plan, when an error is about to be raised:

- **Locate on the error path only.** Re-read the file's text and scan it for the
  key path. The file already parsed, so the scanner only sees valid TOML, and
  nothing costs anything on a successful run.
- **Match keys, never values.** The text of a value can differ from what
  `tomllib` returns (escapes, literal and multi-line strings, `1_000` or `0x3e8`),
  so the position is the key that holds the bad value; the message already
  shows the parsed value.
- **`tomllib` decodes the keys.** A key has many spellings (`link_mode`,
  `"link_mode"`, `"link\u005fmode"`, `project . collections_dir`, dotted keys,
  `[project]` versus `project = { ... }`, `[[index]]` versus `index = [{ ... }]`).
  Rather than reimplementing them, the scanner cuts out the text before the
  first `=` outside quotes, or a header, and parses `<text> = 0` with `tomllib`
  to get the exact key path. The scanner itself only finds where keys and
  headers end, tracks the current table and `[[...]]` repeat, and skips
  strings, comments and multi-line arrays. Array items such as
  `project.collections[2]` are found by counting top-level items.
- **No line rather than a wrong one.** Without exactly one match the message
  keeps today's form, and an array item it can't place falls back to its
  key's line.
- **Format `path:line:col: message`**, which terminals and editors make
  clickable, with columns in characters as `tomllib` counts them. Syntax
  errors move to the same prefix instead of `(at line 4, column 7)`. Errors
  from flags and environment variables keep naming the flag or variable.
- **Tested against `tomllib`**: parse awkward documents, walk every key path
  `tomllib` returns, and check the locator finds where each is defined, so a
  spelling the scanner reads differently fails a test.

Estimated at ~150 lines plus tests.

## Project manifest

The project's `alai.toml` also lists what the project needs, as `Cargo.toml`
does, with the lockfile to go next to it. The user-facing reference is
[docs/projects.md](../docs/projects.md#declaring-collections), implemented in
`src/alai/_manifest.py`; the version syntax is in [versions.md](versions.md).
Nothing uses it until `alai lock` exists.

```toml
[project]
collections_dir = "collections"
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "corp.tools>=1.2",
]

[project.groups.test]
collections = ["ansible.posix", "community.crypto"]

[project.overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]

[project.sources]
"corp.tools" = { index = "internal" }
```

Decisions:

- **The manifest is project data, not config.** `--no-config` and
  `--config-file` choose which settings are read; they don't change what the
  project needs. `_manifest.load_manifest` always reads the project root's
  `alai.toml`, as uv reads `pyproject.toml` dependencies under `--no-config`.
  Otherwise `alai lock --no-config` would see an empty project and write an
  empty lockfile, and a CI job's `--config-file` could swap the project's
  collections for its own while the lockfile beside `alai.toml` still
  described the originals.
- **Manifest keys anywhere else are errors**, as all of `[project]` is
  outside the project file (see `--config-file` and the `[project]` rule
  above). Since alai never
  reads them there, accepting them would let someone believe a CI file adds
  collections when it does nothing.
- **Keys and types on every command, values when used.** Loading settings
  from the project file checks the manifest's keys and types
  (`_config.RawManifest`), so a misspelt key or a string where an array
  belongs fails on the next command. Whether a requirement or specifier
  string is valid, or a pinned index exists, is checked by `_manifest` when a
  command reads the manifest. This keeps the version parsing (~1.3 ms of
  imports, mostly compiling patterns) off every other command, and `_config`
  never imports `_manifest`. With `--no-config` the project file isn't read
  for settings, so nothing in it is checked until a command uses the
  manifest. uv and Cargo do the same for manifests: both parse dependency
  requirements when a command loads the project (`uv tree`,
  `cargo metadata`) and leave other commands (`uv cache dir`,
  `cargo locate-project`) alone. Settings stay checked on every command, as
  uv checks `uv.toml`; Cargo instead reads each `.cargo/config.toml` value
  only when used.
- **A collection is listed once per list.** Two entries for one name in
  `project.collections`, or in one group, are an error rather than merged.
  The same name in the project and a group is allowed; the resolver combines
  them.
- **Index pins are checked when used**, by `alai lock`, like any other
  value. Checking them on every command would also break `alai config show`,
  the command for finding out why an index is missing.
- **Where a collection comes from is `[project.sources]`, not part of the
  requirement** (2026-10-20). An `INDEX:` prefix (`internal:corp.tools`),
  carried over from the early workspace notes, was dropped for a table like
  `[tool.uv.sources]`: requirement strings stay exactly ansible-galaxy's, so
  they copy to and from `requirements.yml` and `galaxy.yml`; a collection
  repo's `galaxy.yml` dependencies can't carry a prefix anyway; and git,
  path and URL sources need a table, so every kind of source is in one
  place. Only `{ index = "<name>" }` exists so far; other kinds are an
  error until supported. An entry applies to any collection in the
  resolution, not only those listed, overriding the index spec's rule that
  a dependency comes from its parent's index: the user named the index, so
  nothing is guessed, and pinning a transitive dependency is the point. So
  sources are resolution-wide, one per name, like overrides, and the lock
  records them as a top-level `[sources]`.
- **Groups are named like indexes** (`[a-z0-9][a-z0-9_-]*`), so a group is
  spelled one way in files and on the command line.

Requirements are strings rather than a Cargo-style table because collection
names contain a dot: `community.general = ">=9"` is a TOML dotted key. They're
listed per kind, as `requirements.yml` does with `collections:` and `roles:`:
a role and a collection can share a `namespace.name`, so one list couldn't
hold both. `project` and each group can take a `roles` key later without a
format change. For now alai handles collections only, and `-r
requirements.yml` warns about and skips roles.

Planned alongside it:

- **Collection source repos** keep their dependencies in `galaxy.yml`, which
  alai reads instead of `project.collections`. Their `alai.toml` holds groups
  and settings. See [collection-development.md](collection-development.md).
- **More kinds in `[project.sources]`**: git, URL and local path sources a
  requirement string can't express, as `[tool.uv.sources]` has. Only
  `{ index = "<name>" }` exists so far; the lock side is in
  [lockfile.md](lockfile.md#planned-other-sources).
- **`alai export --format requirements.yml`** writes the lock as a
  `collections/requirements.yml` for AWX/Controller, ansible-builder and
  ansible-navigator, which can't run alai.
- **Environments and an ansible-core pin**, with `requires_ansible` staying
  the range: see [environments.md](environments.md).
- **`[python]`** is reserved for Python requirements, should alai manage the
  virtual environment.

No `name` or `version`: a playbook project isn't published.

## Not read yet

- **`ansible.cfg`.** The plan is for a `collections_path` in the project
  root's `ansible.cfg` to choose `collections_dir` when alai's own settings
  don't, sitting between the project file and the default. User-level and
  system-wide Ansible config would never choose where alai installs, since
  that would make the same command behave differently on different machines.
  It needs Ansible's own rules first: only one `ansible.cfg` is used
  (`ANSIBLE_CONFIG`, then `./ansible.cfg` unless the directory is
  world-writable, then `~/.ansible.cfg`, then `/etc/ansible/ansible.cfg`),
  and relative paths in it are relative to the file. alai settings are never
  read from `ansible.cfg`.
- **`ANSIBLE_COLLECTIONS_PATH`.** It will be used to find what Ansible can
  already see, for shadowing warnings, never for where alai installs.
- **`galaxy.yml`** isn't a marker until collection repos are supported; see
  [collection-development.md](collection-development.md) for where it fits
  in the order.
