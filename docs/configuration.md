# Configuration

alai reads settings from the command line, environment variables and TOML
config files. The layering follows uv's: the closer a source is to the
command being run, the higher its precedence.

1. Command-line flags.
2. `ALAI_*` environment variables.
3. The project config file, at the [project root](projects.md#finding-the-project-root).
4. The user config file.
5. The system config file.
6. Built-in defaults.

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

All of these files take the same settings. The project file can also have
the [`[project]` table](projects.md#the-project-table) and the
[manifest](projects.md#declaring-collections), which describe that one
project rather than how alai runs; in any other file, including a
`--config-file`, they're an error. Keys are snake_case, and the matching
command-line options are kebab-case: `link_mode` in a file, `--link-mode` on
the command line.

Every key is optional. A project file setting all of them, with what each
value means (see [Settings](#settings) and
[Declaring collections](projects.md#declaring-collections) for the details):

```toml
# alai.toml

# Top-level keys come before any [table] in TOML; written below one, a key
# belongs to that table instead.

# Where downloaded collections are cached, shared by every project. A
# relative path is relative to this file. Default: $XDG_CACHE_HOME/alai,
# or ~/.cache/alai.
cache_dir = "~/.cache/alai"

# How files get from the cache into the collections directory: "auto"
# (the default) tries "clone", then "hardlink", then "copy". "symlink" is
# opt-in only.
link_mode = "auto"

# Verify TLS certificates with the operating system's trust store, which
# includes roots your organisation installs. Set false to use OpenSSL's.
system_certs = true

# Settings that describe the project. Only the project's alai.toml can have
# this table.
[project]
# The directory holding ansible_collections/, relative to this file.
# Default: "collections" next to alai.toml.
collections_dir = "collections"
# The rest of this table, [groups] and [overrides] are the manifest: what
# the project needs. No command installs from them yet.
# The ansible-core versions the project targets, as a PEP 440 specifier.
requires_ansible = ">=2.17"
# The collections the project needs: [INDEX:]NAMESPACE.NAME[REQUIREMENT].
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "internal:corp.tools>=1.2",
]

# Collections only some workflows need, such as tests.
[groups.test]
collections = ["ansible.posix", "community.crypto"]

# Fixes for wrong metadata in the collections the project depends on.
[overrides]
# Replaces every requirement on ansible.netcommon, including those in other
# collections' dependencies. Collection names are quoted, as they hold a dot.
"ansible.netcommon" = ">=6"
# Collections whose own requires_ansible is ignored.
ignore_requires_ansible = ["some.collection"]

# An index to resolve collections from. Repeat [[index]] for more; they're
# searched in the order given, before the default index.
[[index]]
# Lowercase letters, digits, "-" and "_". Pin a requirement to this index
# with internal:namespace.name.
name = "internal"
# The index root, the directory holding config.json.
url = "https://ansible-index.example.com/"

[[index]]
name = "mirror"
url = "https://alai-mirror.example.com/"
# Use this index in place of the built-in default, https://index.alai.sh/.
default = true

# Never read by alai, at any level: room for other tools' settings.
[tool.my-ci]
deploy_target = "prod"
```

An unknown key or a value of the wrong type is an error naming the file, so a
typo never silently does nothing. The one exception is the `[tool]` table,
which alai never reads, at any level. This is reserved for future tools and
scripts so they can keep their own settings under `[tool.<name>]`, as in
`pyproject.toml`.

### Merging

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
  [`index`](#index)). The built-in default index goes last.

### Paths

A relative path in a config file is relative to the directory holding that
file. A relative path from a flag or environment variable is relative to the
current directory. A leading `~` expands to the home directory in both.

## Settings

| Key | Config files | Flag | Environment variable | Type | Default |
|---|---|---|---|---|---|
| [`project.collections_dir`](#projectcollections_dir) | **project only** | `--collections-dir` | `ALAI_COLLECTIONS_DIR` | [path](#paths) | `<project root>/collections` |
| [`cache_dir`](#cache_dir) | any | `--cache-dir` | `ALAI_CACHE_DIR` | [path](#paths) | `$XDG_CACHE_HOME/alai`, default `~/.cache/alai` |
| [`link_mode`](#link_mode) | any | `--link-mode` | `ALAI_LINK_MODE` | `auto`, `clone`, `hardlink`, `copy` or `symlink` | `auto` |
| [`system_certs`](#system_certs) | any | `--system-certs`, `--no-system-certs` | `ALAI_SYSTEM_CERTS` | boolean | `true` |
| [`index`](#index) | any | `--index` | `ALAI_INDEX` | array of index tables | none |
| none, see [`index`](#index) | | `--default-index` | `ALAI_DEFAULT_INDEX` | URL | `https://index.alai.sh/` |

"Config files" says which files can set the key. **Project only** keys are in
the `[project]` table, which only the project's `alai.toml` can have; a
`[project]` table in any other file is an error. Flags and environment
variables can always set them.

Settings that only make sense for alai itself have no config key:

| Flag | Environment variable | |
|---|---|---|
| `--project <dir>` | `ALAI_PROJECT` | Start project discovery here instead of the current directory |
| `--config-file <path>` | `ALAI_CONFIG_FILE` | Read only this config file |
| `--no-config` | `ALAI_NO_CONFIG` | Read no config files |
| `-v`, `--verbose` | none | Show debug lines: what alai decided and why. `-vv` also shows trace lines for each step. Counted wherever it appears, so `alai -v config show -v` is `-vv`; more than two is an error |
| `-q`, `--quiet` | none | Show only errors. Can't be combined with `-v` |

An environment variable set to an empty string counts as unset. Boolean
variables take `1`, `true`, `yes` or `on`, and `0`, `false`, `no` or `off`,
in any case.

### `project.collections_dir`

The directory holding `ansible_collections/`; collections install to
`<collections_dir>/ansible_collections/<namespace>/<name>`. A value ending in
`ansible_collections` is taken as its parent, as Ansible does with its
collection paths. See
[Where collections are installed](projects.md#where-collections-are-installed)
for an example layout.

Unset, it's `collections` in the project root. A relative value in
`alai.toml` is relative to the project root, where that file is, and one
from `--collections-dir` or `ALAI_COLLECTIONS_DIR` is relative to the current
directory. It can name a directory outside the project, such as
`../collections` for a manifest kept in a subdirectory of the repository.

Only the project file, the environment and the command line can set it. In a
user or system file it would apply to every project, defeating the
per-project layout. For a path that only makes sense on one machine, such as
an image build installing to `/usr/share/ansible/collections`, use the flag
or the environment variable rather than committing it to `alai.toml`.

### `cache_dir`

Where downloaded artifacts, extracted collections and index metadata are
kept, shared by every project.

!!! warning "Keep the cache to the users who need it"

    alai trusts what's in its cache: anyone who can change it can change
    what alai installs. The default, under your home directory, is only
    writable by you. A `cache_dir` elsewhere, such as under `/tmp` or a
    directory shared between users or CI jobs, may be readable and writable
    by others, and it's up to you to restrict it to the users who need it.

### `link_mode`

How files get from the cache into `collections_dir`:

- `auto`, the default: try `clone`, then `hardlink`, then `copy`, using the
  first that works.
- `clone`: a copy-on-write reflink, where the filesystem supports one.
- `hardlink`: the file shares its data with the cache. Editing an installed
  file also edits the cached copy.
- `copy`: an ordinary copy.
- `symlink`: a link to the cached file. These break in execution
  environments and containers, so it's opt-in only.

Any other value uses that mode or fails. Setting `auto` at a higher level
brings the fallback back when a lower level picks a mode, for example
`--link-mode auto` in a project whose `alai.toml` sets `copy`.

### `system_certs`

How alai checks the certificates of the indexes it connects to over HTTPS:

- `true`, the default: with the operating system's trust store. On macOS
  and Windows the operating system verifies the certificate, so roots your
  organisation installs, such as for a proxy that inspects TLS traffic, are
  trusted as they are by your browser. On Linux and other systems it's the
  system's CA bundle, as OpenSSL finds it.
- `false`: with OpenSSL's default certificate paths, which on macOS and
  Windows may not include roots your organisation installs.

`SSL_CERT_FILE` or `SSL_CERT_DIR` in the environment, naming a CA bundle or
directory, take precedence over the trust store: alai verifies against that
bundle alone.

To capture alai's TLS traffic for debugging, set `SSLKEYLOGFILE` to a file
path in the environment, as for curl or a browser; alai writes each
session's keys to it and warns on every run while it's set. Anyone with that
file and a capture of the traffic can read it, so unset it when you're done.
It's deliberately not a setting in `alai.toml`.

### `index`

!!! warning "Only index.alai.sh is supported"

    The index format is still being developed and may change without notice.
    You can point alai at another index, such as a mirror of
    `index.alai.sh`, but a future release may stop reading it until it's
    updated to match.

The indexes collections are resolved from, in order:

```toml
[[index]]
name = "internal"
url = "https://ansible-index.example.com/"

[[index]]
name = "mirror"
url = "https://alai-mirror.example.com/"
default = true
```

| Key | Type | |
|---|---|---|
| `name` | string | Required. Lowercase letters, digits, `-` and `_`, starting with a letter or digit. Used to pin a requirement to an index (`internal:ns.name`) |
| `url` | string | Required. An `http://`, `https://` or `file://` URL of the index root, the directory holding `config.json`, without a query or fragment. A trailing `/` is added if missing |
| `default` | boolean | Optional, default `false`. Makes this index replace the built-in default index |

The final list is built from every level, highest first:

1. Indexes from `--index`, `ALAI_INDEX`, then the config files in precedence
   order. When two levels define an index with the same name, the higher one
   wins and the lower one is dropped. The same name twice in one file is an
   error.
2. The default index goes last: the highest-precedence index marked
   `default = true`, or else the built-in `alai` index at
   `https://index.alai.sh/`. `--default-index` and `ALAI_DEFAULT_INDEX` add
   an index marked `default = true` at their level. Other indexes marked
   `default = true` are dropped, since only one index can replace the default.

A top-level requirement comes from its pinned index, or else from the first
index in this list that has it.

`--index` and `ALAI_INDEX` take `NAME=URL`. `ALAI_INDEX` holds several,
separated by whitespace; the flag can be repeated. `--default-index` and
`ALAI_DEFAULT_INDEX` take `NAME=URL` or a bare URL. A bare URL keeps the name
`alai`, so a mirror of `index.alai.sh` set this way still answers `alai:`
pins.
