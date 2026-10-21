# Installing by hand

`alai collection install` and `alai collection uninstall` change a
collections directory directly, as `uv pip install` and `uv pip uninstall`
change a virtual environment. They don't read or write the project's
requirements or `alai.lock`.

## Compared with `alai sync`

| | [`add`, `remove`, `lock`, `sync`](managing-collections.md) | `collection install`, `collection uninstall` |
|---|---|---|
| What's installed comes from | `alai.lock`, resolved from `alai.toml` | The command line |
| Recorded for the next person | Yes, in `alai.toml` and `alai.lock` | No |
| Removes what wasn't asked for | Yes: `sync` makes the directory match the lock | Never: only what's named is uninstalled |
| Versions already installed | Replaced if the lock says otherwise | Kept while they fit what's asked for |
| Needs a project | Yes, with an `alai.toml` | No: any [project marker](projects.md#finding-the-project-root), or `--collections-dir` |

Use the project commands for anything the project needs, so it's recorded
and the same for everyone. Installing by hand suits trying a collection
before deciding to add it, a directory that isn't a project, such as a
shared collections path, or one other tools also install to.

!!! note "`alai sync` undoes them"

    In a project with an `alai.lock`, `alai sync` makes the collections
    directory match the lock again, so it removes what was installed by hand
    and puts back what was uninstalled, unless it's run with `--inexact`.
    To keep a collection, [add it](managing-collections.md#adding-and-removing-collections)
    instead.

## Installing

`alai collection install` resolves what's asked for and what that needs,
keeps installed versions that still fit, and removes nothing else:

[Installing into a project's collections directory](casts/collection-install.cast){ .alai-cast }

When every collection asked for is already installed at a version that
fits, it says `Checked` and connects to nothing. `-U` and `-P NAME` upgrade,
as for [`alai lock`](managing-collections.md#locking). `--reinstall`
reinstalls everything it resolves, what's asked for and what that needs,
even those already at the version chosen, and
`--reinstall-collection NAME` reinstalls only `NAME`, as for
[`alai sync`](managing-collections.md#installing). Requirements take the
same [syntax](alai-toml.md#requirements) as in `alai.toml`, such as
`'cisco.ios>=11'`.

A requirement with a `/` in it, such as `../corp.dev` or `./corp.dev`, is a
directory holding a collection's `galaxy.yml`, or a built collection's
`MANIFEST.json`, as `uv pip install ./lib` takes a directory. A source is
built and copied in, and a built one copied as it is, again every time
it's asked for, and what it depends on is resolved from the indexes.
`-e DIR` (`--editable`) links it instead, so edits there take effect
without installing again; it can be repeated. A name alone, such as
`corp.dev`, is always a collection name, even when a directory of that name
is beside you. See
[Collection sources](collection-sources.md#a-directory)
for what's copied.

If the project has an `alai.toml`, it's read as for any command: it marks
the project, and its settings, such as the indexes and `collections_dir`,
apply. Its `[project]` collections and `alai.lock` are neither used nor
changed.

## Uninstalling

`alai collection uninstall` removes the collections named, and only them:
what they depend on stays installed. A collection that isn't installed is
skipped with a warning.

[Uninstalling from a project's collections directory](casts/collection-uninstall.cast){ .alai-cast }

## Where they install

They work on the
[collections directory](projects.md#where-collections-are-installed) of the
project alai finds from the current directory, whether it's marked by an
`alai.toml`, an `ansible.cfg` or a `collections/ansible_collections/`, so
running from a subdirectory still installs into the project's:

[Installing from a directory inside the project](casts/collection-install-subdirectory.cast){ .alai-cast }

Unless it's `collections/` in the current directory, they first say which
directory they're using, as `uv pip` names the environment it uses.

With no project, they need `--collections-dir` (or `ALAI_COLLECTIONS_DIR`,
or the `collections_dir` setting in a user or system config file) to say
where, rather than making a `collections/` wherever alai happens to run:

[Installing outside a project](casts/collection-install-outside.cast){ .alai-cast }

## Collections whose version can't be read

An installed collection's version is read from its `MANIFEST.json`. One
without, such as a source checkout with only a `galaxy.yml`, has no version
alai can match, so `install` replaces it when it installs that collection,
whether it was asked for or is needed by one that was.

`--keep-unversioned` leaves such a collection as it is instead, counting it
as whatever version is asked for, as `ansible-galaxy` does without
`--force`. One asked for isn't looked up at all, so it needn't be on any
index; one that's only needed by another is still looked up, which decides
what else it needs, but its directory isn't touched. It can't be used with
`--reinstall` or `--reinstall-collection`, which would replace it.
