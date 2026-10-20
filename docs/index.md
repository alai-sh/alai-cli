# alai

A fast package and environment manager for Ansible collections, it is heavily
inspired by [uv](https://docs.astral.sh/uv/).

!!! warning "Preview"

    alai is in preview and its interface isn't stable yet. Expect breaking
    changes as workflows are worked out and adjusted.

alai installs the collections an Ansible project needs into the project
itself, in `collections/ansible_collections/` next to your playbooks, where
`ansible-playbook` finds them with no extra configuration. It records the
exact versions it chose in a lock file, so every machine and CI run gets the
same collections, and installs them in a fraction of the time
`ansible-galaxy` takes.

alai is designed to run on Linux and macOS only. There is no Windows support
as Ansible itself cannot run on Windows and thus has no need for collections
to be managed there.

## A quick look

Start a project, add the collections it needs, and they're resolved, locked
and installed in one step:

[Adding collections to a new project](casts/add.cast){ .alai-cast }

`alai.toml` now lists what the project needs, `alai.lock` the exact versions
chosen, and `collections/` holds them. Commit the first two; anyone else
runs `alai sync` to get the same collections.

## Why alai

- **A lock file.** `alai.lock` records the exact version of every
  collection, dependencies included. Versions only change when you change
  `alai.toml` or ask to upgrade, and `alai sync --locked` fails CI when
  `alai.toml` and the lock disagree.
- **Faster installs.** Metadata comes from a static index rather than the
  Galaxy API, each collection is downloaded once and linked into every
  project that uses it, and a sync with nothing to change connects to
  nothing. A first install of `community.general` takes about 1.4 s where
  `ansible-galaxy` takes 16.5 s (see
  [Speed comparison](#speed-comparison)).
- **Reliable installs.** Every download is checked against the SHA-256 in
  `alai.lock`, and every file against the collection's own `FILES.json`.
  Each collection is built beside its place and renamed in, so an
  interrupted install leaves what was there, never half a collection.
  Installs don't depend on the Galaxy API being up, and with `--offline`
  alai installs from its cache without connecting at all.
- **Collections per project.** Each project has its own `collections/`,
  instead of one shared `~/.ansible/collections` where one project's
  upgrade changes another's.
- **No Ansible needed to install.** alai doesn't need ansible-core
  installed, so a CI job or container can fetch collections before, or
  without, installing Ansible.
- **Familiar commands.** `alai add`, `alai remove`, `alai lock` and
  `alai sync` work as uv's do for Python packages.

## Speed comparison

Installing `community.general`, a large collection of over 4,000 files, and
`cisco.ios` with the three collections it needs:

| `community.general` | ansible-galaxy | alai |
|---|---|---|
| First install, nothing cached | 16.5 s | 1.4 s |
| First install, from a committed `alai.lock` | | 0.9 s |
| Installing again into another project | 12.8 s | 0.23 s |
| Nothing to change | 0.23 s | 0.03 s |

| `cisco.ios` and its dependencies | ansible-galaxy | alai |
|---|---|---|
| First install, nothing cached | 27.8 s | 0.87 s |
| First install, from a committed `alai.lock` | | 0.56 s |
| Installing again into another project | 15.1 s | 0.20 s |
| Nothing to change | 0.25 s | 0.03 s |

alai reads collection metadata from its index, a static mirror of Galaxy
with a few changes to make that metadata quicker to fetch, and keeps each
collection it downloads in a cache, so installing it again into another
project is a matter of linking files, not downloading them.

!!! note "How these were measured"

    The median of five runs each, on 2026-10-21, on a 6-core aarch64 Linux
    machine: ansible-core 2.20.0's `ansible-galaxy collection install -r
    requirements.yml -p ./collections` from Galaxy, against `alai sync`
    from `index.alai.sh`, over the same connection. Every run started with an
    empty home directory, so neither tool had anything cached; the first
    install cleared ansible-galaxy's response cache, and the second kept it,
    as ansible-galaxy does by default. Galaxy's response times varied
    widely: the slowest first install of `community.general` took 78 s.

## Where next

- [Projects](projects.md): how alai finds your project, where collections
  are installed, and declaring the collections it needs.
- [Managing collections](managing-collections.md): adding, removing,
  locking and installing a project's collections, and the recommended
  workflows for teams and CI.
- [Installing by hand](installing-by-hand.md): installing and uninstalling
  collections outside the project's lock, and how that differs.
- [Coming from ansible-galaxy](ansible-galaxy.md): moving a project over,
  and the differences you may run into.
- [Configuration](configuration.md): where alai reads its settings from,
  and how files, environment variables and flags combine.
- [Indexes](indexes.md): where alai gets collections from. Still in flux.
- [alai.toml](alai-toml.md): every key and table the file can hold.
- [Shell completion](shell-completion.md): Tab completion for alai's commands
  and options.
- [CLI reference](cli.md): every command and option, with recordings of
  them running.
- [Roadmap](roadmap.md): what's planned next.
