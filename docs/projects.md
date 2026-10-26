# Projects

Most alai commands revolve around a single setup, which it calls a "project".
A project contains the settings and metadata needed by alai to manage the
requirements of the project itself. It also typically functions as a marker to
signal what directory is the "root" of a project.

This page goes through what a project is, how to start and use each kind,
how a project is found, and where its collections are installed.

## Playbook projects and collection projects

alai supports two kinds of project, a **playbook** project and a
**collection** project.

A **playbook** project is a project that contains any Ansible content, such
as playbooks, roles, inventories, and even internal collections. It is
designed for end users to bring all the components they use together and
run them through `ansible-playbook` or other Ansible commands.

A **collection** project is one used to manage a single collection with its
own roles, playbooks, and plugins. The overall intent is to publish the
collection as an individual artifact, so it can be reused by others,
including in a **playbook** project.

| | Playbook project | Collection project |
|---|---|---|
| Marked by | `alai.toml` without a `galaxy.yml` beside it, `ansible.cfg` or `collections/ansible_collections/` | `galaxy.yml`, with or without an `alai.toml` beside it |
| The collections it needs | [`project.collections`](alai-toml.md#projectcollections) in `alai.toml` | `dependencies` in `galaxy.yml` |
| The ansible-core versions it targets | [`project.requires_ansible`](alai-toml.md#projectrequires_ansible) in `alai.toml` | `requires_ansible` in `meta/runtime.yml` |
| Collections only some tasks need, such as tests | [`[project.groups.<name>]`](alai-toml.md#projectgroupsname) in `alai.toml` | [`[project.groups.<name>]`](alai-toml.md#projectgroupsname) in `alai.toml` |
| Overrides and sources | [`[project.overrides]`](alai-toml.md#projectoverrides) and [`[project.sources]`](alai-toml.md#projectsources) in `alai.toml` | The same |
| Default collections directory | [`collections/` in the project root](#where-collections-are-installed) | [A directory of its own in alai's cache](#where-collections-are-installed_1) |
| In `alai.lock` | Its dependencies | Its dependencies, and its own collection as `{ editable = "." }` |

In both project types, the [`alai run`](cli.md#alai-run) and
[`alai env`](cli.md#alai-env) commands are designed to run Ansible with the
alai-managed environment configured. How that is done depends on the project
type.

## Playbook project

A typical playbook project looks like:

```text
my-project/
├── alai.toml           # The collections the project needs, and alai's settings
├── alai.lock           # The exact versions alai resolved them to
├── ansible.cfg         # Ansible's own settings, alai doesn't read it
├── inventory.ini
├── site.yml            # Playbooks at the root find collections/ beside them
├── roles/
│   └── webserver/
├── collections_src/    # Collections kept in this repository, if any
│   └── corp/
│       └── tools/
└── collections/        # Installed by alai, not committed
    └── ansible_collections/
```

How the Ansible content is set up is entirely up to you, but the key things
to note are:

- `alai.toml` and `alai.lock` contain the project details and collection
  dependencies.
- `collections/` is the recommended collections directory for alai to
  manage, but it can be set to another directory in `alai.toml` (see
  [Where collections are installed](#where-collections-are-installed)).

To start one, create `alai.toml` and add the collections the project needs:

```console
$ touch alai.toml
$ alai add ansible.utils
```

Commit everything but `collections/`, which `alai sync` creates from
`alai.lock`:

```text
# .gitignore
/collections/
```

Run Ansible through `alai run` so it loads only the project's collections.
Run directly, `ansible-playbook` finds `collections/` beside a playbook at
the root, but it also loads collections from `~/.ansible/collections` and
the `ansible` package, and other commands, such as `ansible-doc`, don't look
in `collections/` at all.

Here `alai add` has added `ansible.utils`, and `site.yml` uses a module from
it:

[Running a playbook in a playbook project](casts/project-playbook.cast){ .alai-cast }

`alai run` can also be used to run other Ansible commands, such as an ad hoc
`ansible` command (see [Running Ansible](running-ansible.md)).

### Collections kept in the repository

If the playbook project needs collections that are kept in its own
repository rather than on an index, store them in a separate directory and
add them to the project as a directory source.

Here `corp.tools` is committed in `collections_src/` and added as an
editable directory, so `collections/` links to it and edits take effect
straight away (see
[Collections in your own repository](collection-sources.md#collections-in-your-own-repository)):

[Using a collection kept in the project's repository](casts/project-playbook-local-collection.cast){ .alai-cast }

### Where collections are installed

alai installs collections to the configured `collections_dir`, at
`<collections_dir>/ansible_collections/<namespace>/<name>`. In a playbook
project, that's `collections/` in the project root by default.

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
same tree. It doesn't add a second `ansible_collections/` level.

## Collection project

!!! note "Expect some rough edges"

    The mechanism alai uses to set up a collection project and its collection
    dependencies may not work with some tools. The aim is to ensure this
    project structure works, but if there are any problems, please report the
    details to the [issue tracker](https://github.com/alai-sh/alai-cli/issues).

A typical collection project looks like:

```text
my_col/
├── alai.toml           # Groups, overrides and sources, and marks the root
├── alai.lock           # The exact versions of its dependencies
├── galaxy.yml          # Its name, version and dependencies
├── meta/
│   └── runtime.yml     # The ansible-core versions it supports
├── plugins/
│   └── modules/
│       └── hello.py
├── playbooks/
│   └── site.yml        # Run as my_ns.my_col.site
├── roles/
└── tests/
    ├── integration/
    │   └── targets/
    │       └── hello/
    │           └── tasks/
    │               └── main.yml
    └── output/         # Written by ansible-test, not committed
```

The checkout doesn't need to be in an `ansible_collections/<namespace>/<name>`
directory, and has no `collections/` directory of its own.

To start one, create an empty `alai.toml` beside `galaxy.yml` and install
the collection's dependencies:

```console
$ touch alai.toml
$ alai sync
```

While an `alai.toml` file is not required, it is recommended, as it removes
any ambiguity when alai looks for the project root. Commit `alai.toml` and
`alai.lock`, and ignore what ansible-test writes:

```text
# .gitignore
/tests/output/
```

Unlike in a playbook project, `alai.toml` does not declare the collection's
requirements or `requires_ansible`, which come from `galaxy.yml` and
`meta/runtime.yml` (see the table
[above](#playbook-projects-and-collection-projects)). Setting
`project.collections` or `project.requires_ansible` there is an error. This
is to avoid duplicating information that is present in the existing
collection metadata until alai has a solution that can build a collection
without `ansible-galaxy` and the `galaxy.yml` setup.

Consequently, `alai add` and `alai remove` can only edit groups, with
`--group`. They cannot edit the `galaxy.yml` dependencies, so edit the file
manually, then run `alai lock` or `alai sync`.

Run tools through `alai run ...`, so they see the collection as if it were
checked out in an `ansible_collections` directory beside its dependencies
(see [Where collections are installed](#where-collections-are-installed_1)).

In the below example `my_ns.my_col` depends on `ansible.utils` and is set as a
dependency in `galaxy.yml`. Using `alai run ...` with subsequent commands will
sync the declared dependencies and set up the environment so commands like
`ansible-test` and `ansible-playbook` can find them.

[Testing and running a collection project](casts/project-collection.cast){ .alai-cast }

See [In a collection project](running-ansible.md#in-a-collection-project)
for what works with `ansible-test` and what doesn't yet.

### Where collections are installed

One complicated aspect of using collections in Ansible is that a collection
must be at a path ending in `ansible_collections/<namespace>/<name>/`, and
some tools only load its dependencies from the same `ansible_collections`
directory. So that the project can be cloned anywhere, and its dependencies
are still seen by those tools, the collections directory is a directory of
its own in alai's cache, `<cache_dir>/envs/<id>/`, where `<id>` comes from
the checkout's path. The checkout is linked into it, beside its
dependencies:

```text
~/.cache/alai/envs/f982071c15236c92/
└── ansible_collections/
    ├── ansible/
    │   └── utils/
    └── my_ns/
        └── my_col -> ~/my_col
```

Ansible sees the collection at that link, so the paths it shows for it are
there rather than in the checkout. `alai config show` prints the directory
as `project.collections_dir`.
[`collections_dir`](alai-toml.md#projectcollections_dir) can still move it,
but not into the checkout, nor to a directory that holds it.

### Collections only the tests need

A collection the tests use, but the collection itself doesn't need, should be
added to the `dev` group rather than `galaxy.yml`, so it isn't a dependency of
the published collection. `alai sync` and `alai run` install the `dev` group by
default, so the tests see it with no extra options.

Here the integration target checks its result with a filter from
`community.general`:

[A collection project's test-only collections, in its dev group](casts/project-collection-dev-group.cast){ .alai-cast }

To keep some apart, such as collections only one kind of test needs, put
them in another group with `--group NAME`, and install it with
`alai sync --group NAME` or `alai run --group NAME ...` (see
[Groups](managing-collections.md#groups)).

### In CI

Install exactly what `alai.lock` lists, then run the tests through
`alai run`:

```console
$ alai sync --locked
$ alai run ansible-test integration
```

`--locked` fails, without installing anything, if `alai.lock` is missing or
out of date with `galaxy.yml` or `alai.toml`.

## Finding the project root

For alai to manage a project, it must determine what the root of the
project is. The root is used for things like determining where the lock file is
created and where collections are installed to. To find the project root,
alai walks up from the current directory (or from `--project`,
`ALAI_PROJECT`) to the filesystem root. The project root is the directory
holding, in order of preference:

1. the nearest `alai.toml`
2. else the nearest `galaxy.yml`
3. else the nearest `ansible.cfg`
4. else the nearest `collections/ansible_collections/`
5. else the starting directory

A marker earlier in the list wins over a nearer one later in it, so running
from `repo/playbooks/`, which has its own `ansible.cfg`, still finds
`repo/alai.toml`, and running from an integration test target with an
`ansible.cfg` still finds the collection's `galaxy.yml`. The root is the
same whichever directory inside the project alai runs from.

!!! tip "Give every project an `alai.toml`"

    It's the strongest marker, so alai never takes a directory inside the
    project, such as a subdirectory with its own `ansible.cfg`, as a project
    of its own. Even an empty one works.

A repository can hold several projects, each in its own directory with its
own `alai.toml`. alai uses the nearest one, so each project has its own
`alai.lock` and collections.

Regardless of the marker that is used, alai settings are only read from
`alai.toml`. `ansible.cfg` only marks where the project is if no earlier
marker is present. alai doesn't read Ansible's settings from it.

Discovery is the same with `--no-config` and `--config-file`, as those
arguments only change which settings are read, not where the project is.
