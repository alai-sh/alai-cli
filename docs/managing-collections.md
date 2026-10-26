# Managing collections

A project's collections are managed with four commands:

| Command | What it does |
|---|---|
| [`alai add`, `alai remove`](#adding-and-removing-collections) | Change what `alai.toml` asks for, then lock and install |
| [`alai lock`](#locking) | Resolve what `alai.toml` asks for to exact versions in `alai.lock` |
| [`alai sync`](#installing) | Make the collections directory hold exactly what `alai.lock` lists |

`alai.toml` says what the project needs, as loosely or tightly as you like,
such as `community.general>=10`. `alai.lock` records the exact versions
chosen, and every collection they need in turn. `alai sync` installs from
the lock, so everyone with the same `alai.lock` gets the same collections.
`add` and `remove` edit `alai.toml`, then lock and sync for you, so day to
day they're often the only commands you need.

## Common tasks

### Starting a project

Create an `alai.toml`, which marks the project root, and add the
collections you need:

```console
$ touch alai.toml
$ alai add cisco.ios 'community.general<11'
```

[Adding collections to a new project](casts/add.cast){ .alai-cast }

Commit `alai.toml` and `alai.lock`, and ignore the collections directory,
as `alai sync` creates it:

```text
# .gitignore
/collections/
```

### After cloning or pulling

Run `alai sync`. It installs what's missing, replaces what's at another
version, and removes what `alai.lock` doesn't list:

```console
$ alai sync
Resolved 4 collections in 0ms
Uninstalled 1 collection in 0ms
Installed 1 collection in 6ms
 - ansible.posix==2.1.0
 + ansible.posix==2.2.2
```

When nothing has changed, it only checks what's installed and is done in
a fraction of a second, so it's safe to run before every task.

### Adding a collection

```console
$ alai add ansible.posix                    # written as "ansible.posix>=2.2.2"
$ alai add 'community.general>=10,<12'      # written as given
$ alai add 'ansible.windows<3.9'            # replaces its existing requirement
```

A name alone is written with the version just locked as its lower bound,
so a later lock never goes back before it. A name that's already in
`alai.toml` keeps its requirement. To add a collection from a directory, a
tarball or a git repository instead of an index, see
[Collection sources](collection-sources.md).

### Removing a collection

[Removing a collection](casts/remove.cast){ .alai-cast }

`alai remove` uninstalls the collection and whatever only it needed.
Dependencies another collection still needs stay installed. Use the name of
the collection to remove without any of the custom source details. For example

```console
$ alai add ./some-dir/ns/name
$ alai remove ns.name
```

### Collections only tests need

Put them in a [group](alai-toml.md#projectgroupsname), such as `dev` or
`test`:

```console
$ alai add --dev ansible.utils
$ alai add --group test community.docker
```

The `dev` group is installed with the project's collections by every
`alai sync`. Other groups are only installed when asked for:

```console
$ alai sync --group test
```

!!! warning "A plain sync removes other groups"

    `alai sync`, and the sync `alai add` and `alai remove` run, make the
    collections directory hold only the project's collections and `dev`.
    A `test` group installed earlier is uninstalled again. Give
    `alai sync` `--group test` each time, or `--inexact` to leave it
    installed, and run `alai sync --group test` again after an `alai add`
    or `alai remove`.

Every group is locked together, so installing a different set of groups
never changes `alai.lock`.

### Upgrading

Locked versions are kept while they still fit, so collections only change
when you ask. To see what an upgrade would change without writing
anything:

```console
$ alai lock --upgrade --check
Resolved 4 collections in 174ms
Updated ansible.posix v2.1.0 -> v2.2.2
error: alai.lock is out of date; run alai lock
```

Then upgrade and install:

```console
$ alai sync --upgrade-collection cisco.ios   # just cisco.ios
$ alai sync --upgrade-group test             # what the test group lists
$ alai sync --upgrade                        # everything
```

`alai lock` takes the same options to update `alai.lock` without
installing:

[Upgrading one collection, then the rest](casts/lock.cast){ .alai-cast }

An upgrade still stays within what `alai.toml` asks for. To move past an
upper bound, such as `<11`, change the requirement with `alai add`.

### In CI

```console
$ alai sync --locked --group test
```

`--locked` fails, without installing anything, when `alai.lock` is
missing or doesn't match `alai.toml`. A change to `alai.toml` that nobody
locked fails the build rather than being resolved in CI:

```console
$ alai sync --locked
Resolved 4 collections in 242ms
alai.toml has changed since alai.lock was made:
  project.groups.dev.collections: changed ansible.utils>=6.1.1 to ansible.utils>=6
error: alai.lock is out of date, and --locked was given; run alai lock
```

To only check the lock, without installing, run `alai lock --check`.

### A merge conflict in `alai.lock`

Don't edit `alai.lock` by hand. Resolve any conflict in `alai.toml`, take
either side of `alai.lock`, and lock again:

```console
$ git checkout --theirs alai.lock
$ alai lock
Resolved 5 collections in 635ms
Added ansible.windows v3.9.2
```

`alai lock` keeps every version in that lock that still fits and resolves
only what the merged `alai.toml` adds or changes.

### Editing `alai.toml` by hand

Some changes have no command, such as
[overrides](alai-toml.md#projectoverrides) or
[sources](alai-toml.md#projectsources) for a dependency. Edit `alai.toml`
and run `alai sync` or `alai lock`. It sees the change and locks again,
keeping every locked version that still fits.

### Restoring an edited collection

`alai sync` checks an installed collection's version, not its files, so an
edited collection isn't replaced. To put back a clean copy:

```console
$ alai sync --reinstall-collection ansible.posix
```

With the `hardlink` or `symlink` [link mode](alai-toml.md#link_mode), the
edit also reached the cache, so clear the cache first (see the warning
there).

## Adding and removing collections

[`alai add`](cli.md#alai-add) and [`alai remove`](cli.md#alai-remove)
take the same options as [`alai sync`](#installing) for upgrading and
reinstalling. Their own are:

| Option | |
|---|---|
| `--group NAME` | Add to or remove from group `NAME` rather than the project's own collections. `alai add` creates the group if it doesn't exist, and installs it |
| `--dev` | The same as `--group dev` |
| `--no-sync` | Lock, but don't install or uninstall |
| `--frozen` | Only edit `alai.toml`, without locking or installing. A name alone is written as it is |
| `--locked` | Exit 1, writing nothing, if `alai.lock` is missing or the change would alter it. Adding what's already there goes on to install, so a setup script can run it every time |

Adding a collection keeps every other locked version that still fits.
`-P NAME` upgrades `NAME` along the way, and it needn't be one being
added:

[Upgrading a locked collection while adding another](casts/add-upgrade.cast){ .alai-cast }

!!! warning "Check the diff"

    alai only means to change the entries you name, but it may change
    something else in `alai.toml` by mistake. Check the diff before
    committing.

## Locking

[`alai lock`](cli.md#alai-lock) resolves what `alai.toml` asks for, its
groups included, to one version of each collection and its dependencies.
Only the changes are listed.

For each collection, alai chooses the newest version that every
requirement on it accepts, with these exceptions:

- A pre-release is only chosen when no stable version fits.
- A yanked version is only chosen when the project pins it exactly with
  `==`, or `alai.lock` already has it, and `alai lock` warns about it.
- When `requires_ansible` is set, a version whose own `requires_ansible`
  starts later than the project's isn't chosen. A version whose range ends
  inside the project's is used, with a warning.
- A collection comes from its [`[project.sources]`](alai-toml.md#projectsources)
  index, or the first configured index that has it.

When no version fits, `alai lock` lists every requirement on the
collection and where it came from, and writes nothing.

`--check` resolves as usual but writes nothing, and exits 1 if `alai.lock`
is missing or would change. A newer release alone doesn't count, as locked
versions are kept while they fit. On failure, it lists what would change,
and what in `alai.toml`, or in a
[directory source's](collection-sources.md#a-directory) `galaxy.yml`,
`MANIFEST.json` or `meta/runtime.yml`, differs from what was locked. With
`--offline`, it resolves from the cache.

## Installing

[`alai sync`](cli.md#alai-sync) makes the
[collections directory](projects.md#where-collections-are-installed) hold
exactly what `alai.lock` lists. If `alai.lock` is missing, or `alai.toml`
has changed since it was written, it locks first, as `alai lock` would:

[Installing a project's collections](casts/sync.cast){ .alai-cast }

An installed collection is matched by the version in its `MANIFEST.json`,
not its files. `--reinstall-collection NAME`, or `--reinstall` for every
collection, installs it again regardless. `--inexact` leaves collections
the lock doesn't list where they are.

!!! warning "Symlinks in `ansible_collections`"

    `alai sync` never writes through a symlink, or deletes anything behind
    one. It only removes the link:

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
| `--group NAME` | Also install group `NAME`. Can be repeated |
| `--all-groups` | Install every group |
| `--no-group NAME` | Don't install group `NAME`, even if another option asks for it. Can be repeated |
| `--no-default-groups`, `--no-dev` | Don't install the `dev` group |
| `--only-group NAME`, `--only-dev` | Install only that group, without the project's own collections. Can be repeated |

Naming a group `alai.toml` doesn't have is an error, except
`--no-group dev` and `--no-dev`, which are always accepted.

## Installing outside the lock

To try a collection without adding it to the project, or to install into
a directory that isn't a project, see
[Installing by hand](installing-by-hand.md). Anything installed that way
into the project's collections directory is removed by the next
`alai sync`, unless it's run with `--inexact`.
[`alai collection list`](installing-by-hand.md#listing) shows what's
installed and what doesn't match `alai.lock`.
