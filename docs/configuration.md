# Configuration

alai reads settings from the command line, environment variables and
[`alai.toml`](alai-toml.md) files. The layering of config values uses the
following precedence.

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

If an `XDG_*` variable has a relative path, it is ignored, as per the XDG spec.

All of these files take the same settings, the top-level keys and
`[[index]]`. Only the project's own `alai.toml` can also have
[`[project]`](alai-toml.md#project), which describes that project rather
than how alai runs. If `[project]` is defined in any other file, including a
`--config-file`, it's an error. `--no-config` and `--config-file` change which
settings are read, not the project details.

An unknown key or a value of the wrong type is an error naming the file, so a
typo never silently does nothing. The one exception is custom
[`[tool.*]`](alai-toml.md#tool) entries, which alai ignores.

## Merging

Settings from every level are merged with the following rules:

- a string, number or boolean set at more than one level takes the value
  from the highest level
- the `[[index]]` tables are combined into one list, highest level first
  (see [Indexes](#indexes))

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
$ ALAI_LINK_MODE=clone alai -v config show --index staging=https://staging.example.com/
debug: Ignoring index 'internal' from ~/.config/alai/alai.toml, already defined at a higher level
Project
  root  ~/project (found by alai.toml)

Config files, highest precedence first
  project  ~/project/alai.toml
  user     ~/.config/alai/alai.toml
  system   none, looked for /etc/xdg/alai/alai.toml, /etc/alai/alai.toml

Settings
  project.collections_dir  ~/project/collections  default
  cache_dir                ~/.cache/alai          default
  link_mode                clone                  ALAI_LINK_MODE
                           hardlink               ~/project/alai.toml, overridden
                           copy                   ~/.config/alai/alai.toml, overridden
  system_certs             true                   default
  offline                  false                  default

Indexes, in resolution order
  staging   https://staging.example.com/        --index
  internal  https://ansible-index.example.com/  ~/project/alai.toml
  alai      https://index.alai.sh/              built-in, default index
```

Each setting shows where its value came from, such as a flag, an environment
variable, a file, or the default. Running with `-v` also shows the values a
higher level overrode, and the files looked for but not found.

### Indexes

The [`[[index]]`](alai-toml.md#index) tables from every level make one list,
built highest level first:

1. Indexes from `--index`, `ALAI_INDEX`, then the config files in precedence
   order. When two levels define an index with the same name, the higher one
   wins and the lower one is dropped. The same name twice in one file is an
   error.
2. The default index goes last. If more than one level replaces it, the
   highest one wins.

A collection comes from the first index that contains that collection, unless
[`[project.sources]`](alai-toml.md#projectsources) overrides the source. An
index later in the list can't shadow it, even with a higher version.

## Paths

A relative path in a config file is relative to the directory holding that
file. A relative path from a flag or environment variable is relative to the
current directory. A leading `~` expands to the home directory in both.

## Flags and environment variables

Every setting can also be given for one run with a command-line flag or an
`ALAI_*` environment variable, which take precedence over every file. Each
setting's flag and environment variable are listed with it in the
[`alai.toml` reference](alai-toml.md). Flags are only accepted by the
commands that use them. Only the project's `alai.toml` can set
`project.collections_dir`, but its flag and environment variable work for
any run.

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

These control where settings come from, alai's output or a safety limit,
so they have no `alai.toml` key:

| Flag | Environment variable | |
|---|---|---|
| `--project <dir>` | `ALAI_PROJECT` | Start [project discovery](projects.md#finding-the-project-root) here instead of the current directory |
| `--config-file <path>` | `ALAI_CONFIG_FILE` | Read only this config file |
| `--no-config` | `ALAI_NO_CONFIG` | Read no config files |
| `-v`, `--verbose` | none | Show debug lines: what alai decided and why. `-vv` also shows trace lines for each step. Counted wherever it appears, so `alai -v config show -v` is `-vv`; more than two is an error |
| `-q`, `--quiet` | none | Show only errors. Can't be combined with `-v` |
| `--color <when>` | `NO_COLOR`, `FORCE_COLOR` | Color the output: `auto` (the default) only when it goes to a terminal that isn't `TERM=dumb`, `always` or `never`. With `auto`, a non-empty `NO_COLOR` turns color off and a non-empty `FORCE_COLOR` turns it on; `NO_COLOR` wins if both are set |
| `--no-progress` | `ALAI_NO_PROGRESS` | Don't draw progress while resolving, downloading or installing. Progress is only drawn when the output goes to a terminal that isn't `TERM=dumb`, and never with `-q` |
| none | `ALAI_MAX_UNPACKED_SIZE` | The most a collection tarball may unpack to, in bytes, before alai refuses it. The default is `536870912` (512 MiB), and `0` means no limit. It stops a small hostile tarball from filling the disk |
