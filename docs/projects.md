# Projects

Most alai commands act on a project: a directory of Ansible content and
the collections it uses. alai finds the project's root by walking up from
the directory it runs in: the root is where the nearest `alai.toml` is, or
with none, the nearest `galaxy.yml`, then the nearest `ansible.cfg`, then
the nearest `collections/ansible_collections/` (see
[Finding the project root](#finding-the-project-root)). Collections are
installed into the project's collections directory, and the root's
[`alai.toml`](alai-toml.md), the project file, says what the project needs
and holds settings for that project alone.

## Playbook projects and collection projects

Every project is one of two kinds, decided by its root alone:

- A **collection project** is one whose root defines a collection: it has
  a `galaxy.yml`. The collection it defines is the
  **project's own collection**, which alai installs as part of the
  project, linked to the directory it's checked out in. See
  [Collection projects](#collection-projects).
- A **playbook project** is any other project. It uses collections without
  defining one, whatever it holds: playbooks, roles, inventories, or
  nothing but an `alai.toml`.

| | Playbook project | Collection project |
|---|---|---|
| Marked by | `alai.toml` without a `galaxy.yml` beside it, `ansible.cfg` or `collections/ansible_collections/` | `galaxy.yml`, with or without an `alai.toml` beside it |
| The collections it needs | [`project.collections`](alai-toml.md#projectcollections) in `alai.toml` | `dependencies` in `galaxy.yml` |
| The ansible-core versions it targets | [`project.requires_ansible`](alai-toml.md#projectrequires_ansible) in `alai.toml` | `requires_ansible` in `meta/runtime.yml` |
| Groups, overrides and sources | `alai.toml` | `alai.toml` |
| Collections directory by default | `collections/` in the project root | A directory of its own in alai's cache |
| In `alai.lock` | Its dependencies | Its dependencies, and its own collection as `{ editable = "." }` |

## Finding the project root

alai walks up from the current directory (or from `--project`,
`ALAI_PROJECT`) to the filesystem root. The root is the directory holding, in
order of preference:

1. the nearest `alai.toml`, the project file, which makes it a
   [collection project](#collection-projects) if a `galaxy.yml` is beside
   it;
2. else the nearest `galaxy.yml`, a collection project;
3. else the nearest `ansible.cfg`;
4. else the nearest `collections/ansible_collections/`;
5. else the starting directory.

A marker earlier in the list wins over a nearer one later in it, so running
from `repo/playbooks/`, which has its own `ansible.cfg`, still finds
`repo/alai.toml`, and running from an integration test target with an
`ansible.cfg` still finds the collection's `galaxy.yml`. The root is the
same whichever directory inside the project alai runs from.

!!! tip "Give every project an `alai.toml`"

    Put an `alai.toml` at the root of every project, of either kind, even
    when it would be empty:

    ```console
    $ touch alai.toml
    ```

    It's the strongest marker, so it settles which directory alai manages
    whatever else is inside it. Without one, alai falls back to weaker
    markers that can turn up in places that aren't a project of their own:
    a collection's source checkout under `collections/` has a `galaxy.yml`,
    a subdirectory can have its own `ansible.cfg` for one environment or
    test, and one with its own `collections/ansible_collections/` looks
    like a project too. Run from inside any of those, alai would take it
    as the project. In a collection project, the `alai.toml` goes beside
    `galaxy.yml`.

alai settings are only read from `alai.toml`. `ansible.cfg` only marks where
the project is; alai doesn't read Ansible's settings from it.

Discovery is the same with `--no-config` and `--config-file`: those change
which settings are read, not where the project is.

## Where collections are installed

Collections install to
`<collections_dir>/ansible_collections/<namespace>/<name>`. By default,
`collections_dir` is:

- in a playbook project, `collections/` in the project root;
- in a collection project, a directory of its own in alai's cache, as
  [Collection projects](#collection-projects) explains.

Ansible looks for collections in a `collections/` directory next to the
playbook it runs, so with a playbook project's playbooks at its root,
`ansible-playbook` finds them with no extra configuration.

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

## Collection projects

In a [collection project](#playbook-projects-and-collection-projects),
alai installs the project's own collection as part of the project, linked
to the checkout rather than copied, so edits take effect straight away.
The rest of what it needs comes from the collection's own files:

| What | Where it comes from |
|---|---|
| The collections it needs | `dependencies` in `galaxy.yml` |
| The ansible-core versions it targets | `requires_ansible` in `meta/runtime.yml` |
| Collections only its tests need | [`[project.groups.<name>]`](alai-toml.md#projectgroupsname) in `alai.toml` |
| Overrides and sources | [`[project.overrides]`](alai-toml.md#projectoverrides) and [`[project.sources]`](alai-toml.md#projectsources) in `alai.toml` |

`project.collections` and `project.requires_ansible` in `alai.toml` are
errors there, as `galaxy.yml` and `meta/runtime.yml` already say them.
`galaxy.yml`'s dependencies use ansible-galaxy's syntax (`"*"`,
`">=1.0.0"`, a bare version for exactly that one), and are read as
ansible-galaxy reads them. `alai.lock` goes beside `galaxy.yml`, with the
collection itself as an entry whose source is `{ editable = "." }`.

The collections aren't installed in the checkout but in a directory of
their own in alai's cache, `<cache_dir>/envs/<id>/`, with the checkout
linked in at `<cache_dir>/envs/<id>/ansible_collections/<namespace>/<name>`.
This is to allow the collection and its dependencies to be usable by Ansible
and other tooling that go through the collection loader that require this
project structure. Use
[`alai run`](running-ansible.md#in-a-collection-project) or
[`alai env`](running-ansible.md#in-a-shell-ci-or-an-editor) to run Ansible
against them. Ansible then sees the collection at its link in that
directory, so the paths it shows for it are there rather than in the
checkout. `collections_dir` can still move them, but not into the
checkout, nor to a directory that holds it.

A collection on an index that depends on the collection itself gets the
checkout, at the version its `galaxy.yml` says. If that version doesn't meet the
requirement, an older version of the collection that asked is chosen, or
locking fails and says so.

`alai add` and `alai remove` only edit groups there, with `--group`: they
can't edit `galaxy.yml`'s `dependencies` yet, so change those by hand, then
run `alai lock` or `alai sync`.

## Managing and installing them

Once the project's collections are declared, they're locked and installed
with `alai add`, `alai remove`, `alai lock` and `alai sync`: see
[Managing collections](managing-collections.md), which also has the
recommended workflows. To install collections without recording them in
the project, see [Installing by hand](installing-by-hand.md).
