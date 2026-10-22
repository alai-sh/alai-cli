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
| [`collection`](#alai-collection) | Install, uninstall and list collections outside the project's requirements and alai.lock |
| [`config`](#alai-config) | Inspect alai's settings |
| [`env`](#alai-env) | Print the environment variables alai run sets, for a shell to use |
| [`find`](#alai-find) | Find which collections have a plugin, role or playbook |
| [`lock`](#alai-lock) | Resolve the project's collections into alai.lock |
| [`remove`](#alai-remove) | Remove collections from alai.toml, then lock and sync |
| [`run`](#alai-run) | Run a command, such as ansible-playbook, with the project's collections |
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

Add collections to the project's alai.toml, then lock and install them. A collection given by name alone is written as >= the version locked for it. A directory, given with a / such as ./ns.col, is the collection it holds, from its galaxy.yml or a built one's MANIFEST.json: its name is added, and [project.sources] takes it from there. So is a built collection's tarball, by path, such as ns-col-1.0.0.tar.gz, or by https:// URL, and a git repository, as git+URL[@REV][#subdirectory=DIR&version=VERSION]. Every other line of alai.toml is left as it is.

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
| `REQUIREMENT` | A collection, such as ansible.posix or 'cisco.ios>=11', a directory holding one, such as ../ns.col, a built collection's tarball, by path or URL, or a git repository, such as git+https://github.com/org/repo@v1.0.0 |

### Options

| Option | Description |
|---|---|
| `--editable` | Add the directories given as editable, so the collection is linked to them rather than copied |
| `--group NAME` | Add them in group NAME rather than the project. Can't be used with `--dev` |
| `--dev` | The same as `--group dev`. Can't be used with `--group` |
| `--no-sync` | Lock, but don't install. Can't be used with `--frozen` |
| `--frozen` | Only edit alai.toml, without locking or installing. Can't be used with `--no-sync` |
| `--locked` | Resolve, and exit 1 without writing anything if alai.lock is missing or would change |
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |
| `--upgrade-group NAME` | Choose the newest version of every collection group NAME lists that fits; can be repeated |
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

Install, uninstall and list collections in the collections directory by hand, as uv pip does for packages. Installing and uninstalling don't use or change the project's requirements or alai.lock, though an alai.toml, if the project has one, marks the project's directory and its settings apply, such as where collections are installed; the next alai sync puts the collections directory back as the lock has it. Listing compares what's installed with alai.lock.

```console
$ alai collection [OPTIONS] <COMMAND>
```

| Command | Description |
|---|---|
| [`install`](#alai-collection-install) | Install collections and what they need |
| [`uninstall`](#alai-collection-uninstall) | Uninstall collections |
| [`list`](#alai-collection-list) | List the installed collections |

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
$ alai collection install [OPTIONS] [REQUIREMENT ...]
```

### Arguments

| Argument | Description |
|---|---|
| `REQUIREMENT` | A collection, such as ansible.posix or 'cisco.ios>=11'; a directory holding one, such as ../ns.col, which is copied again every time; a built collection's tarball, such as ns-col-1.0.0.tar.gz or an https:// URL to one; or a git repository, as git+URL[@REV][#subdirectory=DIR&version=VERSION] |

### Options

| Option | Description |
|---|---|
| `-e`, `--editable DIR` | Install the collection in DIR, a source or a built one, as a link to it, so edits there are seen; can be repeated |
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

Uninstall collections from the collections directory. What they depend on stays. A collection is given by its name, even one installed from a directory, a tarball or a git repository. Outside a project, there's no collections directory to use, so `--collections-dir`, or the collections_dir setting, has to say where.

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
| `NAME` | A collection name, such as ansible.posix, not the directory, tarball or git URL it was installed from |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--link-mode {auto,clone,hardlink,copy,symlink}` | How files get from the cache into the collections directory |

Also takes the [global options](#global-options).

## alai collection list

List the collections in the collections directory, whatever installed them, and, when the project has an alai.lock, how each compares with it: as locked, not in it, installed differently, or locked but not installed. Outside a project, there's no collections directory to use, so `--collections-dir`, or the collections_dir setting, has to say where.

[Listing a project's collections against its lock](casts/collection-list.cast){ .alai-cast }

`cisco.ios` and what it needs are locked. `community.docker` was installed by hand, `ansible.posix` was installed by hand at an older version, and `ansible.utils` was uninstalled, so `alai sync` would change all three.
{: .alai-cast-description }

```console
$ alai collection list [OPTIONS]
```

### Options

| Option | Description |
|---|---|
| `--output-format {text,json}` | Text, the default, or json for scripts |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |

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

## alai env

Print the environment variables that make Ansible load collections from the project's collections directory and nowhere else, as alai run sets them: eval "$(alai env)" in a shell. Nothing is locked or installed; a warning says when the collections directory doesn't match alai.lock.

```console
$ alai env [OPTIONS]
```

### Options

| Option | Description |
|---|---|
| `--output-format {sh,fish,pwsh,dotenv}` | What to print: sh, export lines for POSIX shells; fish or pwsh, the same for fish and PowerShell; or dotenv, KEY=VALUE lines for env files. fish when SHELL is fish, else sh |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai find

Find plugins, roles and playbooks by name in the indexes' plugin indexes, with the ones in installed collections and ansible.builtin first. QUERY is part of a name (user finds win_user too), a glob ('*_info', 'community.general.*'), or a FQCN, which shows that plugin's details: where its redirects lead, and how to install it.

[Finding plugins](casts/find.cast){ .alai-cast }

`community.general` 11.0.0 is installed. Its `docker_container` was removed in 2.0.0, but it still redirects to `community.docker`'s, so a playbook that uses either name keeps working once `community.docker` is installed. `to_` matches part of each filter's name, underlined on a terminal.
{: .alai-cast-description }

[A plugin's details and where its redirects lead](casts/find-detail.cast){ .alai-cast }

```console
$ alai find [OPTIONS] QUERY
```

### Arguments

| Argument | Description |
|---|---|
| `QUERY` | Part of a name, a glob, or a FQCN |

### Options

| Option | Description |
|---|---|
| `-t`, `--type TYPE` | Only this type, as ansible-doc `-t` names them (module, filter, lookup...), or role or playbook; can be repeated |
| `-x`, `--exact` | Match whole names only, not names that contain QUERY |
| `--installed` | Only plugins in installed collections and ansible.builtin |
| `--include-removed` | List removed plugins that match part of a name too; ones named exactly always are |
| `--limit N` | List at most N matches from collections that aren't installed, 0 for all; 10 on a terminal, all when the output is piped or `--output-format` is json |
| `--output-format {text,json}` | Text, the default, or json for scripts |

### Settings

| Option | Description |
|---|---|
| `--collections-dir DIR` | Install collections under DIR/ansible_collections |
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
| `--upgrade-group NAME` | Choose the newest version of every collection group NAME lists that fits; can be repeated |

### Settings

| Option | Description |
|---|---|
| `--system-certs`, `--no-system-certs` | Verify TLS certificates with the operating system's trust store (the default) |
| `--offline`, `--no-offline` | Use only what's cached, without connecting to any index |
| `--index NAME=URL` | An index to resolve from, before those in config files; can be repeated |
| `--default-index [NAME=]URL` | Replace the default index |

Also takes the [global options](#global-options).

## alai remove

Remove collections from the project's alai.toml, then lock and sync, which uninstalls what nothing needs any more. A collection is given by its name, even one added from a directory, a tarball or a git repository, whose [project.sources] entry goes with it. Every other line of alai.toml is left as it is.

[Removing a collection](casts/remove.cast){ .alai-cast }

```console
$ alai remove [OPTIONS] NAME [NAME ...]
```

### Arguments

| Argument | Description |
|---|---|
| `NAME` | A collection name, such as ansible.posix, not the directory, tarball or git URL it was added from |

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
| `--upgrade-group NAME` | Choose the newest version of every collection group NAME lists that fits; can be repeated |
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

## alai run

Install what alai.lock lists, locking first if needed, as alai sync does, then run COMMAND with Ansible loading collections from the project's collections directory and nowhere else. Collections there that alai.lock doesn't list are kept, with a warning unless `--inexact` is given, or removed with `--exact`. Everything after COMMAND is passed to it.

[Running an Ansible module from a collection just added](casts/run.cast){ .alai-cast }

`alai run` runs Ansible where it can find the collections alai manages.
{: .alai-cast-description }

```console
$ alai run [OPTIONS] COMMAND ...
```

### Arguments

| Argument | Description |
|---|---|
| `COMMAND` | The command to run, found on PATH |
| `ARG` | The command's arguments |

### Options

| Option | Description |
|---|---|
| `--locked` | Resolve, and exit 1 rather than install if alai.lock is missing or would change. Can't be used with `--frozen` or `--no-sync` |
| `--frozen` | Install from alai.lock as it is, without checking it against alai.toml. Can't be used with `--locked` or `--no-sync` |
| `--no-sync` | Run without locking or installing anything first. Can't be used with `--locked` or `--frozen` |
| `--exact` | Remove installed collections alai.lock doesn't list, as alai sync does, rather than warn of them. Can't be used with `--inexact` |
| `--inexact` | Keep installed collections alai.lock doesn't list without warning of them. Can't be used with `--exact` |
| `-U`, `--upgrade` | Choose the newest versions that fit, ignoring alai.lock |
| `-P`, `--upgrade-collection NAME` | Choose the newest version of collection NAME that fits; can be repeated |
| `--upgrade-group NAME` | Choose the newest version of every collection group NAME lists that fits; can be repeated |
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
| `--upgrade-group NAME` | Choose the newest version of every collection group NAME lists that fits; can be repeated |
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
