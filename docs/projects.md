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
the project is; alai doesn't read Ansible's settings from it.

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

## Managing and installing them

Once the project's collections are declared, they're locked and installed
with `alai add`, `alai remove`, `alai lock` and `alai sync`: see
[Managing collections](managing-collections.md), which also has the
recommended workflows. To install collections without recording them in
the project, see [Installing by hand](installing-by-hand.md).
