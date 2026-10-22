# Managing collections

A project's collections are managed with four commands, the same as uv
manages a Python project's dependencies:

| Command | What it does |
|---|---|
| [`alai add`, `alai remove`](#adding-and-removing-collections) | Change what `alai.toml` asks for, then lock and install |
| [`alai lock`](#locking) | Resolve what `alai.toml` asks for to exact versions in `alai.lock` |
| [`alai sync`](#installing) | Make the collections directory hold exactly what `alai.lock` lists |

`alai.toml` says what the project needs, as loosely or tightly as you like
(`community.general>=10`). `alai.lock` records the exact versions that
satisfied it when it was last resolved, and every collection they need in
turn. `alai sync` installs from the lock, so everyone with the same
`alai.lock` gets the same collections. `add` and `remove` edit `alai.toml`
and then lock and sync for you, so day to day they're often the only
commands needed.

## Recommended workflows

### Starting a project

An `alai.toml` marks the project's root, so create one in the directory
holding your playbooks, then add the collections they use:

```console
$ touch alai.toml
$ alai add ansible.posix 'community.general>=10,<12'
```

[Adding collections to a new project](casts/add.cast){ .alai-cast }

`alai add` writes the requirements into `alai.toml`, resolves them into
`alai.lock`, and installs them into `collections/` beside it. A collection
given by name alone is written with the version just locked as its lowest,
`"ansible.posix>=2.2.2"`, so later locks never go back past it.

### In version control

Commit both `alai.toml` and `alai.lock`, and ignore the collections
directory, which `alai sync` can always recreate:

```text
# .gitignore
/collections/
```

### After pulling changes

Run `alai sync`. It installs what `alai.lock` lists, replaces what's at the
wrong version, and removes what the lock no longer lists. If someone changed
`alai.toml` without locking, it locks first.

### In CI

```console
$ alai sync --locked
```

`--locked` fails, without installing, when `alai.lock` is missing or doesn't
match `alai.toml`, so a change to `alai.toml` that nobody locked fails the
build instead of being resolved there. Add `--group NAME` for collections
only CI needs, such as a `test` group (see [Groups](#groups)). To only check
the lock, without installing anything, run `alai lock --check`.

### Upgrading

Locked versions are kept while they still fit, so collections only change
when you ask:

```console
$ alai lock --upgrade-collection cisco.ios   # just cisco.ios
$ alai lock --upgrade-group test             # what the test group lists
$ alai lock --upgrade                        # everything
$ alai sync
```

[Upgrading one collection, then the rest](casts/lock.cast){ .alai-cast }

Each change is listed, `-v` says why each happened, and `git diff alai.lock`
shows the whole change before it's committed. `alai sync -U` and
`alai sync -P NAME` lock and install in one step.

### Changing `alai.toml` by hand

`alai.toml` can be edited directly, for anything `alai add` and
`alai remove` don't do, such as [overrides](alai-toml.md#projectoverrides)
or [sources](alai-toml.md#projectsources). The next `alai sync` or
`alai lock` sees the change and locks again, keeping every locked version
that still fits.

## Adding and removing collections

`alai add` adds collections to `alai.toml`, then locks and installs them,
as `alai sync` would. `alai remove` takes collection names and removes them,
uninstalling what nothing needs any more:

[Removing a collection](casts/remove.cast){ .alai-cast }

A collection given by name alone is written with the version it was
locked at as its lower bound version, `"cisco.ios>=11.6.0"`; one given with a
[requirement](alai-toml.md#requirements), such as `'cisco.ios<12'`, is
written as given, and replaces the requirement already there for that
collection. A collection already listed and given by name alone is left as
it is.

| Option | |
|---|---|
| `--group NAME` | Add to or remove from group `NAME` rather than the project's own collections. `alai add` creates the group if it doesn't exist, and installs it |
| `--dev` | The same as `--group dev` |
| `--no-sync` | Lock, but don't install |
| `--frozen` | Only edit `alai.toml`, without locking or installing; a name alone is written as it is |
| `--locked` | Resolve, and exit 1 without writing anything if `alai.lock` is missing or the change would alter it. An `alai add` of what's already there goes on to install, so a setup script can run it every time, in CI say. Can't be used with `--frozen` |
| `-U`, `-P NAME`, `--upgrade-group NAME` | Upgrade every collection, `NAME`, or what group `NAME` lists, while locking, as [`alai lock`](#locking) does |
| `--reinstall`, `--reinstall-collection NAME` | Reinstall every collection, or `NAME`, while installing, as [`alai sync`](#installing) does |

Adding a collection keeps every other locked version that still fits, even
when a newer one is out. `-P NAME` upgrades `NAME` along the way; it takes
the name of the collection to upgrade, which needn't be one being added:

[Upgrading a locked collection while adding another](casts/add-upgrade.cast){ .alai-cast }

Only the one array changes: every other line of `alai.toml`, comments and
layout included, is left as it is. A comment on a removed collection's line
stays, on a line of its own. A new collection goes in order when the list is
already sorted, and at the end otherwise. If resolving fails, nothing is
written.

!!! warning "Check the diff"

    Nothing outside the one array changes, and the result is always valid
    TOML, but comments and layout inside it can move: a comment on its own
    line can end up above a different entry, a trailing comma can be added,
    and entries that shared a line can be joined. Review
    `git diff alai.toml` after `alai add` or `alai remove`, especially in a
    list with comments.

!!! note "Arrays alai can't edit"

    An array written inside an inline table, such as
    `project = { collections = [...] }`, can't be edited this way: `alai add`
    and `alai remove` say so and change nothing, and the collections need
    adding or removing by hand.

## Collections from a directory

`alai add` also takes a directory holding a collection, such as a checkout
of one you're working on, with a `/` in it so it isn't read as a collection
name. It adds the collection by name and takes it from that directory in
[`[project.sources]`](alai-toml.md#projectsources); `--editable` links it
rather than copying it:

```console
$ alai add ../corp.dev
Resolved 2 collections in 621ms
Prepared 1 collection in 602ms
Installed 2 collections in 3ms
 + ansible.posix==2.2.2
 + corp.dev==0.3.0 (from ~/corp.dev)
```

`alai add` of the same directory with or without `--editable` switches
between the two. `alai remove corp.dev` removes its `[project.sources]`
entry too, once no group lists it; an `{ index = ... }` entry stays, as it
can apply to what other collections depend on.

`alai add` takes a built collection's tarball the same way, by path, such
as `dist/corp-net-2.1.0.tar.gz`, or by `https://` URL, and a git
repository as `git+<url>`, such as
`git+https://github.com/corp/windows.git@v3.0.0`; `alai remove` removes
their entries too.

!!! note "Remove by name"

    `alai remove` takes the collection's name, such as
    `alai remove corp.win`, never the directory, tarball or URL it was
    added from. Given one of those, it says which collection
    `[project.sources]` takes from it.

How a directory, a tarball or a git repository is installed, when it's
installed again, and when to use each is in
[Collection sources](collection-sources.md).

## Locking

`alai lock` resolves what the project's `alai.toml` asks for, its groups
included, to one version of each collection and its dependencies, and
writes them to `alai.lock` beside it:

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
what needs an added collection, the requirement a locked version no longer
meets, or, for a collection whose dependencies are pinned exactly, the pin
that conflicts:

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
| `--upgrade-group NAME` | The same for every collection group `NAME` lists, but not what they depend on; can be repeated. A collection the group shares with the project, or another group, is upgraded for all of them, since it's locked once |
| `--check` | Resolve as usual but write nothing, and exit 1 if `alai.lock` is missing or would change: `alai.toml` asks for something different, or the indexes no longer give the locked versions. A newer release alone doesn't count, since locked versions are kept while they fit. On failure it lists the collections that would change and what in `alai.toml`, or in a [directory's](collection-sources.md#a-directory) `galaxy.yml` or `MANIFEST.json` and `meta/runtime.yml`, differs from what was locked. With `--offline`, it resolves from the cache |

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

`alai sync` makes the
[collections directory](projects.md#where-collections-are-installed) hold
exactly what `alai.lock` lists: it installs what's missing, replaces any
other version, and removes collections the lock doesn't list. If
`alai.lock` is missing, or `alai.toml` has changed since it was written, it
locks first, as `alai lock` would:

[Installing a project's collections](casts/sync.cast){ .alai-cast }

On a terminal, each phase shows its progress until its summary line
replaces it: what's being resolved, what's being downloaded or extracted,
and a bar while installing (see
[`--no-progress`](configuration.md#options-that-arent-settings)). Running it
again with nothing to change connects to nothing, and says `Checked`.

A collection is matched by the version in its `MANIFEST.json`, and where it
came from, so one already at its locked version is left as it is, files
edited in place included; to put back a collection you've edited, run
`alai sync --reinstall-collection NAME`. Anything else under a locked collection's name, including
a directory without a `MANIFEST.json`, is replaced. Collections are
downloaded once, checked against `alai.lock`, and kept in the cache;
installing links their files from there, as
[`link_mode`](alai-toml.md#link_mode) says.

| Option | |
|---|---|
| `--locked` | Resolve, and exit 1 without installing if `alai.lock` is missing or would change, saying why, as [`alai lock --check`](#locking) does. For CI, to catch an `alai.toml` change nobody locked |
| `--frozen` | Install from `alai.lock` as it is, without checking it against `alai.toml`. It still needs to exist |
| `--inexact` | Keep installed collections `alai.lock` doesn't list, rather than removing them. For a collections directory other tools also install to |
| `-U`, `--upgrade` | Lock again choosing the newest version of every collection that fits, then install. Can't be used with `--frozen` |
| `-P NAME`, `--upgrade-collection NAME` | The same for collection `NAME` only; can be repeated |
| `--upgrade-group NAME` | The same for what group `NAME` lists, as for [`alai lock`](#locking); can be repeated |
| `--reinstall` | Reinstall every collection, even those already at their locked version, from the cache. Shown as `~ NAME==VERSION` |
| `--reinstall-collection NAME` | The same for collection `NAME` only; can be repeated. One that isn't locked is ignored |

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

## Installing outside the lock

To try a collection without adding it to the project, or to install into a
directory that isn't a project, see [Installing by hand](installing-by-hand.md).
Anything installed that way into the project's collections directory is
removed by the next `alai sync`, unless it's run with `--inexact`.
