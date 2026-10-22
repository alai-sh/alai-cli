# alai

A fast package and environment manager for Ansible collections, heavily
inspired by [uv](https://docs.astral.sh/uv/).

> **Preview:** alai's interface isn't stable yet. Expect breaking changes as
> workflows are worked out.

alai installs the collections an Ansible project needs into the project
itself, in `collections/ansible_collections/` next to your playbooks, where
`ansible-playbook` finds them with no extra configuration. It records the
exact versions it chose in a lock file, so every machine and CI run gets the
same collections, and installs them in a fraction of the time
`ansible-galaxy` takes.

**Documentation: <https://docs.alai.sh/>**

## Why alai

- **A lock file.** `alai.lock` records the exact version of every
  collection, dependencies included, and only changes when you change what
  the project needs or ask to upgrade.
- **Fast.** A first install of `community.general` takes about 1.4 s where
  `ansible-galaxy` takes 16.5 s, and installing it again into another
  project takes 0.23 s, as each collection is downloaded once and linked
  from a cache.
- **Reliable.** Every download is checked against its SHA-256 in the lock,
  an interrupted install never leaves half a collection, and `--offline`
  installs from the cache without connecting at all.
- **Collections per project**, rather than one shared
  `~/.ansible/collections` where one project's upgrade changes another's.
- **No Ansible needed to install**, so CI can fetch collections before, or
  without, installing ansible-core.
- **Familiar commands:** `alai add`, `alai remove`, `alai lock` and
  `alai sync` work as uv's do for Python packages.

alai runs on Linux and macOS, as Ansible itself does.

## Installing

Install alai with [uv](https://docs.astral.sh/uv/getting-started/installation/),
from the latest source on GitHub:

```console
$ uv tool install https://github.com/alai-sh/alai-cli/archive/refs/heads/main.tar.gz
```

Run the same command with `--reinstall` to update. Part of alai is written in
Rust, so installing from source needs Rust 1.88 or later, with `cargo` on your
`PATH` ([rustup](https://rustup.rs/) installs it). This is temporary: once
alai is published on PyPI, it will be `uv tool install alai`, from a
ready-built wheel on most Linux and macOS systems.

## Getting started

An `alai.toml` marks the project, so create one beside your playbooks, then
add the collections they use:

```console
$ touch alai.toml
$ alai add ansible.posix 'community.general>=10,<12'
Resolved 2 collections in 2.15s
Prepared 2 collections in 2.41s
Installed 2 collections in 122ms
 + ansible.posix==2.2.2
 + community.general==11.4.9
```

`alai.toml` now lists what the project needs, `alai.lock` the exact versions
chosen, and `collections/` holds them:

```toml
[project]
collections = ["ansible.posix>=2.2.2", "community.general>=10,<12"]
```

Commit `alai.toml` and `alai.lock`, and ignore `collections/`. Anyone else,
or a fresh checkout, gets the same collections with `alai sync`:

```console
$ alai sync
Resolved 2 collections in 1ms
Installed 2 collections in 89ms
 + ansible.posix==2.2.2
 + community.general==11.4.9
```

## Common tasks

| Task | Command |
|---|---|
| Add a collection, or change its requirement | `alai add cisco.ios` |
| Add one only some workflows need | `alai add --group test ansible.utils` |
| Remove a collection | `alai remove cisco.ios` |
| Install what `alai.lock` lists, after a pull | `alai sync` |
| Fail CI if `alai.lock` is out of date | `alai sync --locked` |
| Upgrade one collection, a group, or everything | `alai lock -P cisco.ios`, `alai lock --upgrade-group test`, `alai lock -U` |
| Put back a collection edited in place | `alai sync --reinstall-collection cisco.ios` |
| Install a collection by hand, outside the lock | `alai collection install ansible.posix` |
| See where each setting comes from | `alai config show` |

Coming from `ansible-galaxy` and a `requirements.yml`? The
[migration guide](https://docs.alai.sh/ansible-galaxy/) covers moving a
project over and the differences you may run into.

## Learn more

- [Managing collections](https://docs.alai.sh/managing-collections/):
  adding, locking and installing, and workflows for teams and CI.
- [Configuration](https://docs.alai.sh/configuration/) and
  [`alai.toml`](https://docs.alai.sh/alai-toml/): every setting and key.
- [CLI reference](https://docs.alai.sh/cli/): every command and option,
  with recordings of them running.
- [Roadmap](https://docs.alai.sh/roadmap/): what's planned next.
- [Acknowledgements](https://docs.alai.sh/acknowledgements/): the projects
  alai is inspired by and built on.

Bugs and ideas are welcome in the
[issue tracker](https://github.com/alai-sh/alai-cli/issues).

## License

MIT; see [LICENSE](https://github.com/alai-sh/alai-cli/blob/main/LICENSE).
