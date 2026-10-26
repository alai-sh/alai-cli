# Installing by hand

Using `alai sync` and `alai add|remove` is nice when you are dealing with a
lock file or an alai project, but there are times when you might want to
install, uninstall, or list collections manually. The `alai collection ...`
set of commands can be used for this purpose and do not require an `alai.toml`
or `alai.lock`.

## Compared with `alai sync`

| | [`add`, `remove`, `lock`, `sync`](managing-collections.md) | `collection install`, `collection uninstall` |
|---|---|---|
| Collection names from | `alai.lock`, resolved from `alai.toml` | The command line |
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
    and puts back what was uninstalled. To keep a collection,
    [add it](managing-collections.md#adding-and-removing-collections)
    instead.

## Installing

[`alai collection install`](cli.md#alai-collection-install) resolves what's
asked for and what that needs, keeps installed versions that still fit those
requirements, and removes nothing else:

[Installing into a project's collections directory](casts/collection-install.cast){ .alai-cast }

`-U` upgrades everything it resolves to the newest versions that fit, and
`-P NAME` upgrades only `NAME`. `--reinstall` reinstalls everything it
resolves, including dependencies, whereas `--reinstall-collection NAME`
reinstalls only `NAME`. Requirements are in the same
[format](alai-toml.md#requirements) as in `alai.toml`, such as
`'cisco.ios>=11'`. See
[Collection sources](collection-sources.md) for the sources you can install
from and how to give them.

The `alai.toml`, if present, is read to find the `collections_dir` to install
the collections to as well as any other alai settings relevant for
installation. Its `[project]` collections and `alai.lock` are neither used
nor changed.

## Uninstalling

[`alai collection uninstall`](cli.md#alai-collection-uninstall) removes the
collections named, and only those collections. Their dependencies stay
installed. A collection that isn't installed is skipped with a warning.

[Uninstalling from a project's collections directory](casts/collection-uninstall.cast){ .alai-cast }

## Listing

[`alai collection list`](cli.md#alai-collection-list) lists every
collection in the collections directory, by name, with its version. A
collection installed from a directory, a tarball or a git repository also
shows where it came from.

In a project with an `alai.lock`, a marker lets you know if the installed
collection matches the lock entry:

| Marker | Means | The row says |
|---|---|---|
| `✓` | Installed as `alai.lock` has it | Nothing more |
| `?` | `alai.lock` doesn't list it, such as one installed by hand | `not in alai.lock` |
| `≠` | Installed, but not as `alai.lock` has it | What the lock has instead: another version, another source, or another commit of the same repository; or that its source has changed since it was copied |
| `✗` | Locked, but not installed | The locked version |

[Listing a project's collections against its lock](casts/collection-list.cast){ .alai-cast }

`alai sync` makes the directory match the lock, which changes every row not
marked `✓`. Without an `alai.lock`, there are no markers, only the collections
present.

### For scripts

`--output-format json` gives the same as JSON, with each collection's path
and source in full and details around its lock status. The following is the
JSON output for the project above with `corp.dev` added as an editable
directory, trimmed to one collection of each status:

```json
{
  "v": 1,
  "collections_dir": "/home/me/project/collections",
  "lock": "/home/me/project/alai.lock",
  "collections": [
    {
      "name": "ansible.posix",
      "version": "2.1.0",
      "path": "/home/me/project/collections/ansible_collections/ansible/posix",
      "source": null,
      "editable": false,
      "lock": {
        "status": "different",
        "version": "2.2.2",
        "source": {
          "index": "https://index.alai.sh/"
        }
      }
    },
    {
      "name": "ansible.utils",
      "version": null,
      "path": null,
      "source": null,
      "editable": false,
      "lock": {
        "status": "missing",
        "version": "6.1.1",
        "source": {
          "index": "https://index.alai.sh/"
        }
      }
    },
    {
      "name": "community.docker",
      "version": "4.8.1",
      "path": "/home/me/project/collections/ansible_collections/community/docker",
      "source": null,
      "editable": false,
      "lock": {
        "status": "unlocked",
        "version": null,
        "source": null
      }
    },
    {
      "name": "corp.dev",
      "version": "0.3.0",
      "path": "/home/me/project/collections/ansible_collections/corp/dev",
      "source": {
        "directory": "/home/me/corp.dev"
      },
      "editable": true,
      "lock": {
        "status": "locked",
        "version": "0.3.0",
        "source": {
          "editable": "../corp.dev"
        }
      }
    }
  ],
  "namespace_links": []
}
```

| Field | |
|---|---|
| `v` | The format's version, which changes if a field is removed or changes meaning |
| `collections_dir` | The collections directory read, absolute; collections are in `ansible_collections/` inside it |
| `lock` | The path of the `alai.lock` compared with, or `null` without one |
| `collections` | Every collection installed, and every one locked but not installed that a plain `alai sync` would install, sorted by name |
| `namespace_links` | Each namespace that's a link to somewhere else: its `namespace`, the link's `path`, and its `target` as the link gives it |

Each collection has:

| Field | |
|---|---|
| `name` | Its full name, `namespace.name` |
| `version` | Its version, or `null` if it isn't installed or its version can't be read |
| `path` | Its directory, absolute, or `null` if it isn't installed |
| `source` | Where it was installed from, or `null` for an index's: `directory` or `path` for a directory or a tarball on disk, absolute; `url` for a tarball's URL; `git`, `commit` and, if set, `subdirectory` for a git repository; `directory` for an editable link's target |
| `editable` | Whether it's a link to a directory, so edits there are seen |
| `lock` | How it compares with `alai.lock`, or `null` without one: its `status`, and the `version` and `source` the lock has for it, `null` when the lock doesn't list it |

`lock.status` is one of:

| Status | Marker | |
|---|---|---|
| `locked` | `✓` | Installed as `alai.lock` has it |
| `unlocked` | `?` | `alai.lock` doesn't list it |
| `different` | `≠` | Installed, but at another version, from another source, or changed at its source since it was copied |
| `missing` | `✗` | Locked, but not installed |

## Where they install

The `alai collection ...` commands work on the
[collections directory](projects.md#where-collections-are-installed) of the
project alai finds from the current directory. See
[Finding the project root](projects.md#finding-the-project-root) for more
information.

In the following example, `alai.toml` is in the parent directory, so running
`alai collection install ...` from `playbooks/` installs the collections in
the directory containing the `alai.toml`.

[Installing from a directory inside the project](casts/collection-install-subdirectory.cast){ .alai-cast }

With no project, use `--collections-dir` to say which collections directory
to use.

[Installing outside a project](casts/collection-install-outside.cast){ .alai-cast }

## Collections whose version can't be read

An installed collection's version is read from its `MANIFEST.json`. If that
file is not present, `alai collection install` treats it as not installed.
This means any existing content is replaced with the collection being
installed, even without `--reinstall` or `--reinstall-collection NAME`.

`--keep-unversioned` leaves such a collection as it is instead, counting it
as whatever version is asked for, as `ansible-galaxy` does without
`--force`.
