# Running Ansible

While alai attempts to work with Ansible out of the box there are scenarios
where it needs to configure the environment for Ansible to see the collections
it manages and ignore others. The [`alai run`](cli.md#alai-run) and
[`alai env`](cli.md#alai-env) commands are designed for this purpose.

```console
$ alai run ansible-playbook site.yml -i inventory.ini
```

The below example will set up a project that includes the `ansible.utils`
collection then run the adhoc ansible command with a module from that
collection:

[Running a module from a collection just added](casts/run.cast){ .alai-cast }

`alai run` first syncs the project, creating the lock file, and installing/
uninstalling any collections specified in the project as
[`alai sync`](cli.md#alai-sync) does. After this it will run the command
specified with various environment vars set that has Ansible find your
collections and no others. All the output, terminal signals, exit codes are
passed through making it act like command itself was run.

`alai env` is slightly different in that it does not perform a sync operation
but it can be used to output the environment variables that need to be set for
Ansible to see your collections without running an actual command straight up.
Use this to configure the shell or editor environment:

```console
$ eval "$(alai env)"
$ ansible-doc community.general.ufw
```

## Which collections Ansible sees

alai doesn't install or choose Ansible so it relies on it already being present
in the `PATH`. It is up to on how to install Ansible, whether it be through the
system packages, `pip` in a virtual environment, `pipx`, `uv`, or something
else.

Currently `alai run` sets the following two environment variables:

| Variable | Value | Why |
|---|---|---|
| `ANSIBLE_COLLECTIONS_PATH` | The [collections directory](projects.md#where-collections-are-installed) | Collections load from there, instead of `~/.ansible/collections` or the paths in `ansible.cfg` |
| `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH` | `False` | Collections installed in Python's `site-packages`, such as those the `ansible` package bundles, aren't loaded |

The environment variables that are set and what they are set to is not a public
interface and alai can change it at any time. It is only documented here to
help people understand how `alai run` and `alai env` works.

With the environment variables alai sets, only the
[collections_dir](alai-toml.md#projectcollections_dir) configured will be the
directory that Ansible sees. The environment overwrite overwrites the
`collections_path` that might be set in the `ansible.cfg` too.

The only except to this rule is the playbook adjacent `collections/` directory
that is always checked when run with `ansible-playbook`. There is no
configuration that can turn this off and is why alai recommended keeping the
default `collections_dir` which is that same directory.

alai will not check the Ansible version at runtime with the `requires_ansible`
configured for the project. Instead alai leaves that check for Ansible to
perform. You can use the
[COLLECTIONS_ON_ANSIBLE_VERSION_MISMATCH](https://docs.ansible.com/projects/ansible/latest/reference_appendices/config.html#collections-on-ansible-version-mismatch)
configuration option in ansible to have Ansible either issue a `warning`
(defualt), `ignore`, or `error` when loading a collection with an incompatible
`requires_ansible` version. Either set this through the `ansible.cfg:

```ini
[defaults]
collections_on_ansible_version_mismatch = "error"
```

Or use the environment variable below when calling Ansible:

```console
$ ANSIBLE_COLLECTIONS_ON_ANSIBLE_VERSION_MISMATCH=error \
    alai run ansible-playbook ...
```

## Collections the lock doesn't list

Unlike `alai sync`, `alai run` keeps collections in the collections
directory that `alai.lock` doesn't list, so a collection you are working
on before adding it to the lock file isn't deleted. Instead `alai run` will
issue a warning saying so but still continue allowing you to use those
collections:

```console
$ alai run ansible-playbook site.yml
warning: Ansible can load collections alai.lock doesn't list here, from collections: my.work
warning: alai run --exact or alai sync removes them
```

Use the `--exact` CLI argument to have `alai run` do a full sync like
`alai sync` does. The `--inexact` CLI argument will keep the existing behaviour
but hides the warning as well.

## Options

`alai run` takes the options of `alai sync` that choose what's
installed, for example the [groups](managing-collections.md#groups),
`--locked`, `--frozen`, the upgrade and reinstall options, and the index,
cache and collections directory settings.

| Option | |
|---|---|
| `--no-sync` | Run without locking or installing anything. Can't be used with `--locked` or `--frozen` |
| `--exact` | Remove installed collections `alai.lock` doesn't list before running, rather than warn of them |
| `--inexact` | Keep installed collections `alai.lock` doesn't list without warning of them. Can't be used with `--exact` |

`alai run` needs an `alai.toml` as without a project there's no
collections directory to point Ansible at.

## In a collection project

In a [collection project](projects.md#collection-project),
`alai run` and `alai env` set two more variables:

| Variable | Value | Why |
|---|---|---|
| `ANSIBLE_TEST_CONTENT_ROOT` | `<collections_dir>/ansible_collections/<namespace>/<name>`, the collection's link | ansible-test needs the collection at a path ending in `ansible_collections/<namespace>/<name>`; this gives it one wherever the checkout is |
| `PYDEVD_RESOLVE_SYMLINKS` | `1` | The collection is loaded through its link, so debuggers built on debugpy, such as VS Code's, show its files in the checkout rather than through the collection cache |

This allows tools like `ansible-test` to be run from anywhere rather than in
the expected `ansible_collections/<namespace>/<name>` directory.

```console
$ alai run ansible-test sanity --docker
$ alai run ansible-test integration --local my_target
$ alai run ansible-doc my_ns.my_col.my_module
```

!!! warning "`ansible-test units --local` and `--venv` don't work"

    `ansible-test units --local`, and every command with `--venv`, don't
    work in a collection project, as ansible-test resolves the
    collection's link. Use `--docker` instead.

To run a playbook stored in the collection's `playbooks/` directory, reference
it by its fully qualified name, `<namespace>.<name>.<playbook>` and not its
path:

```console
$ alai run ansible-playbook my_ns.my_col.playbook
```

If you were to run it using the path, any plugin short names will no longer
resolve to the fully qualified collection name of that enclosed collection.

[Running a collection's playbook by name](casts/run-collection-playbook.cast){ .alai-cast }

Because Ansible loads the collection project through its link in the
[collections directory](projects.md#collection-project), every path it displays
in the output will show that directory, for example
`~/.cache/alai/envs/<id>/ansible_collections/<namespace>/<name>/`, rather
than where you checked it out. This includes places like tracebacks, verbose
output, `ansible-doc`'s output, a plugin's `__file__` and ansible-test's
messages, etc. These files are the same ones as your local project it is just
Ansible that is unable to load them from that actual path.

## In a shell, CI or an editor

Use `alai env` to retrieve a set of environment variables you can use in the
shell or editor of your choice that can tell Ansible how to see the project's
collections. See [editors](editors.md) for more information around how to
configure a particular editor.

With `alai env` you can wrap the output in the `eval` equivalent of your shell.
The `alai env` command never locks or installs the collections so it is quick
enough to run whenever a shell enters the project. To ensure the environment
is sync'd correctly run `alai sync` before `alai env`:

```console
$ alai sync && eval "$(alai env)"
```

| Use | How |
|---|---|
| A POSIX shell | `eval "$(alai env)"` |
| fish | `alai env | source` |
| PowerShell | `alai env --output-format pwsh | Out-String | Invoke-Expression` |
| direnv | `eval "$(alai env)"` in `.envrc` |
| A CI step | `alai sync --locked`, then `alai env --output-format dotenv >> "$GITHUB_ENV"` on GitHub Actions |
| An editor | `alai env --output-format dotenv > .vscode/alai.env`, for editors that read `KEY=VALUE` lines; see [Editors](editors.md) for running and debugging Ansible in VS Code |

The `--output-format` argument can be set to one of the following: `sh`,
`fish`, `pwsh` or `dotenv`.
