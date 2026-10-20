# alai.toml

`alai.toml` is alai's TOML file. The same file name is used for the
project's file, at the [project root](projects.md#finding-the-project-root),
and for the user and system config files; [Configuration](configuration.md)
covers where each is and how their settings combine.

There's one rule for what can go where:

- **Top-level keys and `[[index]]` are settings**: how alai runs. Any
  `alai.toml` can set them, and each has a command-line flag and an
  environment variable too, listed with it below. How the levels combine is
  in [Configuration](configuration.md).
- **`[project]`, and everything under it, describes the project.** Only the
  project's own `alai.toml` can have it. In a user or system file it would
  apply to every project, and a `--config-file` only holds settings, so in
  any other file it's an error.

Every key is optional. An unknown key, or a value of the wrong type, is an
error naming the file, so a typo never silently does nothing. Keys are
snake_case; the matching flags are kebab-case (`link_mode`, `--link-mode`).

```toml
# alai.toml

# Settings. Top-level keys come before any [table] in TOML: written below
# one, a key belongs to that table instead.
cache_dir = "~/.cache/alai"
link_mode = "auto"
system_certs = true

[[index]]
name = "internal"
url = "https://ansible-index.example.com/"

[[index]]
name = "mirror"
url = "https://alai-mirror.example.com/"
default = true

# The project. Only the project's own alai.toml can have these.
[project]
collections_dir = "collections"
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "corp.tools>=1.2",
]

[project.groups.test]
collections = ["ansible.posix", "community.crypto"]

[project.overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]

[project.sources]
"corp.tools" = { index = "internal" }

# Never read by alai, at any level: room for other tools' settings.
[tool.my-ci]
deploy_target = "prod"
```

## Settings

| Key | Type | |
|---|---|---|
| [`cache_dir`](#cache_dir) | [path](configuration.md#paths) | Where downloads, extracted collections and index data are kept |
| [`link_mode`](#link_mode) | string | How files get from the cache into the collections directory |
| [`system_certs`](#system_certs) | boolean | Verify TLS certificates with the operating system's trust store |
| [`offline`](#offline) | boolean | Use only what's cached, without connecting to any index |
| [`[[index]]`](#index) | array of tables | The indexes collections are resolved from, before the default one |
| [`[tool]`](#tool) | table | Ignored by alai, for other tools |

### `cache_dir`

- **Type:** [path](configuration.md#paths)
- **Default:** `$XDG_CACHE_HOME/alai`, else `~/.cache/alai`
- **Flag:** `--cache-dir <dir>`
- **Environment variable:** `ALAI_CACHE_DIR`

Where downloaded artifacts, extracted collections and index metadata are
kept, shared by every project.

!!! warning "Keep the cache to the users who need it"

    alai trusts what's in its cache: anyone who can change it can change
    what alai installs. The default, under your home directory, is only
    writable by you. A `cache_dir` elsewhere, such as under `/tmp` or a
    directory shared between users or CI jobs, may be readable and writable
    by others, and it's up to you to restrict it to the users who need it.

### `link_mode`

- **Type:** `auto`, `clone`, `hardlink`, `copy` or `symlink`
- **Default:** `auto`
- **Flag:** `--link-mode <mode>`
- **Environment variable:** `ALAI_LINK_MODE`

How files get from the cache into the
[collections directory](#projectcollections_dir):

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

- **Type:** boolean (TOML's `true` or `false`, not a string)
- **Default:** `true`
- **Flags:** `--system-certs`, `--no-system-certs`
- **Environment variable:** `ALAI_SYSTEM_CERTS`, a
  [boolean](configuration.md#environment-variables)

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

### `offline`

- **Type:** boolean (TOML's `true` or `false`, not a string)
- **Default:** `false`
- **Flags:** `--offline`, `--no-offline`
- **Environment variable:** `ALAI_OFFLINE`, a
  [boolean](configuration.md#environment-variables)

Use only index data and collections alai has already cached, without
connecting to any index. A command that needs something that isn't in the
cache fails, saying what it couldn't find. An index given as a `file://` URL is read from disk
either way.

### `[[index]]`

!!! warning "Only index.alai.sh is supported"

    The index format is still being developed and may change without notice.
    You can point alai at another index, such as a mirror of
    `index.alai.sh`, but a future release may stop reading it until it's
    updated to match.

- **Type:** array of tables, one per index
- **Default:** none; the built-in default index, `https://index.alai.sh/`,
  is always searched last
- **Flags:** `--index NAME=URL`, which can be repeated, and
  `--default-index [NAME=]URL`
- **Environment variables:** `ALAI_INDEX`, holding `NAME=URL` values
  separated by whitespace, and `ALAI_DEFAULT_INDEX`, holding `[NAME=]URL`

An index to resolve collections from. Repeat `[[index]]` for more; they're
searched in the order given, before the default index.

| Key | Type | |
|---|---|---|
| `name` | string | Required. Lowercase letters, digits, `-` and `_`, starting with a letter or digit. Used to take a collection from this index in [`[project.sources]`](#projectsources) |
| `url` | string | Required. An `http://`, `https://` or `file://` URL of the index root, the directory holding `config.json`, without a query or fragment. A trailing `/` is added if missing |
| `default` | boolean | Optional, default `false`. Makes this index replace the built-in default index |

`--default-index` and `ALAI_DEFAULT_INDEX` replace the default index. Given
a bare URL, the index keeps the name `alai`, so a mirror of `index.alai.sh`
set this way still answers `[project.sources]` entries naming `alai`.

How the indexes from every file, flag and environment variable combine into
one list is described in [Configuration](configuration.md#indexes).

### `[tool]`

Never read by alai, at any level. It's room for other tools and scripts to
keep their own settings under `[tool.<name>]`, as in `pyproject.toml`.

## Project

Every command checks that these keys are spelt right and have the right
types; the requirements themselves are checked when
[`alai lock`](projects.md#locking) or [`alai sync`](projects.md#installing)
resolves them.

| Key | Type | |
|---|---|---|
| [`project.collections_dir`](#projectcollections_dir) | [path](configuration.md#paths) | Where collections are installed |
| [`project.requires_ansible`](#projectrequires_ansible) | string | The ansible-core versions the project targets |
| [`project.collections`](#projectcollections) | array of [requirements](#requirements) | The collections the project needs |
| [`[project.groups.<name>]`](#projectgroupsname) | table per group | Collections only some workflows need |
| [`[project.overrides]`](#projectoverrides) | table | Fixes for wrong metadata in the collections you depend on |
| [`[project.sources]`](#projectsources) | table | Where particular collections come from |

Only `collections_dir` has a flag and an environment variable: the rest
describe what the project needs, so they only come from its `alai.toml`.

These are only read from the project's own `alai.toml`, the one that
[fixes the project root](projects.md#finding-the-project-root), whatever
`--no-config` or `--config-file` say: those change which settings alai reads,
not what the project needs. See [Projects](projects.md#declaring-collections)
for how they fit together.

TOML defines `[project]` from its sub-tables, so a file can have
`[project.groups.test]` or `[project.overrides]` without a `[project]` line.

### `project.collections_dir`

- **Type:** [path](configuration.md#paths)
- **Default:** `collections` in the project root
- **Flag:** `--collections-dir <dir>`
- **Environment variable:** `ALAI_COLLECTIONS_DIR`

The directory holding `ansible_collections/`; collections install to
`<collections_dir>/ansible_collections/<namespace>/<name>`. A value ending in
`ansible_collections` is taken as its parent, as Ansible does with its
collection paths. See
[Where collections are installed](projects.md#where-collections-are-installed)
for an example layout.

A relative value in `alai.toml` is relative to the project root, where that
file is, and one from the flag or environment variable is relative to the
current directory. It can name a directory outside the project, such as
`../collections` for a project file kept in a subdirectory of the
repository.

For a path that only makes sense on one machine, such as an image build
installing to `/usr/share/ansible/collections`, use the flag or the
environment variable rather than committing it to `alai.toml`.

### `project.requires_ansible`

- **Type:** string, a PEP 440 version specifier
- **Default:** none: any ansible-core

The ansible-core versions the project targets, as a
[PEP 440](https://peps.python.org/pep-0440/#version-specifiers) specifier
such as `>=2.17` or `>=2.17,<2.20`: the same syntax as `requires_ansible` in
a collection's `meta/runtime.yml`.

### `project.collections`

- **Type:** array of [requirements](#requirements)
- **Default:** none

The collections the project needs, as an array of
[requirements](#requirements). A collection can be listed once.

### `[project.groups.<name>]`

Collections only some workflows need, such as tests, in a table per group:

```toml
[project.groups.test]
collections = ["ansible.posix", "community.crypto>=2"]
```

| Key | Type | |
|---|---|---|
| `collections` | array of [requirements](#requirements) | The group's collections. A collection can be listed once per group, and in the project's `collections` as well |

Group names use lowercase letters, digits, `-` and `_`, starting with a
letter or digit. `alai sync` installs the `dev` group with the project's
collections and the others when asked for; see
[Groups](projects.md#groups).

### `[project.overrides]`

Fixes for wrong metadata in the collections the project depends on:

```toml
[project.overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]
```

| Key | Type | |
|---|---|---|
| `"<namespace.name>"` | [version requirement](#version-requirements) | Replaces every requirement on that collection, including those in other collections' dependencies. `"*"` accepts any version |
| `ignore_requires_ansible` | array of collection names | Collections whose own `requires_ansible` is ignored |

Collection names contain a dot, so they're quoted: `"ansible.netcommon" =
">=6"`. Unquoted, TOML reads `ansible.netcommon` as a table.

### `[project.sources]`

Where particular collections come from, by collection name:

```toml
[project.sources]
"corp.tools" = { index = "internal" }
"ansible.netcommon" = { index = "internal" }
```

| Value | |
|---|---|
| `{ index = "<name>" }` | Take the collection from the [`[[index]]`](#index) with that name, and only from there |

An entry applies wherever the collection appears, whether the project lists
it in `collections` or another collection depends on it, as
`ansible.netcommon` might here. Without an entry, a collection comes from the
first index that has it (see [Configuration](configuration.md#indexes)). Naming the index yourself is
also how to be sure a collection can't come from somewhere else that
happens to have one by the same name.

Collection names contain a dot, so they're quoted, as in
`[project.overrides]`.

## Requirements

A requirement is a collection name followed by an optional version
requirement, the same syntax as ansible-galaxy's `requirements.yml` and
`galaxy.yml`. Where the collection comes from is set separately, in
[`[project.sources]`](#projectsources).

```text
NAMESPACE.NAME[VERSION REQUIREMENT]
```

| Requirement | Means |
|---|---|
| `ansible.windows` | Any version |
| `community.general>=9,<11` | 9.0.0 or later, but before 11.0.0 |
| `community.general >= 9` | The same as `community.general>=9`: spaces are allowed around each part |

Collection names are lowercase: each of the namespace and the name is a
Python identifier that isn't a Python keyword.

### Version requirements

A version requirement is `*`, for any version, or one or more clauses
separated by commas, all of which must hold. A clause is an operator and a
version:

| Operator | Means |
|---|---|
| `==` | Exactly this version |
| `!=` | Any version but this one |
| `>=`, `>` | This version or later, or only later |
| `<=`, `<` | This version or earlier, or only earlier |

Collection versions are [semantic versions](https://semver.org/). A version
in a clause can leave out its minor and patch parts, which count as `0`:
`>=9` is `>=9.0.0`, and `==9` is exactly `9.0.0`. It can have a pre-release
part, as in `>=2.0.0-rc.1`, but not build metadata, since comparisons ignore
it: `==1.0.0` already matches `1.0.0+build1`.

This is the syntax ansible-galaxy uses for collection dependencies, with a
few forms left out because they read as something else:

| Written | Write instead |
|---|---|
| `1.2.3` or `=1.2.3` | `==1.2.3` |
| `^1.2.0`, `~1.2.0` | `>=1.2.0,<2.0.0`, `>=1.2.0,<1.3.0` |
| `==1.0.0+build1` | `==1.0.0` |
