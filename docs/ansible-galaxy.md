# Coming from ansible-galaxy

alai is designed to replace `ansible-galaxy collection install` and provide a
modern interface for managing an Ansible project. While alai can be used to
install collections in a similar way to `ansible-galaxy`, it is not a straight
replacement. This page covers the details of how to migrate to alai and some
of the differences you may run into.

## Moving a project over

These steps are recommended, but using `alai sync` and a lock file isn't
required. You can use `alai collection install` to install collections
manually (see [Installing by hand](installing-by-hand.md)). Don't mix it with
`alai sync`, which removes collections the lock doesn't list. You should still
follow steps 1 and 2 to set up a project marker and configure the correct
collections directory.

1. **Create an `alai.toml`** in the directory holding your playbooks. It
   marks the project's root, and can start empty:

    ```console
    $ touch alai.toml
    ```

2. **Check the collections directory.** Ansible looks in a `collections/` directory
   next to the playbook it runs, so with playbooks at the project root the
   default value will work. When a playbook is in a subdirectory, either set
   [`collections_dir`](alai-toml.md#projectcollections_dir) to install next
   to it, point `collections_path` in your `ansible.cfg` at alai's
   directory, or use [alai run](cli.md#alai-run) when calling Ansible. See
   [Where collections are installed](projects.md#where-collections-are-installed)
   for more details.

3. **Move collections you commit to the project** out of `collections/`,
   if there are any. alai manages that directory and `alai sync` removes
   anything `alai.lock` doesn't list. Move each one to another directory in
   the project and add it as a source with `alai add --editable <dir>`. See
   [Collections in your own repository](collection-sources.md#collections-in-your-own-repository).

4. **Add the collections from your `requirements.yml`**, each with its
   version requirement, translated as in
   [the table below](#requirementsyml-to-alaitoml):

    ```console
    $ alai add 'community.general>=10,<12' ansible.posix 'cisco.ios==11.6.0'
    ```

    This writes them into `alai.toml`, resolves them into `alai.lock` and
    installs them into `collections/ansible_collections/` in the project.

5. **Commit `alai.toml` and `alai.lock`**, ignore `collections/`, and
   replace `ansible-galaxy collection install -r requirements.yml` in CI
   and docs with `alai sync --locked` (see
   [Common tasks](managing-collections.md#common-tasks)).

6. **Keep `requirements.yml` for roles**, if it lists any: alai only
   installs collections. Its `collections:` entries can go.

## Commands

The following table shows the alternative alai command when coming from `ansible-galaxy`:

| ansible-galaxy | alai |
|---|---|
| `ansible-galaxy collection install -r requirements.yml` | `alai sync`, from `alai.toml` and `alai.lock` |
| `ansible-galaxy collection install NAME`, then adding it to `requirements.yml` | `alai add NAME` |
| `ansible-galaxy collection install NAME -p DIR`, for a one-off | `alai collection install NAME`, with `--collections-dir DIR` to override the project's directory ([Installing by hand](installing-by-hand.md)) |
| `ansible-galaxy collection install --upgrade NAME` | `alai sync -P NAME`, or `alai lock -P NAME` to only update the lock |
| `ansible-galaxy collection install --force NAME` | `alai sync --reinstall-collection NAME`, or `alai collection install --reinstall-collection NAME NAME` for a one-off (see [below](#edited-collections-arent-replaced)) |
| `ansible-galaxy collection install --force-with-deps NAME` | `alai collection install --reinstall NAME` |
| `ansible-galaxy collection install --pre NAME` | Nothing extra: a pre-release is chosen when no stable version fits (see [Pre-releases](#pre-releases)) |
| Deleting a collection's directory | `alai remove NAME`, or `alai collection uninstall NAME` |
| `ansible-galaxy role install` | Not handled: keep using ansible-galaxy for roles |

## `requirements.yml` to `alai.toml`

alai can't read a `requirements.yml` yet, so you need to add the collections
yourself. Use either `alai add` to add and sync each collection, or edit
`alai.toml` to set the `[project] collections`. Each entry becomes one
[requirement](alai-toml.md#requirements), the name followed by its
version:

| `requirements.yml` | `alai.toml` |
|---|---|
| `- community.general`, or `version: "*"` | `"community.general"` |
| `version: ">=10.0.0,<12.0.0"` | `"community.general>=10.0.0,<12.0.0"` |
| `version: "10.7.9"`, a bare version meaning exactly it | `"community.general==10.7.9"`: alai needs the `==` |
| `version: "!=10.7.0"` | `"community.general!=10.7.0"` |
| `source:` naming a Galaxy server | No equivalent; see [Indexes, not Galaxy servers](#indexes-not-galaxy-servers) |
| `type: git`, `name: git+<url>#/<dir>,<version>` | `"<ns>.<name>"`, with `{ git = "<url>", subdirectory = "<dir>", rev = "<version>" }` in [`[project.sources]`](collection-sources.md#a-git-repository) |
| `type: url` or `file` | `"<ns>.<name>"`, with `{ url = "<url>" }` or `{ path = "<file>" }` in [`[project.sources]`](collection-sources.md#a-tarball) |
| `type: dir` | `"<ns>.<name>"`, with `{ path = "<dir>" }` in [`[project.sources]`](collection-sources.md#a-directory) |
| `signatures:` | Not supported |

The following is an example `requirements.yml` that covers a few different
scenarios like version constraints, git, directory, and http sourced
collections.

```yaml
# requirements.yml
collections:
  - name: community.general
    version: ">=10.0.0,<12.0.0"
  - name: cisco.ios
    version: 11.6.0
  - ansible.posix
  - name: git@github.com:ansible-collections/ansible.windows.git
    type: git
    version: 3.9.2
  - name: ./collections_src/corp/tools
    type: dir
  - name: https://artifacts.example.com/corp-net-2.1.0.tar.gz
    type: url
```

To add the collections above into `alai.toml` and the lock file you can run:

```console
$ alai add 'community.general>=10.0.0,<12.0.0'
$ alai add 'cisco.ios==11.6.0'
$ alai add ansible.posix
$ alai add 'git+ssh://git@github.com/ansible-collections/ansible.windows.git@3.9.2'
$ alai add ./collections_src/corp/tools
$ alai add https://artifacts.example.com/corp-net-2.1.0.tar.gz
```

When added, the `alai.toml` will look like the following.

```toml
# alai.toml
[project]
collections = [
    "ansible.posix",
    "ansible.windows",
    "cisco.ios==11.6.0",
    "community.general>=10.0.0,<12.0.0",
    "corp.net",
    "corp.tools",
]

[project.sources]
"ansible.windows" = { git = "ssh://git@github.com/ansible-collections/ansible.windows.git", rev = "3.9.2" }
"corp.tools" = { path = "collections_src/corp/tools" }
"corp.net" = { url = "https://artifacts.example.com/corp-net-2.1.0.tar.gz" }
```

The `alai.toml` can also be edited manually, but `alai add` configures the
different source types for you.

## Differences

### There's a lock file

ansible-galaxy installs whatever versions fit when it runs, so two runs a
month apart can install different collections. Instead, alai stores the
resolved versions in `alai.lock`, and any future `alai sync` runs will
continue to use those versions. This ensures every machine and CI run gets
the same versions and no silent upgrades. Upgrading a collection in the lock
can be done with the `-U` flag for all collections or `-P NAME` for a single
collection.

```console
# See what upgrading every collection would change, without writing alai.lock
$ alai lock -U --check

# Upgrade every collection and install the new versions
$ alai sync -U

# Upgrade one collection and install it
$ alai sync -P community.general

# Upgrade the collections in a group
$ alai sync --upgrade-group test

# Upgrade every collection in alai.lock only, without installing
$ alai lock -U
```

Each version still has to fit what `alai.toml` asks for. To go past that,
change the requirement with `alai add`, such as
`alai add 'community.general>=12'`.

### Collections live in the project

ansible-galaxy installs into the first entry of Ansible's collections path,
which by default is `~/.ansible/collections`. As this is a shared location,
every project on the machine will see any updates, and one project could
impact another. alai instead installs the collections into the project
directory, `collections/ansible_collections/` by default, so each project
has its own versions.

Using [alai run](cli.md#alai-run) is also recommended to isolate the Ansible
run with only your collections, so that other shared locations won't also be
seen.

```console
# Runs ansible-playbook that can only see your project collections
$ alai run ansible-playbook site.yml
```

### `sync` removes what the lock doesn't list

`alai sync` makes the collections directory match `alai.lock`, so
collections installed there some other way, with ansible-galaxy or
`alai collection install`, are removed. Use `alai add` for anything the
project needs, `--inexact` to keep the extras for one run, or a different
directory for collections managed by other tools. A collection you commit
to the project belongs in a directory of its own, added as a source (see
[Collections in your own repository](collection-sources.md#collections-in-your-own-repository)).

### Edited collections aren't replaced

`alai sync` checks an installed collection by its version, not its files.
If you edit files in an installed collection, its version still matches
`alai.lock`, so `alai sync` leaves your edits in place. ansible-galaxy does
the same and needs `--force` to reinstall. With alai, use
`alai sync --reinstall-collection NAME` for one collection, or `--reinstall`
for all of them.

If the collection was installed with `hardlink` or `symlink`, which `auto`
uses on filesystems like ext4, the edit is also in alai's cache, and a
reinstall brings it back. See [`link_mode`](alai-toml.md#link_mode) for how
to get a clean copy.

### Source checkouts in the collections directory

As alai manages the collections directory in the project, you should not clone
custom collections into it. ansible-galaxy leaves a checkout there alone, but
`alai sync` makes the directory hold only the collections in the lock file.
A checkout there is replaced or removed, and any uncommitted changes are lost.

Keep the collections in a different directory and add it as an
[editable source](collection-sources.md#collections-in-your-own-repository)
instead.

```console
$ alai add --editable ./collections_src/corp/tools
```

### Pre-releases

ansible-galaxy ignores pre-releases unless given `--pre`, the pre-release was
pinned exactly, or no stable version exists. alai chooses a pre-release
whenever no stable version fits the version bounds. For example, with `2.5.0`
and `3.0.0-rc.1` released, `>2.5.0` gives the release candidate, whereas
ansible-galaxy would have failed without the `--pre` flag.

### `requires_ansible`

ansible-galaxy ignores a collection's `requires_ansible` when installing and
will select the latest version that fits the dependency map. With
[`requires_ansible`](alai-toml.md#projectrequires_ansible) set in
`alai.toml`, alai doesn't choose a version whose own `requires_ansible`
starts later than the project's, and warns about one whose range ends inside
the project's.

### Version requirements are stricter

`alai.toml` takes ansible-galaxy's requirement syntax but without forms that
read as something else. For example a bare version (`1.2.3`) or `=1.2.3` needs
`==1.2.3`, and `^` and `~`, which ansible-galaxy fails on, need writing as
ranges. Collections' own dependencies are read exactly as ansible-galaxy reads
them to ensure existing collections are installable. See
[Version requirements](alai-toml.md#version-requirements).

### Indexes, not Galaxy servers

alai reads collections from an index, by default `index.alai.sh`, and not
servers that use the Galaxy API. Galaxy servers set with `--server`,
`source:` or `ansible.cfg`'s `[galaxy]` settings are not usable, which means
Automation Hub and private Galaxy servers can't be installed from.

### `ansible.cfg` isn't read

alai uses an `ansible.cfg` only to find the project's root. Its settings,
such as `collections_path` or Galaxy servers, aren't read. Only alai's own
settings are read, from [`alai.toml`](alai-toml.md), environment variables and
flags.

If your `ansible.cfg` sets `collections_path` to somewhere other than
`collections/`, set alai's
[`collections_dir`](alai-toml.md#projectcollections_dir) to the same place.

### Not supported yet

Currently alai does not support the following features of `ansible-galaxy`:

- Roles.
- Verifying signatures.
- Reading `requirements.yml`.
