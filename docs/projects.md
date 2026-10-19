# Projects

Most alai commands act on a project: a directory of playbooks and the
collections they use. A project has a root, found from the directory alai
runs in, and a collections directory inside it that collections are
installed into. Its `alai.toml`, the project file, holds settings for that
project alone.

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
[`collections_dir`](configuration.md#projectcollections_dir) to install next
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

Settings that describe the project, rather than how alai runs, go in the
`[project]` table of the project file. Today that's
[`collections_dir`](configuration.md#projectcollections_dir), plus the
[collections the project needs](#declaring-collections).

Only the project's `alai.toml` can have a `[project]` table. In a user or
system file it would apply to every project, and a `--config-file` only
holds settings, so in any other file it's an error.
`--collections-dir` and `ALAI_COLLECTIONS_DIR` can still set
`collections_dir` for a single run.

See [Configuration](configuration.md) for the other settings and how the
config files combine.

## Declaring collections

!!! note "Not used yet"

    No command installs from these keys yet. Every command already checks
    that they're spelt right and have the right types, but the requirements
    themselves are only checked by the commands that use them.

The project's `alai.toml` lists the collections the project needs:

```toml
# alai.toml
[project]
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "internal:corp.tools>=1.2",
]

# Collections only some workflows need, such as tests.
[groups.test]
collections = ["ansible.posix", "community.crypto"]

# Fixes for wrong metadata in the collections you depend on.
[overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]
```

These keys are only read from the project's own `alai.toml`, the one that
[fixes the project root](#finding-the-project-root). `--no-config` and
`--config-file` change which settings alai reads, not what the project
needs: a command that uses these keys reads them from the project's
`alai.toml` either way. In any other file, including a `--config-file`,
they're an error, rather than being silently ignored.

| Key | Type | |
|---|---|---|
| `project.collections` | array of [requirements](#requirements) | The collections the project needs. A collection can be listed once |
| `project.requires_ansible` | string | The ansible-core versions the project targets, as a [PEP 440](https://peps.python.org/pep-0440/#version-specifiers) specifier such as `>=2.17` or `>=2.17,<2.20`: the same syntax as `requires_ansible` in a collection's `meta/runtime.yml` |
| `groups.<name>.collections` | array of [requirements](#requirements) | Collections only some workflows need. Group names use lowercase letters, digits, `-` and `_`, starting with a letter or digit |
| `overrides."<namespace.name>"` | [version requirement](#version-requirements) | Replaces every requirement on that collection, including those in other collections' dependencies. `"*"` accepts any version |
| `overrides.ignore_requires_ansible` | array of collection names | Collections whose own `requires_ansible` is ignored |

Collection names in `[overrides]` contain a dot, so they're quoted:
`"ansible.netcommon" = ">=6"`. Unquoted, TOML reads `ansible.netcommon` as a
table.

### Requirements

A requirement is a collection name, optionally pinned to an
[index](configuration.md#index) and followed by a version requirement:

```text
[INDEX:]NAMESPACE.NAME[VERSION REQUIREMENT]
```

| Requirement | Means |
|---|---|
| `ansible.windows` | Any version |
| `community.general>=9,<11` | 9.0.0 or later, but before 11.0.0 |
| `community.general >= 9` | The same as `community.general>=9`: spaces are allowed around each part |
| `internal:corp.tools>=1.2` | 1.2.0 or later, from the `internal` index only |

Collection names are lowercase: each of the namespace and the name is a
Python identifier that isn't a Python keyword.

### Version requirements

A version requirement is `*`, for any version, or one or more clauses
separated by commas, all of which must hold. A clause is an operator and a
version:

| Operator | Means |
|---|---|
| `==` | Exactly this version |
| `!=` | Any version but this one |
| `>=`, `>` | This version or later, or only later |
| `<=`, `<` | This version or earlier, or only earlier |

Collection versions are [semantic versions](https://semver.org/). A version
in a clause can leave out its minor and patch parts, which count as `0`:
`>=9` is `>=9.0.0`, and `==9` is exactly `9.0.0`. It can have a pre-release
part, as in `>=2.0.0-rc.1`, but not build metadata, since comparisons ignore
it: `==1.0.0` already matches `1.0.0+build1`.

This is the syntax ansible-galaxy uses for collection dependencies, with a
few forms left out because they read as something else:

| Written | Write instead |
|---|---|
| `1.2.3` or `=1.2.3` | `==1.2.3` |
| `^1.2.0`, `~1.2.0` | `>=1.2.0,<2.0.0`, `>=1.2.0,<1.3.0` |
| `==1.0.0+build1` | `==1.0.0` |
