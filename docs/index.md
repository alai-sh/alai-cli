# alai

A fast package and environment manager for Ansible collections, it is heavily
inspired by [uv](https://docs.astral.sh/uv/).

!!! warning "Preview"

    alai is in preview and its interface isn't stable yet. Expect breaking
    changes as workflows are worked out and adjusted.

!!! note "A word on how alai is made"

    alai is written with the help of AI coding tools. That doesn't mean a
    model was handed a prompt and left to get on with it: a human argues
    over every design decision, the reasoning behind them is written down
    in the repository, and features are tried by hand against real
    projects before they land, then tried again when they don't behave.
    Think of it as pair programming where one of the pair never needs a
    coffee break.

    If you'd rather not use software built this way, that's a fair
    position, and it seemed better to say so here than after you've read
    the rest of the docs. No hard feelings.

alai installs the collections an Ansible project needs into a
self-contained location of that project's own. This separates the collections
a project uses from other projects and keeps things self contained and
isolated. [`alai run`](running-ansible.md) runs Ansible with those
collections and no others. It records the exact versions it chose in a
lock file, so every machine and CI run gets the same collections, and
installs them in a fraction of the time `ansible-galaxy` takes.

Everything alai manages is a project: a directory of Ansible content and
the collections it needs. A project is one of two kinds (see
[Projects](projects.md#playbook-projects-and-collection-projects)):

- **Playbook projects**, for using collections: playbooks, roles,
  inventories or any other Ansible content. The collections go in
  `collections/` beside its `alai.toml`, where `ansible-playbook` finds
  them with or without `alai run`.
- **Collection projects**, for writing one: a collection's own repository,
  with its `galaxy.yml`. The collection you're developing is installed
  beside its dependencies, linked to your checkout, so `ansible-test`,
  `ansible-doc` and playbooks see your edits straight away, wherever it's
  checked out.

alai is designed to run on Linux and macOS only. There is no Windows support
as Ansible itself cannot run on Windows and thus has no need for collections
to be managed there.

## Installing

Install alai with [uv](https://docs.astral.sh/uv/getting-started/installation/),
from the latest source on GitHub:

```console
$ uv tool install https://github.com/alai-sh/alai-cli/archive/refs/heads/main.tar.gz
```

uv puts the `alai` command on your `PATH`, in an environment of its own,
and finds or downloads a Python 3.10 or later for it. To update to the
latest source, run the same command with `--reinstall`; `uv tool upgrade`
doesn't look for new commits. `uv tool uninstall alai` removes it.

!!! note "Until alai is on PyPI"

    Installing from GitHub is temporary. Once alai is published on PyPI,
    `uv tool install alai` will install it, and `uv tool upgrade alai`
    will update it.

## A quick look

Start a project, add the collections it needs, and they're resolved, locked
and installed in one step:

[Adding collections to a new project](casts/add.cast){ .alai-cast }

`alai.toml` now lists what the project needs, `alai.lock` the exact versions
chosen, and the project's collections directory holds them: `collections/`
beside `alai.toml` here, where `ansible-playbook` finds them even without
`alai run`. Commit the first two; anyone else
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
- **Collections per project.** Each project has its own collections,
  instead of one shared `~/.ansible/collections` where one project's
  upgrade changes another's, and `alai run` makes sure Ansible loads only
  those.
- **Collection development.** A
  [collection project](projects.md#collection-projects) no longer needs to
  be in the `ansible_collections/<namespace>/<name>` directory layout, and
  its dependencies never end up in the collection you build or the files
  `ansible-test sanity` checks.
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
- [Running Ansible](running-ansible.md): running `ansible-playbook` and
  the other Ansible commands with the project's collections, and setting
  up a shell, CI or an editor to do the same.
- [Editors](editors.md): running and debugging Ansible from VS Code with
  the project's collections.
- [Installing by hand](installing-by-hand.md): installing and uninstalling
  collections outside the project's lock, and how that differs.
- [Finding plugins](finding-plugins.md): which collection has a module,
  filter or role, and where a moved or removed one went.
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
- [Acknowledgements](acknowledgements.md): the projects alai is inspired
  by and built on.
