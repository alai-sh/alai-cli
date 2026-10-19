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
- **A placeholder for the project root in paths.** A relative path in a
  config file is relative to that file, including `[project]
  collections_dir` in a `--config-file`, while the project root still comes
  from discovery. So `--config-file ci/alai.toml` with
  `collections_dir = "collections"` installs to `ci/collections`, though
  leaving it unset gives `<root>/collections`. Kept: one rule for every path
  in every file. A placeholder would let such a file name the root instead:
  - `${PROJECT_ROOT}/collections`, as PDM has. Readable, but it looks like
    environment variable expansion, so `${HOME}` or `${CI_PROJECT_DIR}` would
    be expected to work: either alai expands any environment variable (a much
    bigger feature, and files whose meaning depends on the environment) or it
    rejects most `${...}`.
  - `{root}/collections` (or `{project_root}`), like Hatch's context
    formatting. Doesn't read as an environment variable; a literal `{` in a
    path would need escaping.

  Leaning towards a closed set of `{name}` placeholders, starting with
  `{root}`, expanded only in path settings and only at the start of a value,
  as `~` is. An unknown placeholder is an error, like an unknown key, so more
  can be added later. Not built: it only matters for a `--config-file`
  outside the project root that sets `[project]` paths.

## Project manifest (planned)

The project's `alai.toml` will also list what the project needs, as
`Cargo.toml` does, with the lockfile next to it. These tables will only be
allowed in the project file, like `[project]` already is.

```toml
[project]
collections_dir = "collections"
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "internal:corp.tools>=1.2",
]

[groups.test]
collections = ["ansible.posix", "community.crypto"]

[overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]
```

| Key | |
|---|---|
| `project.collections` | The collections the project needs, as `NAME[SPEC]` strings with ansible-galaxy's version specifiers (`>=1.0.0,<2.0.0`, `*`, `!=`). An `INDEX:` prefix pins one to an index |
| `project.requires_ansible` | The ansible-core versions the project targets. The resolver drops collection versions whose own `requires_ansible` doesn't overlap it, and the lock is solved for this range rather than for whatever is installed. The name follows `meta/runtime.yml` |
| `groups.<name>.collections` | Collections only some workflows need, such as tests. Installed on request |
| `overrides` | Fixes for wrong upstream metadata: a collection name replaces every requirement on that collection with the given spec, and `ignore_requires_ansible` skips the check for the listed collections |

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
- **`[sources]`**, keyed by collection name, for git, URL and local path
  sources a string can't express, like `[tool.uv.sources]`.
- **`alai export --format requirements.yml`** writes the lock as a
  `collections/requirements.yml` for AWX/Controller, ansible-builder and
  ansible-navigator, which can't run alai.
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
