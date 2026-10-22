# Installing by hand

`alai collection install` and `alai collection uninstall` change a
collections directory directly, as `uv pip install` and `uv pip uninstall`
change a virtual environment. They don't read or write the project's
requirements or `alai.lock`. `alai collection list` shows what's in the
collections directory, however it got there, and how that compares with
`alai.lock`, as `uv pip list` lists an environment.

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

A requirement ending in `.tar.gz`, such as `corp-net-2.1.0.tar.gz`, is a
built collection's tarball, and an `http://` or `https://` URL one to
download, as `uv pip install` takes an archive. It's installed as an
index's artifact is, and left as it is when the same tarball is already
installed from there; a different one, of the same version or not, replaces
it.

A requirement starting `git+`, such as
`git+https://github.com/corp/windows.git@v3.0.0`, is a git repository, as
`uv pip install` takes one: `@<rev>` names a branch, tag or full commit,
and `#subdirectory=<dir>` the collection's directory in it, with
`&version=<version>` for a collection whose `galaxy.yml` has a placeholder
instead (see
[A version that isn't one](collection-sources.md#a-version-that-isnt-one)). The commit is
fetched and the collection installed from it as a directory is, and left as
it is when that commit is already installed. See
[Collection sources](collection-sources.md#a-git-repository).

If the project has an `alai.toml`, it's read as for any command: it marks
the project, and its settings, such as the indexes and `collections_dir`,
apply. Its `[project]` collections and `alai.lock` are neither used nor
changed.

## Uninstalling

`alai collection uninstall` removes the collections named, and only them:
what they depend on stays installed. A collection that isn't installed is
skipped with a warning. Name the collection, such as `corp.win`, even one
installed from a directory, a tarball or a git repository; given one of
those instead, it says which installed collection came from it.

[Uninstalling from a project's collections directory](casts/collection-uninstall.cast){ .alai-cast }

## Listing

`alai collection list` lists every collection in the collections
directory, by name, with its version. A collection from a directory, a
tarball or a git repository also shows where it came from, and an editable
one the directory it links to; one from an index shows nothing more, as
`uv pip list` shows nothing for a package from PyPI.

In a project with an `alai.lock`, a marker before each name says how it
compares with the lock:

| Marker | Means | The row says |
|---|---|---|
| `✓` | Installed as `alai.lock` has it | Nothing more |
| `?` | `alai.lock` doesn't list it, such as one installed by hand | `not in alai.lock` |
| `≠` | Installed, but not as `alai.lock` has it | What the lock has instead: another version, another source, or another commit of the same repository; or that its source has changed since it was copied |
| `✗` | Locked, but not installed | The locked version |

[Listing a project's collections against its lock](casts/collection-list.cast){ .alai-cast }

`alai sync` makes the directory match the lock, which changes every row not
marked `✓`. A collection only a group that isn't installed by default
needs isn't listed when it's missing, as a plain `alai sync` wouldn't
install it. A namespace that's a link to somewhere else is listed as
`ns.*`, as Ansible loads everything behind it.

Without an `alai.lock`, there are no markers, only the collections. On a
terminal, a row too wide for it puts where the collection came from on a
line of its own; piped, each collection is one line, whole.

### For scripts

`--output-format json` gives the same as JSON, with each collection's path
and source in full and what the lock has for it. The directory it reads
and the hint go to stderr, as they do for text. In the project above, with
`corp.dev` added as an editable directory:

```json
{
  "collections": [
    {
      "editable": false,
      "lock": {"source": {"index": "https://index.alai.sh/"}, "status": "different", "version": "2.2.2"},
      "name": "ansible.posix",
      "path": "/home/me/project/collections/ansible_collections/ansible/posix",
      "source": null,
      "version": "2.1.0"
    },
    {
      "editable": false,
      "lock": {"source": {"index": "https://index.alai.sh/"}, "status": "missing", "version": "6.1.1"},
      "name": "ansible.utils",
      "path": null,
      "source": null,
      "version": null
    },
    {
      "editable": false,
      "lock": {"source": null, "status": "unlocked", "version": null},
      "name": "community.docker",
      "path": "/home/me/project/collections/ansible_collections/community/docker",
      "source": null,
      "version": "4.8.1"
    },
    {
      "editable": true,
      "lock": {"source": {"editable": "../corp.dev"}, "status": "locked", "version": "0.3.0"},
      "name": "corp.dev",
      "path": "/home/me/project/collections/ansible_collections/corp/dev",
      "source": {"directory": "/home/me/corp.dev"},
      "version": "0.3.0"
    }
  ],
  "collections_dir": "/home/me/project/collections",
  "lock": "/home/me/project/alai.lock",
  "namespace_links": [],
  "v": 1
}
```

| Field | |
|---|---|
| `collections` | Every collection installed, and every one locked but not installed that a plain `alai sync` would install, sorted by name |
| `collections_dir` | The collections directory read, absolute; collections are in `ansible_collections/` inside it |
| `lock` | The path of the `alai.lock` compared with, or `null` without one |
| `namespace_links` | Each namespace that's a link to somewhere else: its `namespace`, the link's `path`, and its `target` as the link gives it |
| `v` | The format's version, which changes if a field is removed or changes meaning |

Each collection has:

| Field | |
|---|---|
| `editable` | Whether it's a link to a directory, so edits there are seen |
| `lock` | How it compares with `alai.lock`, or `null` without one: its `status`, and the `version` and `source` the lock has for it, `null` when the lock doesn't list it |
| `name` | Its full name, `namespace.name` |
| `path` | Its directory, absolute, or `null` if it isn't installed |
| `source` | Where it was installed from, or `null` for an index's: `directory` or `path` for a directory or a tarball on disk, absolute; `url` for a tarball's URL; `git`, `commit` and, if set, `subdirectory` for a git repository; `directory` for an editable link's target |
| `version` | Its version, or `null` if it isn't installed or its version can't be read |

`lock.status` is one of:

| Status | Marker | |
|---|---|---|
| `locked` | `✓` | Installed as `alai.lock` has it |
| `unlocked` | `?` | `alai.lock` doesn't list it |
| `different` | `≠` | Installed, but at another version, from another source, or changed at its source since it was copied |
| `missing` | `✗` | Locked, but not installed |

`lock.source` is the collection's source as `alai.lock` records it: `index`
with the index's URL, or the `[project.sources]` entry it was locked from,
with paths as `alai.toml` gives them, relative to the project, and a git
repository's `commit`.

The keys are sorted here to make them easier to find; alai doesn't print
them in any particular order.

## Where they install

They work on the
[collections directory](projects.md#where-collections-are-installed) of the
project alai finds from the current directory, whether it's marked by an
`alai.toml`, an `ansible.cfg` or a `collections/ansible_collections/`, so
running from a subdirectory still installs into the project's:

[Installing from a directory inside the project](casts/collection-install-subdirectory.cast){ .alai-cast }

Unless it's `collections/` in the current directory, they first say which
directory they're using, as `uv pip` names the environment it uses.
`collection list` always says, as what it lists is only what's in that
directory.

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
