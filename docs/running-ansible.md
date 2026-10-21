# Running Ansible

`alai run` runs a command, such as `ansible-playbook`, so that Ansible
loads the project's collections and no others:

```console
$ alai run ansible-playbook site.yml -i inventory.ini
```

[Running a module from a collection just added](casts/run.cast){ .alai-cast }

It first installs what `alai.lock` lists, locking if needed, as
[`alai sync`](managing-collections.md#installing) does, then runs the
command, which behaves as if you'd run it directly: its output, input and
exit code are its own. Everything after the command is the command's own,
so no `--` is needed, and alai's options go before it.

`alai env` prints the same setup for a shell to use, so any command run
afterwards sees the project's collections, without `alai run`:

```console
$ eval "$(alai env)"
$ ansible-doc community.general.ufw
```

## Which collections Ansible sees

alai doesn't install or choose Ansible. The command runs with the
ansible-core, and the Python, that it finds on `PATH` as usual, whether
from a virtual environment, `uv run`, pipx or a system package. alai only
sets two environment variables:

| Variable | Value | Why |
|---|---|---|
| `ANSIBLE_COLLECTIONS_PATH` | The [collections directory](projects.md#where-collections-are-installed) | Collections load from there, instead of `~/.ansible/collections` or the paths in `ansible.cfg` |
| `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH` | `False` | Collections installed in Python's `site-packages`, such as those the `ansible` package bundles, aren't loaded |

So what runs is what `alai.lock` describes. Environment variables take
precedence over `ansible.cfg`, so a `collections_path` there is
overridden too.

`ansible-playbook` also searches a `collections/` directory beside each
playbook it's given, whatever these variables say. With playbooks at the
project root that's the collections directory itself, but a playbook
elsewhere with its own `collections/` can still load from it.

Ansible checks each collection's `requires_ansible` as it loads it, and
warns when the running ansible-core doesn't match. alai leaves that check
to it. Set `ANSIBLE_COLLECTIONS_ON_ANSIBLE_VERSION_MISMATCH=error` to make
it fail instead.

## Collections the lock doesn't list

Unlike `alai sync`, `alai run` keeps collections in the collections
directory that `alai.lock` doesn't list, so a collection you're working
on before adding it isn't deleted. Ansible can load them, so alai warns of each one:

```console
$ alai run ansible-playbook site.yml
warning: Ansible can load collections alai.lock doesn't list here, from collections: my.work
warning: alai run --exact or alai sync removes them
```

`--exact` removes them first, as `alai sync` does, and `--inexact` keeps
them without the warning, for when you know they're there.

## Options

`alai run` takes the options of `alai sync` that choose what's
installed: the [groups](managing-collections.md#groups), `--locked`,
`--frozen`, the upgrade and reinstall options, and the index, cache and
collections directory settings. It only reports what changed: nothing
when everything is already installed, and each collection's line only
with `-v`.

| Option | |
|---|---|
| `--no-sync` | Run without locking or installing anything. Can't be used with `--locked` or `--frozen` |
| `--exact` | Remove installed collections `alai.lock` doesn't list before running, rather than warn of them |
| `--inexact` | Keep installed collections `alai.lock` doesn't list without warning of them. Can't be used with `--exact` |

`alai run` needs an `alai.toml`: without a project there's no
collections directory to point Ansible at.

## In a collection project

In a [collection project](projects.md#collection-projects),
`alai run` and `alai env` set two more variables:

| Variable | Value | Why |
|---|---|---|
| `ANSIBLE_TEST_CONTENT_ROOT` | `<collections_dir>/ansible_collections/<namespace>/<name>`, the collection's link | ansible-test needs the collection at a path ending in `ansible_collections/<namespace>/<name>`; this gives it one wherever the checkout is |
| `PYDEVD_RESOLVE_SYMLINKS` | `1` | The collection is loaded through its link, so debuggers built on debugpy, such as VS Code's, show its files in the checkout rather than through the link |

So `ansible-test` runs from anywhere in the checkout, with the
collection's dependencies beside it:

```console
$ alai run ansible-test sanity --docker
$ alai run ansible-test integration --local my_target
$ alai run ansible-doc my_ns.my_col.my_module
```

!!! warning "`ansible-test units --local` and `--venv` don't work"

    `ansible-test units --local`, and every command with `--venv`, don't
    work in a collection project, as ansible-test resolves the
    collection's link: unit tests fail with `not found in collection
    path`, and `--venv` with `The current working directory must be
    within the source tree being tested`. Use `--docker` for those; the
    other `--local` commands work.

Run a playbook in the collection's `playbooks/` directory by its fully
qualified name, `<namespace>.<name>.<playbook>`, not its path, as anyone who
installs the collection would: `playbooks/site.yml` runs as
`alai run ansible-playbook my_ns.my_col.site`, and
`playbooks/deploy/web.yml` as `my_ns.my_col.deploy.web`. Run that way,
Ansible treats the playbook as part of the collection, so it can use the
collection's own modules and roles by their short names. Run by its path,
it's an ordinary playbook that happens to be in the checkout, and short
names for the collection's content don't resolve.

[Running a collection's playbook by name](casts/run-collection-playbook.cast){ .alai-cast }

Ansible loads the collection through its link in the
[collections directory](projects.md#collection-projects), so every path it
shows for the collection is under that directory, by default
`~/.cache/alai/envs/<id>/ansible_collections/<namespace>/<name>/`, rather
than where you checked it out: in tracebacks, `ansible-doc`'s output, a
plugin's `__file__` and ansible-test's messages. The files are the same
ones, so edits in the checkout still take effect. To run the collection
with paths in the checkout, use a playbook project instead, with the
collection installed into its collections directory from the checkout.
Installing a collection from a directory or a git repository isn't
supported yet; it's on the [roadmap](roadmap.md).

## In a shell, CI or an editor

`alai env` prints the variables as POSIX shell `export` lines, or as fish
`set -gx` lines when `SHELL` is fish; `--output-format pwsh` prints them
for PowerShell. It never locks or installs, so it's
quick enough to run whenever a shell enters the project, but it warns
when `alai.lock` is missing or out of date, a locked collection isn't
installed, or the collections directory holds collections the lock
doesn't list. To avoid those, run `alai sync` first, so the collections
directory matches the lock before the variables are set:

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

`--output-format` is `sh`, `fish`, `pwsh` or `dotenv`.
