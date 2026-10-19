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
[`collections_dir`](configuration.md#projectcollections_dir).

Only the project's `alai.toml` or a file given with `--config-file` can have
a `[project]` table. In a user or system file it's an error, since one
collections directory shared by every project would defeat the per-project
layout. `--collections-dir` and `ALAI_COLLECTIONS_DIR` can still set it for a
single run.

See [Configuration](configuration.md) for the other settings and how the
config files combine.
