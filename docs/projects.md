# Projects

Most alai commands act on a project: a directory of playbooks and the
collections they use. alai finds the project's root by walking up from the
directory it runs in: the root is where the nearest `alai.toml` is, or with
none, the nearest `ansible.cfg`, then the nearest
`collections/ansible_collections/` (see
[Finding the project root](#finding-the-project-root)). Collections are
installed into a collections directory inside it, and the root's
[`alai.toml`](alai-toml.md), the project file, says what the project needs
and holds settings for that project alone.

## Finding the project root

alai walks up from the current directory (or from `--project`,
`ALAI_PROJECT`) to the filesystem root. The root is the directory holding, in
order of preference:

1. the nearest `alai.toml`, the project file;
2. else the nearest `ansible.cfg`;
3. else the nearest `collections/ansible_collections/`;
4. else the starting directory.

A marker earlier in the list wins over a nearer one later in it, so running
from `repo/playbooks/`, which has its own `ansible.cfg`, still finds
`repo/alai.toml`. The root is the same whichever directory inside the
project alai runs from.

alai settings are only read from `alai.toml`. `ansible.cfg` only marks where
the project is.

Discovery is the same with `--no-config` and `--config-file`: those change
which settings are read, not where the project is.

## Where collections are installed

Collections install to
`<collections_dir>/ansible_collections/<namespace>/<name>`, and
`collections_dir` is `<root>/collections` by default. Ansible looks for
collections in a `collections/` directory next to the playbook it runs, so
with playbooks at the project root, `ansible-playbook` finds them with no
extra configuration.

A project that keeps its playbooks in a subdirectory sets
[`collections_dir`](alai-toml.md#projectcollections_dir) to install next
to them instead:

```toml
# repo/alai.toml
[project]
collections_dir = "playbooks/collections"
```

installs `ansible.posix` and `community.general` to:

```text
repo/
├── alai.toml
└── playbooks/
    ├── site.yml
    └── collections/
        └── ansible_collections/
            ├── ansible/
            │   └── posix/
            └── community/
                └── general/
```

`collections_dir = "playbooks/collections/ansible_collections"` gives the
same tree; it doesn't add a second `ansible_collections/` level.

## The `[project]` table

Everything that describes the project, rather than how alai runs, goes in
the [`[project]`](alai-toml.md#project) table of the project's `alai.toml`
and its sub-tables: where collections are installed, which ones the project
needs, and fixes for their metadata.

Only the project's own `alai.toml` can have `[project]`. In a user or system
file it would apply to every project, and a `--config-file` only holds
settings, so in any other file it's an error. `--collections-dir` and
`ALAI_COLLECTIONS_DIR` can still set `collections_dir` for a single run.

See [Configuration](configuration.md) for settings and how the config files
combine.

## Declaring collections

The project's `alai.toml` lists the collections the project needs, the
ansible-core versions it targets, collections only some workflows need, and
fixes for wrong metadata upstream:

```toml
# alai.toml
[project]
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "corp.tools>=1.2",
]

# Collections only some workflows need, such as tests.
[project.groups.test]
collections = ["ansible.posix", "community.crypto"]

# Fixes for wrong metadata in the collections you depend on.
[project.overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]

# Where particular collections come from: corp.tools only from the internal index.
[project.sources]
"corp.tools" = { index = "internal" }
```

Each requirement is a collection name and an optional version requirement,
such as `corp.tools>=1.2`, as in ansible-galaxy's `requirements.yml`. Where a
collection comes from is set in `[project.sources]`, which can also name
collections the project doesn't list itself, such as a dependency's
dependency. The [`alai.toml` reference](alai-toml.md#project) has every key
and the [requirement syntax](alai-toml.md#requirements).

These keys are only read from the project's own `alai.toml`, the one that
[fixes the project root](#finding-the-project-root). `--no-config` and
`--config-file` change which settings alai reads, not what the project
needs: a command that uses these keys reads them from the project's
`alai.toml` either way.

## Adding and removing collections

`alai add` adds collections to `alai.toml`, then locks and installs them,
as `alai sync` would:

```console
$ alai add cisco.ios
Resolved 4 collections in 2.27s
Prepared 4 collections in 752ms
Installed 4 collections in 50ms
 + ansible.netcommon==8.7.1
 + ansible.posix==2.2.2
 + ansible.utils==6.1.1
 + cisco.ios==11.6.0
```

A collection given by name alone is written with the version it was
locked at as its lowest, `"cisco.ios>=11.6.0"`; one given with a
[requirement](alai-toml.md#requirements), such as `'cisco.ios<12'`, is
written as given, and replaces the requirement already there for that
collection. A collection already listed and given by name alone is left as
it is. `alai remove` takes collection names and removes them, uninstalling
what nothing needs any more:

```console
$ alai remove ansible.posix
Resolved 3 collections in 139ms
Uninstalled 1 collection in 1ms
 - ansible.posix==2.1.0
```

| Option | |
|---|---|
| `--group NAME` | Add to or remove from group `NAME` rather than the project's own collections. `alai add` creates the group if it doesn't exist, and installs it |
| `--dev` | The same as `--group dev` |
| `--no-sync` | Lock, but don't install |
| `--frozen` | Only edit `alai.toml`, without locking or installing; a name alone is written as it is |
| `-U`, `-P NAME` | Upgrade every collection, or `NAME`, while locking, as [`alai lock`](#locking) does |

Only the one array changes: every other line of `alai.toml`, comments and
layout included, is left as it is. A comment on a removed collection's line
stays, on a line of its own. A new collection goes in order when the list is
already sorted, and at the end otherwise. If resolving fails, nothing is
written.

!!! note "Arrays alai can't edit"

    An array written inside an inline table, such as
    `project = { collections = [...] }`, can't be edited this way: `alai add`
    and `alai remove` say so and change nothing, and the collections need
    adding or removing by hand.

## Locking

`alai lock` resolves what the project's `alai.toml` asks for, its groups
included, to one version of each collection and its dependencies, and
writes them to `alai.lock` beside it. Commit `alai.lock` with the project,
so everyone gets the same versions:

```console
$ alai lock
Resolved 5 collections in 1.91s
```

Running it again keeps every locked version that still fits, so the lock
only changes when `alai.toml` does or a locked version no longer fits; each
change is listed:

```console
$ alai lock
Resolved 6 collections in 412ms
Added ansible.windows v3.2.0
Updated community.general v10.7.9 -> v11.1.0
```

With `-v`, each change is followed by why it happened, indented under it:
what needs an added collection, the requirement a locked version no longer meets, or, for a
collection whose dependencies are pinned exactly, the pin that conflicts:

```console
$ alai -v lock
...
Updated sivel.acd v11.9.0 -> v11.3.0
  sivel.acd 11.9.0 needs ansible.windows==2.8.0, which conflicts with ansible.windows==2.7.0 (from the project)
```

To move to newer versions anyway:

| Option | |
|---|---|
| `-U`, `--upgrade` | Choose the newest version of every collection that fits |
| `-P NAME`, `--upgrade-collection NAME` | Choose the newest version of collection `NAME` that fits; can be repeated |
| `--check` | Resolve as usual but write nothing, and exit 1 if `alai.lock` is missing or would change: `alai.toml` asks for something different, or the indexes no longer give the locked versions. A newer release alone doesn't count, since locked versions are kept while they fit. With `--offline`, it resolves from the cache |

`alai lock` also takes the index, `--system-certs` and
[`--offline`](alai-toml.md#offline) settings.

For each collection, alai chooses the newest version that every requirement
on it accepts, with these exceptions:

- A pre-release is only chosen when no stable version fits.
- A yanked version is only chosen when the project pins it exactly with
  `==`, or `alai.lock` already has it, and `alai lock` warns about it.
- When `requires_ansible` is set, a version whose own `requires_ansible`
  starts later than the project's isn't chosen; one whose range ends inside
  the project's is, with a warning.
- A collection comes from its [`[project.sources]`](alai-toml.md#projectsources)
  index, or the first configured index that has it.

When no version fits, `alai lock` lists every requirement on the
collection and where it came from, and writes nothing.

## Installing

`alai sync` makes the [collections directory](#where-collections-are-installed)
hold exactly what `alai.lock` lists: it installs what's missing, replaces any
other version, and removes collections the lock doesn't list. If
`alai.lock` is missing, or `alai.toml` has changed since it was written, it
locks first, as `alai lock` would:

```console
$ alai sync
Resolved 6 collections in 2.47s
Prepared 5 collections in 1.77s
Installed 5 collections in 88ms
 + ansible.netcommon==8.7.1
 + ansible.posix==2.2.2
 + ansible.utils==6.1.1
 + cisco.ios==11.6.0
 + community.general==10.7.9
```

On a terminal, each phase shows its progress until its summary line
replaces it: what's being resolved, what's being downloaded or extracted,
and a bar while installing (see
[`--no-progress`](configuration.md#options-that-arent-settings)).

Running it again with nothing to change connects to nothing:

```console
$ alai sync
Resolved 6 collections in 1ms
Checked 5 collections in 0ms
```

A collection is matched by the version in its `MANIFEST.json`, so one
already at its locked version is left as it is, files edited in place
included. Anything else under a locked collection's name, including a
directory without a `MANIFEST.json`, is replaced. Collections are downloaded
once, checked against `alai.lock`, and kept in the cache; installing links
their files from there, as [`link_mode`](alai-toml.md#link_mode) says.

| Option | |
|---|---|
| `--locked` | Resolve, and exit 1 without installing if `alai.lock` is missing or would change, as [`alai lock --check`](#locking) does. For CI, to catch an `alai.toml` change nobody locked |
| `--frozen` | Install from `alai.lock` as it is, without checking it against `alai.toml`. It still needs to exist |
| `--inexact` | Keep installed collections `alai.lock` doesn't list, rather than removing them. For a collections directory other tools also install to |
| `-U`, `--upgrade` | Lock again choosing the newest version of every collection that fits, then install. Can't be used with `--frozen` |
| `-P NAME`, `--upgrade-collection NAME` | The same for collection `NAME` only; can be repeated |

`alai sync` also takes the index, `--system-certs`,
[`--offline`](alai-toml.md#offline), `--collections-dir` and `--link-mode`
settings.

!!! warning "Symlinks in `ansible_collections`"

    `alai sync` never writes through a symlink, or deletes anything behind
    one; it only removes the link:

    - A collection directory that's a symlink is unlinked when it's
      replaced or removed, and what it points at is left as it is.
    - A namespace directory that's a symlink
      (`ansible_collections/community -> /elsewhere`) isn't counted as
      installed. If `alai.lock` lists a collection in that namespace, the
      link is replaced with a real directory, which hides the other
      collections it held, even with `--inexact`. Otherwise it's removed
      like anything else the lock doesn't list, unless `--inexact` is
      given.

### Groups

The project's own collections are installed with its `dev` group, if it
has one, as uv installs its `dev` dependency group. Other
[groups](alai-toml.md#projectgroupsname) are installed when asked for:

| Option | |
|---|---|
| `--group NAME` | Also install group `NAME`; can be repeated |
| `--all-groups` | Install every group |
| `--no-group NAME` | Don't install group `NAME`, even if another option asks for it; can be repeated |
| `--no-default-groups`, `--no-dev` | Don't install the `dev` group |
| `--only-group NAME`, `--only-dev` | Install only that group, without the project's own collections; can be repeated |

Every group is locked together whichever are installed, so switching
groups never changes `alai.lock`. Naming a group `alai.toml` doesn't have
is an error, except `--no-group dev` and `--no-dev`, which are always
accepted.

## Installing by hand

`alai collection install` installs collections without the project's
requirements or `alai.lock`, as `uv pip install` installs packages: it
resolves what's asked for and what that needs, keeps installed versions
that still fit, and removes nothing else. If the project has an
`alai.toml`, it's read as for any command: it marks the project, and its
settings, such as the indexes and `collections_dir`, apply. Its
`[project]` collections and `alai.lock` are neither used nor changed. `alai collection uninstall` removes the
collections named, and only them:

```console
$ alai collection install cisco.ios
Resolved 3 collections in 2.20s
Prepared 3 collections in 809ms
Installed 3 collections in 65ms
 + ansible.netcommon==8.7.1
 + ansible.utils==6.1.1
 + cisco.ios==11.6.0
$ alai collection uninstall cisco.ios
Uninstalled 1 collection in 8ms
 - cisco.ios==11.6.0
```

When every collection asked for is already installed at a version that
fits, `install` says `Checked` and connects to nothing. `-U` and `-P NAME`
upgrade, as for [`alai lock`](#locking). A collection that isn't installed
is skipped by `uninstall` with a warning.

They install to the [collections directory](#where-collections-are-installed)
of the project alai finds, whether it has an `alai.toml`, an `ansible.cfg`
or a `collections/ansible_collections/`. With none of those, they need
`--collections-dir` (or `ALAI_COLLECTIONS_DIR`, or the setting) to say
where, rather than making a `collections/` wherever alai happens to run.

!!! note "`alai sync` undoes them"

    In a project with an `alai.lock`, `alai sync` makes the collections
    directory match the lock again, so it removes what was installed by hand
    and puts back what was uninstalled, unless it's run with `--inexact`.
    To keep a collection, [add it](#adding-and-removing-collections)
    instead.
