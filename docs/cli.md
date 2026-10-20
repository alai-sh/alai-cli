<!-- Written by scripts/cli_reference.py from alai's argument parser: edit the help there, not here. -->

# CLI reference

Every alai command, with what it does and the options it takes, as
`alai <command> --help` shows them. The recordings were made against a
local index when these docs were built, so their output is alai's own,
though the collections and timings are only examples.

## alai

A fast package manager for Ansible collections.

```console
$ alai [OPTIONS] <COMMAND>
```

| Command | Description |
|---|---|
| [`add`](#alai-add) | Add collections to alai.toml, then lock and sync |
| [`collection`](#alai-collection) | Install or uninstall collections outside the project's requirements and alai.lock |
| [`config`](#alai-config) | Inspect alai's settings |
| [`lock`](#alai-lock) | Resolve the project's collections into alai.lock |
| [`remove`](#alai-remove) | Remove collections from alai.toml, then lock and sync |
| [`generate-shell-completion`](#alai-generate-shell-completion) | Print the script that enables Tab completion in a shell |
| [`sync`](#alai-sync) | Install the collections alai.lock lists, locking first if needed |

### Options

| Option | Description |
|---|---|
| `-V`, `--version` | Show the alai version and exit |

## Global options

Every command takes these, before or after its name.

| Option | Description |
|---|---|
| `--project DIR` | Find the project from DIR |
| `--config-file PATH` | Read only this config file |
| `--no-config` | Don't read any config files |
| `--cache-dir DIR` | The cache directory |
| `-v`, `--verbose` | Show debug detail; `-vv` also traces each step |
| `-q`, `--quiet` | Only show errors |
| `--color {auto,always,never}` | Colour the output: auto, the default, colours it on a terminal (env: `NO_COLOR`, `FORCE_COLOR`) |
| `--no-progress` | Don't draw progress while working (env: `ALAI_NO_PROGRESS`) |

## alai add

Add collections to the project's alai.toml, then lock and install them. A collection given by name alone is written as >= the version locked for it. Every other line of alai.toml is left as it is.

[Adding collections to a new project](casts/add.cast){ .alai-cast }

[Upgrading a locked collection while adding another](casts/add-upgrade.cast){ .alai-cast }

Here `ansible.posix` is locked at 2.1.0, and 2.2.2 has since been released.
{: .alai-cast-description }

Adding `cisco.ios` leaves `ansible.posix` at 2.1.0. `cisco.ios` needs `ansible.posix>=2.0.0`, which 2.1.0 still satisfies, and alai doesn't change a locked version that still works, even when a newer one is out.
{: .alai-cast-description }

Adding `community.general -P ansible.posix` adds `community.general` and upgrades `ansible.posix`, and only `ansible.posix`, to the newest version that fits, 2.2.2. `-P` takes the name of the collection to upgrade, which needn't be one being added: here it's already in `alai.toml`. Give `-P` again to upgrade more than one, or use `-U` to upgrade every collection.
{: .alai-cast-description }

```console
$ alai add [OPTIONS] REQUIREMENT [REQUIREMENT ...]
```

### Arguments

| Argument | Description |
|---|---|
| `REQUIREMENT` | A collection, such as ansible.posix or 'cisco.ios>=11' |

### Options

| Option | Description |
|---|---|
| `--group NAME` | Add them in group NAME rather than the project. Can't be used with `--dev` |
| `--dev` | The same as `--group dev`. Can't be used with `--group` |
| `--no-sync` | Lock, but don't install. Can't be used with `--frozen` |
| `--frozen` | Only edit alai.toml, without locking or installing. Can't be used with `--no-sync` |
| `--locked` | Resolve, and exit 1 without writing anything if alai.lock is missing or would change |
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |
| `--reinstall` | Reinstall every collection, even those already at the right version |
| `--reinstall-collection NAME` | Reinstall collection NAME, even if it's already at the right version; can be repeated |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai collection

Install or uninstall collections in the collections directory by hand, as uv pip does for packages. The project's requirements and alai.lock aren't used or changed, though an alai.toml, if the project has one, marks the project's directory and its settings apply, such as where collections are installed; the next alai sync puts the collections directory back as the lock has it.

```console
$ alai collection [OPTIONS] <COMMAND>
```

| Command | Description |
|---|---|
| [`install`](#alai-collection-install) | Install collections and what they need |
| [`uninstall`](#alai-collection-uninstall) | Uninstall collections |

Also takes the [global options](#global-options).

## alai collection install

Install collections and what they need into the collections directory, keeping installed versions that fit. Nothing else is removed. Outside a project, there's no collections directory to use, so `--collections-dir`, or the collections_dir setting, has to say where.

[Installing into a project's collections directory](casts/collection-install.cast){ .alai-cast }

An `alai.toml`, even an empty one, marks the project's directory, and collections are installed into `collections/` beside it.
{: .alai-cast-description }

[Installing from a directory inside the project](casts/collection-install-subdirectory.cast){ .alai-cast }

alai looks for the project from the current directory upwards, so run from `playbooks/`, it finds the `alai.toml` above and installs into the project's `collections/`, not one in `playbooks/`.
{: .alai-cast-description }

[Installing into a project marked by ansible.cfg](casts/collection-install-ansible-cfg.cast){ .alai-cast }

Without an `alai.toml`, an `ansible.cfg` marks the project's directory too, so collections still go into `collections/` beside it. alai only uses it to find the project; it doesn't read Ansible's settings from it.
{: .alai-cast-description }

[Installing outside a project](casts/collection-install-outside.cast){ .alai-cast }

With no `alai.toml`, `ansible.cfg` or `collections/ansible_collections` in the current directory or above it, there's no project, and alai won't guess where to install: `--collections-dir` says where.
{: .alai-cast-description }

```console
$ alai collection install [OPTIONS] REQUIREMENT [REQUIREMENT ...]
```

### Arguments

| Argument | Description |
|---|---|
| `REQUIREMENT` | A collection, such as ansible.posix or 'cisco.ios>=11' |

### Options

| Option | Description |
|---|---|
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |
| `--reinstall` | Reinstall every collection, even those already at the right version |
| `--reinstall-collection NAME` | Reinstall collection NAME, even if it's already at the right version; can be repeated |
| `--keep-unversioned` | Leave a collection whose version can't be read, such as a source checkout with no MANIFEST.json, as it is, as if it were the version asked for, rather than replace it |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai collection uninstall

Uninstall collections from the collections directory. What they depend on stays. Outside a project, there's no collections directory to use, so `--collections-dir`, or the collections_dir setting, has to say where.

[Uninstalling from a project's collections directory](casts/collection-uninstall.cast){ .alai-cast }

Only the collections named are uninstalled: the collections `cisco.ios` needed stay installed.
{: .alai-cast-description }

[Uninstalling a collection outside a project](casts/collection-uninstall-outside.cast){ .alai-cast }

```console
$ alai collection uninstall [OPTIONS] NAME [NAME ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NAME` | A collection name, such as ansible.posix |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |

Also takes the [global options](#global-options).

## alai config

Inspect alai's settings.

```console
$ alai config [OPTIONS] <COMMAND>
```

| Command | Description |
|---|---|
| [`show`](#alai-config-show) | Show the resolved settings and where they came from |

Also takes the [global options](#global-options).

## alai config show

Show the project, config files and settings alai resolves from the current directory. With KEY, print only that setting's value, for scripts.

[Showing the settings in effect](casts/config-show.cast){ .alai-cast }

```console
$ alai config show [OPTIONS] [key]
```

### Arguments

| Argument | Description |
|---|---|
| `key` | A setting named as in alai.toml, such as link_mode or project.collections_dir |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai lock

Resolve the collections the project's alai.toml asks for, its groups included, to one version of each, and write them to alai.lock beside it. Versions already in alai.lock are kept while they still fit.

[Upgrading one collection, then the rest](casts/lock.cast){ .alai-cast }

```console
$ alai lock [OPTIONS]
```

### Options

| Option | Description |
|---|---|
| `--check` | Resolve, but don't write alai.lock; exit 1 if it's missing or would change |
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |

### Settings

| Option | Description |
|---|---|
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai remove

Remove collections from the project's alai.toml, then lock and sync, which uninstalls what nothing needs any more. Every other line of alai.toml is left as it is.

[Removing a collection](casts/remove.cast){ .alai-cast }

```console
$ alai remove [OPTIONS] NAME [NAME ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NAME` | A collection name, such as ansible.posix |

### Options

| Option | Description |
|---|---|
| `--group NAME` | Remove them in group NAME rather than the project. Can't be used with `--dev` |
| `--dev` | The same as `--group dev`. Can't be used with `--group` |
| `--no-sync` | Lock, but don't install. Can't be used with `--frozen` |
| `--frozen` | Only edit alai.toml, without locking or installing. Can't be used with `--no-sync` |
| `--locked` | Resolve, and exit 1 without writing anything if alai.lock is missing or would change |
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |
| `--reinstall` | Reinstall every collection, even those already at the right version |
| `--reinstall-collection NAME` | Reinstall collection NAME, even if it's already at the right version; can be repeated |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai generate-shell-completion

Print the script that enables Tab completion for alai in SHELL.

```console
$ alai generate-shell-completion [OPTIONS] {bash,zsh,fish,powershell,tcsh}
```

### Arguments

| Argument | Description |
|---|---|
| `{bash,zsh,fish,powershell,tcsh}` | The shell to print the script for |

Also takes the [global options](#global-options).

## alai sync

Install the collections alai.lock lists into the collections directory, and remove those it doesn't, so the directory matches the lock. alai.lock is written first if it's missing or alai.toml has changed since. The project's collections are installed with the 'dev' group, if there is one.

[Installing a project's collections](casts/sync.cast){ .alai-cast }

```console
$ alai sync [OPTIONS]
```

### Options

| Option | Description |
|---|---|
| `--locked` | Resolve, and exit 1 rather than install if alai.lock is missing or would change. Can't be used with `--frozen` |
| `--frozen` | Install from alai.lock as it is, without checking it against alai.toml. Can't be used with `--locked` |
| `--inexact` | Keep installed collections alai.lock doesn't list, rather than removing them |
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |
| `--reinstall` | Reinstall every collection, even those already at the right version |
| `--reinstall-collection NAME` | Reinstall collection NAME, even if it's already at the right version; can be repeated |

### Groups

| Option | Description |
|---|---|
| `--group NAME` | Also install group NAME; can be repeated |
| `--no-group NAME` | Don't install group NAME, even if another option asks for it; can be repeated |
| `--only-group NAME` | Install only group NAME, without the project's own collections; can be repeated |
| `--all-groups` | Install every group |
| `--no-default-groups` | Don't install the 'dev' group |
| `--no-dev` | The same as `--no-group dev` |
| `--only-dev` | The same as `--only-group dev` |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).
