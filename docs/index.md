# alai

A fast package and environment manager for Ansible collections, heavily
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

    I respect your decision if you'd rather not use software built this way,
    and it seemed better to say so outright rather than after you've read
    the docs or started using the software.

alai is a tool that can help manage collections used in an Ansible project as
well as managing the dependencies and environment for a collection project
itself. Everything is designed to be self-contained so that environment
settings and existing collections do not affect any projects using alai. This
keeps projects as the authoritative source of what collections they need, as
well as controlling the versions and sources of those collections.

[`alai run`](running-ansible.md) runs the command that follows it with the
collections described in the lock file generated from
[alai.toml](alai-toml.md). These commands can be Ansible itself or any other
tool that needs to interact with the project. alai records the exact versions
it chose in that lock file, so every machine and CI run gets the same
collections, and installs them in a fraction of the time `ansible-galaxy`
takes.

Here `ansible-galaxy` and alai each install `community.general` with
nothing cached, then alai installs it again into another project from its
cache. Each was recorded on its own, so neither's downloads slowed the
other's, and they play side by side from the same start, at the speed they
ran. The timer on each counts how long its commands have been running:

[ansible-galaxy and alai installing community.general](assets/casts/galaxy-comparison.cast){ .alai-cast }

Everything alai manages is a project, of which there are currently two types
(see [Projects](projects.md#playbook-projects-and-collection-projects)):

- **Playbook projects**, for generic Ansible projects that require
  collections, for example custom playbooks, roles and inventories. This is
  end-user content that runs Ansible itself.
- **Collection projects**, for developing a single collection with its
  dependencies declared in its `galaxy.yml`. The collection you are
  developing is installed beside the declared dependencies, linked to your
  checkout in the cache directory.

alai is designed to run on Linux and macOS only. It should work on other
*nix-like platforms, but it is not officially tested on them. There is no
Windows support, as Ansible itself cannot run on Windows and thus has no need
for collections to be managed there.

## Installing

The recommended method is to install alai as a tool using
[uv](https://docs.astral.sh/uv/getting-started/installation/). As alai has
not been officially published, this is done by targeting the git repo.

```console
$ uv tool install https://github.com/alai-sh/alai-cli/archive/refs/heads/main.tar.gz
```

uv puts the `alai` command on your `PATH`, in an environment of its own,
and finds or downloads a Python 3.10 or later for it. To update to the
latest source, run the same command with `--reinstall`; `uv tool upgrade`
will not update it, as it does not look for new commits.
`uv tool uninstall alai` removes it.

It is possible to install alai using any other Python package installer, but
`uv` is recommended as it keeps the environment isolated and puts `alai` on
the `PATH` automatically.

Part of alai is written in Rust, so installing from source requires the Rust
toolchain. Rust 1.88 or later is required, with `cargo` on your `PATH`
([rustup](https://rustup.rs/) installs it), and the build takes a minute or
so.

!!! note "Until alai is on PyPI"

    Installing from GitHub is temporary. Once alai is published on PyPI,
    `uv tool install alai` will install it, and `uv tool upgrade alai`
    will update it. The PyPI release will be a ready-built wheel that is
    compatible with most Linux and macOS systems and will not require Rust.

## A quick look

Here is a quick example of a playbook project that adds the collections it
needs. `alai add` will automatically sync and update the lock file.

[Adding collections to a new project](casts/add.cast){ .alai-cast }

`alai.toml` now lists what the project needs and `alai.lock` has the exact
versions chosen. The project's `collections/` directory also contains only
the collections specified in the lock file, so `ansible-playbook` finds them
automatically when it runs a playbook in this directory. Once ready, commit
`alai.toml` and `alai.lock` to your repository; anyone who pulls these changes
runs `alai sync` to get the same collections.

## Why alai

- **Lock files.** `alai.lock` records the exact version of every
  collection and its dependencies. Versions only change when you change
  `alai.toml` or ask to upgrade a collection. `alai sync --locked` can be
  used in environments like CI to check that `alai.toml` and `alai.lock` agree.
- **Faster installs.** Metadata comes from a static index rather than the
  Galaxy API. With efficient caching both locally and on the Cloudflare CDN,
  alai installs and syncs collections super fast. For example, with no
  cache, installing `community.general` takes about 1.2 s
  whereas `ansible-galaxy` takes 14.6 s (see
  [Speed comparison](#speed-comparison)).
- **Reliability.** Every download is checked against the SHA-256 in
  `alai.lock`, and every file against the collection's own `FILES.json`.
  Installs don't depend on the Galaxy API being available; if Cloudflare
  goes offline, then we have bigger problems. With `--offline`, alai
  can install collections from its cache without any internet access at all.
- **Collections per project.** Each project has its own collections,
  instead of one shared `~/.ansible/collections` where one project's
  upgrade changes another's, and `alai run` makes sure Ansible loads only
  those.
- **Collection development.** A
  [collection project](projects.md#collection-project) no longer needs to
  be in the `ansible_collections/<namespace>/<name>` directory layout, and
  its dependencies never end up in the collection you build or the files
  `ansible-test sanity` checks.
- **No Ansible needed.** alai doesn't need Ansible to be installed in the
  same environment. A CI job or container can fetch collections before, or
  without, installing Ansible.
- **Familiar commands.** `alai add`, `alai remove`, `alai lock` and
  `alai sync` work as uv's do for Python packages.

## Speed comparison

The tables below are the medians of five runs each, separate from the
recording at the top of this page. Please note that timings vary from run to
run and can be affected by a range of factors, such as CDN caching and
network load. Take these results with a grain of salt and use them as a
general indication of the speed differences.

They install `community.general`, a large collection of over 3,500 files, and
`cisco.ios` with the two collections it depends on:

| `community.general` | ansible-galaxy | alai |
|---|---|---|
| First install, nothing cached | 14.6 s | 1.15 s |
| First install, from a committed `alai.lock` | | 0.61 s |
| Installing again into another project | 9.5 s | 0.26 s |
| Nothing to change | 0.30 s | 0.04 s |

| `cisco.ios` and its dependencies | ansible-galaxy | alai |
|---|---|---|
| First install, nothing cached | 20.7 s | 0.80 s |
| First install, from a committed `alai.lock` | | 0.41 s |
| Installing again into another project | 13.4 s | 0.18 s |
| Nothing to change | 0.23 s | 0.04 s |

alai's speed comes from a few different things:

- **Static index files.** The index only serves static files: no server is
  required to process incoming requests, as it is just HTTP content.
- **Efficient index format.** The alai index provides all the data needed to
  resolve a collection's versions and download details in one request, vs
  the 2+ requests needed on Galaxy.
- **CDN caching.** Cloudflare will cache both the index files and collection
  artifacts near your location, making it quick to respond to requests.
  Popular collections should typically be available in the CDN by default.
- **Local caching.** alai will cache all collection details and extracted
  collection artifacts in a local cache, so future operations need only 2
  requests at best to ensure the cache is not stale.
- **Faster tar operations.** Extracting tarballs in Python is slow compared
  to other languages, so alai uses a Rust native extension to speed up this
  operation, and larger collections like `community.general` won't slow
  things down.

!!! note "How these were measured"

    The benchmarks above were run on 2026-10-26 with ansible-galaxy from
    ansible-core 2.21.5. Each run started with an empty home directory, so
    neither tool had anything cached, and each tool did:

    1. A first install into a new project:
       `ansible-galaxy collection install -r requirements.yml -p ./collections --clear-response-cache`,
       or `alai sync`, with and without a committed `alai.lock`.
    2. The same command again, with nothing to change.
    3. A first install into a second project, keeping what the first had
       cached: ansible-galaxy's response cache, and alai's cache.

## Where next

- [Projects](projects.md): how alai finds your project, where collections
  are installed, and declaring the collections it needs.
- [Managing collections](managing-collections.md): adding, removing,
  locking and installing a project's collections, and common tasks
  for teams and CI.
- [Collection sources](collection-sources.md): what sources alai can use when
  installing a collection, and when to use which.
- [Running Ansible](running-ansible.md): running `ansible-playbook` and
  the other Ansible commands with the project's collections, and setting
  up a shell, CI or an editor to do the same.
- [Editors](editors.md): running and debugging Ansible from VS Code with
  the project's collections.
- [Installing by hand](installing-by-hand.md): installing and uninstalling
  collections outside the project's lock.
- [Finding plugins](finding-plugins.md): how to find which collection has a
  particular module, filter, role or other plugin type.
- [Coming from ansible-galaxy](ansible-galaxy.md): moving a project over,
  and the differences you may run into.
- [Configuration](configuration.md): where alai reads its settings from,
  and how files, environment variables and flags work together.
- [Indexes](indexes.md): where alai gets collections from.
- [alai.toml](alai-toml.md): every key and table the file can hold.
- [Shell completion](shell-completion.md): Tab completion for alai's commands
  and options.
- [CLI reference](cli.md): the CLI usage information.
- [Roadmap](roadmap.md): what's planned next.
- [Acknowledgements](acknowledgements.md): the projects alai is inspired
  by and built on.
