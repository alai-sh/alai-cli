# Configuration

alai reads settings from the command line, environment variables and
[`alai.toml`](alai-toml.md) files. The layering follows uv's: the closer a
source is to the command being run, the higher its precedence.

1. Command-line flags.
2. `ALAI_*` environment variables.
3. The project's `alai.toml`, at the [project root](projects.md#finding-the-project-root).
4. The user config file.
5. The system config file.
6. Built-in defaults.

Every setting, with its values, default, flag and environment variable, is
listed in the [`alai.toml` reference](alai-toml.md#settings). This page
covers where they can be set and how they combine.

## Config files

| Level | Location | Used when |
|---|---|---|
| Project | `alai.toml` at the [project root](projects.md#finding-the-project-root) | The project root has one |
| User | `$XDG_CONFIG_HOME/alai/alai.toml`, default `~/.config/alai/alai.toml` | The file exists |
| System | The first `<dir>/alai/alai.toml` for each `<dir>` in `$XDG_CONFIG_DIRS` (default `/etc/xdg`), else `/etc/alai/alai.toml` | The file exists |

An `XDG_*` variable holding a relative path is ignored, as the XDG spec says.

`--config-file <path>` (`ALAI_CONFIG_FILE`) reads only that file, in place
of the project, user and system files. `--no-config` (`ALAI_NO_CONFIG`)
reads no files.

All of these files take the same settings, the top-level keys and
`[[index]]`. Only the project's own `alai.toml` can also have
[`[project]`](alai-toml.md#project), which describes that project rather
than how alai runs; in any other file, including a `--config-file`, it's an
error. `--no-config` and `--config-file` change which settings are read, not
the project: commands that need what `[project]` says read it from the
project's `alai.toml` either way.

An unknown key or a value of the wrong type is an error naming the file, so a
typo never silently does nothing. The one exception is
[`[tool]`](alai-toml.md#tool), which alai never reads, at any level.

## Merging

Settings from every file are merged, as uv does:

- a string, number or boolean set at more than one level takes the value
  from the highest level;
- arrays are concatenated, highest level first.

Environment variables and flags sit above the files under the same rules:
an environment variable overrides every file, and a flag overrides the
environment variable.

For example, with a user file and a project file that both set `link_mode`
and an `internal` index:

```toml
# ~/.config/alai/alai.toml
link_mode = "copy"

[[index]]
name = "internal"
url = "https://old-index.example.com/"
```

```toml
# alai.toml
link_mode = "hardlink"

[[index]]
name = "internal"
url = "https://ansible-index.example.com/"
```

```console
$ ALAI_LINK_MODE=symlink alai -v config show --index staging=https://staging.example.com/
Project
  root  ~/project (found by alai.toml)

Config files, highest precedence first
  project  ~/project/alai.toml
  user     ~/.config/alai/alai.toml
  system   none, looked for /etc/xdg/alai/alai.toml, /etc/alai/alai.toml

Settings
  project.collections_dir  ~/project/collections  default
  cache_dir                ~/.cache/alai          default
  link_mode                symlink                ALAI_LINK_MODE
                           hardlink               ~/project/alai.toml, overridden
                           copy                   ~/.config/alai/alai.toml, overridden
  system_certs             true                   default

Indexes, in resolution order
  staging   https://staging.example.com/        --index
  internal  https://ansible-index.example.com/  ~/project/alai.toml
  alai      https://index.alai.sh/              built-in, default index
```

Each setting shows where its value came from: a flag, an environment
variable, a file, or `default`. `-v` adds the values a higher level
overrode, the files looked for but not found, and, on stderr, a debug line for
each index dropped (here the user file's `internal`).

- `link_mode` is `symlink`: the environment variable beats both files, and
  the project file's `hardlink` beats the user file's `copy`. Adding
  `--link-mode clone` would make it `clone`.
- The `--index` index comes first, then the file indexes. Both files define
  `internal`, so the project's URL is kept and the user's is dropped (see
  [Indexes](#indexes)). The built-in default index goes last.

### Indexes

The [`[[index]]`](alai-toml.md#index) tables from every level make one list,
built highest level first:

1. Indexes from `--index`, `ALAI_INDEX`, then the config files in precedence
   order. When two levels define an index with the same name, the higher one
   wins and the lower one is dropped. The same name twice in one file is an
   error.
2. The default index goes last: the highest-precedence index marked
   `default = true`, or else the built-in `alai` index at
   `https://index.alai.sh/`. `--default-index` and `ALAI_DEFAULT_INDEX` add
   an index marked `default = true` at their level. Other indexes marked
   `default = true` are dropped, since only one index can replace the default.

A collection comes from the index its
[`[project.sources]`](alai-toml.md#projectsources) entry names. Otherwise it
comes from the first index in this list that has it, whether the project
lists it or another collection depends on it, and only that index's versions
are considered: a later index can't shadow it, even with a newer version.

## Paths

A relative path in a config file is relative to the directory holding that
file. A relative path from a flag or environment variable is relative to the
current directory. A leading `~` expands to the home directory in both.

## Flags and environment variables

Every setting can also be given for one run with a command-line flag or an
`ALAI_*` environment variable, which take precedence over every file. Each
setting's are listed with it in the [`alai.toml` reference](alai-toml.md).
Flags are only accepted by the commands that use them, and
`project.collections_dir`, though only the project's `alai.toml` can set it,
can still be given this way for any run.

### Environment variables

An environment variable set to an empty string counts as unset, so the
setting comes from the files or its default. Boolean variables take `1`,
`true`, `yes` or `on`, and `0`, `false`, `no` or `off`, in any case.

### Boolean flags

A boolean setting has a pair of flags, such as `--system-certs` and
`--no-system-certs`, so a run can override a file or environment variable
either way. When both are given, the last one wins, so a flag added after a
wrapper's or alias's own flags takes effect.

## Options that aren't settings

These control where settings come from, or alai's output, so they have no
`alai.toml` key:

| Flag | Environment variable | |
|---|---|---|
| `--project <dir>` | `ALAI_PROJECT` | Start [project discovery](projects.md#finding-the-project-root) here instead of the current directory |
| `--config-file <path>` | `ALAI_CONFIG_FILE` | Read only this config file |
| `--no-config` | `ALAI_NO_CONFIG` | Read no config files |
| `-v`, `--verbose` | none | Show debug lines: what alai decided and why. `-vv` also shows trace lines for each step. Counted wherever it appears, so `alai -v config show -v` is `-vv`; more than two is an error |
| `-q`, `--quiet` | none | Show only errors. Can't be combined with `-v` |
