# alai.toml

`alai.toml` is alai's project declaration, settings and/or marker file. The
file is written in the TOML format and should be placed at the
[project root](projects.md#finding-the-project-root), or used as a user or
system config file. [Configuration](configuration.md) covers where each of
the configuration files is located and how their settings combine with
project or CLI/environment values.

There is one rule for what can be defined in a project `alai.toml` vs a
settings/configuration `alai.toml`:

- **Top-level keys and `[[index]]` are settings.** These values define how
  alai runs. Any `alai.toml` can set them, and each has a command-line
  flag and an environment variable too.
- **`[project]`, and everything under it, describes the project.** Only the
  project's own `alai.toml` can have it. It cannot be set in the user or
  system configuration file.

Every key declared below is optional, and an empty `alai.toml` can still mark
the location of a project root. An unknown key, or a value of the wrong type, is an
error naming the file, so a typo never silently does nothing. Keys are written
in snake_case, matching their kebab-case CLI options
(`link_mode`, `--link-mode`).

```toml
# alai.toml

# Settings: Top-level keys come before any [table] in TOML
cache_dir = "~/.cache/alai"
link_mode = "auto"
system_certs = true

# Index: Declares custom indexes in priority order
[[index]]
name = "internal"
url = "https://ansible-index.example.com/"

[[index]]
name = "mirror"
url = "https://alai-mirror.example.com/"
default = true

# Project: Only the project's own alai.toml can have these
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
"corp.dev" = { path = "../corp.dev", editable = true }

# Never read by alai: Used to declare custom entries for future
# tools that may read alai.toml
[tool.my-ci]
deploy_target = "prod"
```

## Settings

| Key | Type | |
|---|---|---|
| [`cache_dir`](#cache_dir) | string | Where downloads, extracted collections and index data are kept, as a [path](configuration.md#paths) |
| [`link_mode`](#link_mode) | string | How files get from the cache into the collections directory |
| [`system_certs`](#system_certs) | boolean | Verify TLS certificates as the operating system does |
| [`offline`](#offline) | boolean | Use only what's cached, without connecting to any index |
| [`[[index]]`](#index) | array of tables | The indexes collections are resolved from, before the default one |
| [`[tool]`](#tool) | table | Ignored by alai, for other tools to add future configuration values |

### `cache_dir`

- **Type:** string, a [path](configuration.md#paths)
- **Default:** `$XDG_CACHE_HOME/alai`, else `~/.cache/alai`
- **Flag:** `--cache-dir <dir>`
- **Environment variable:** `ALAI_CACHE_DIR`

Where downloaded artifacts, extracted collections, and index metadata are
kept. The data in this cache is shared by every project.

!!! warning "Keep the cache to the users who need it"

    alai trusts what's in its cache. Anyone who can change it can change
    what alai installs. The default, under your home directory, is only
    writable by you. A `cache_dir` elsewhere, such as under `/tmp` or a
    directory shared between users or CI jobs, may be readable and writable
    by others, and it's up to you to restrict it to the users who need it.

### `link_mode`

- **Type:** string
- **Choices:** `auto`, `clone`, `hardlink`, `copy` or `symlink`
- **Default:** `auto`
- **Flag:** `--link-mode <mode>`
- **Environment variable:** `ALAI_LINK_MODE`

How files get from the cache into the
[collections directory](#projectcollections_dir):

- `auto`, the default: try `clone`, then `hardlink`, then `copy`, using the
  first that works.
- `clone`: a copy-on-write reflink, where the filesystem supports one.
- `hardlink`: the file shares its data with the cache, so editing an
  installed file in place also edits the cached copy (see below).
- `copy`: an ordinary copy.
- `symlink`: a link to the cached file, so the installed collection depends
  on the cache. Clearing the cache breaks it, and so does using it in an
  execution environment or container, where the cache isn't. It's opt-in
  only.

!!! warning "Editing installed files with `hardlink` or `symlink`"

    With either mode, an installed file is tied to the data in the cache.
    Editing a file in the project's collections directory will also edit the
    same file in the cache. Every later install of that collection version or
    existing projects that were installed with `hardlink` or `symlink` will
    also see those changes. To get a clean copy, delete
    `<cache_dir>/collections-v1/` and then run
    `alai sync --reinstall-collection NAME`.

    The `clone` and `copy` modes do not share anything with the cache. The `auto`
    mode tries `clone` first, but on a filesystem without copy-on-write,
    such as ext4, it falls back to `hardlink`.

!!! danger "Avoid `symlink`"

    A collection installed with `symlink` is linked to the cache directory and
    only stays valid while the cache contains the collection. Clearing the
    cache breaks every collection installed with `symlink`. It is recommended
    to use another mode unless you have a reason not to.

### `system_certs`

- **Type:** boolean (TOML's `true` or `false`, not a string)
- **Default:** `true`
- **Flags:** `--system-certs`, `--no-system-certs`
- **Environment variable:** `ALAI_SYSTEM_CERTS`, a
  [boolean](configuration.md#environment-variables)

Whether alai has TLS certificates verified as the operating system verifies
them, or verifies them itself using the built-in OpenSSL CA bundle that
alai's Python provides.

With `true`, the default:

- **macOS:** the operating system verifies the certificate, through the
  Security framework, as it does for Safari and other apps. This includes
  support for roots in the Keychain as well as trust settings made in Keychain
  Access.
- **Linux and other systems:** OpenSSL verifies the certificate against
  Python's configured CA bundle, falling back to the locations distributions
  usually keep theirs in, such as `/etc/ssl/certs/ca-certificates.crt`, if
  Python's is missing or empty.

With `false`, alai uses only OpenSSL's defaults, through Python's `ssl`
module:

- **macOS:** the bundle that Python's OpenSSL was built to read, never the
  Keychain. For a Python from python.org, that's empty until you run its
  *Install Certificates.command*. For Homebrew's Python, it's Homebrew's
  CA bundle.
- **Linux and other systems:** the CA bundle and directory compiled into the
  OpenSSL that alai's Python uses, and nowhere else. The actual bundle is
  controlled by many factors, such as what built Python in the first place,
  and Linux distribution configurations.

To see what bundles `false` will use, run
`python -c "import ssl; print(ssl.get_default_verify_paths())"` with the
Python alai runs on.

The `SSL_CERT_FILE` or `SSL_CERT_DIR` environment variables can be used to
override this behavior altogether. Set either to a CA bundle or directory to
have alai use that for TLS verification and ignore the `system_certs` option.

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
cache fails, saying what it couldn't find. This only applies to indexes over
`http://` or `https://`. An index given as a `file://` URL is read from disk
either way.

### `[[index]]`

!!! warning "Only index.alai.sh is supported"

    The index format is still being developed and may change without notice.
    You can point alai at another index, such as a mirror of
    `index.alai.sh`, but a future release may stop reading it until it's
    updated to match the expected behavior.

- **Type:** array of tables, one per index
- **Default:** none; the built-in default index, `https://index.alai.sh/`,
  is always searched last
- **Flags:** `--index NAME=URL`, which can be repeated, and
  `--default-index [NAME=]URL`
- **Environment variables:** `ALAI_INDEX`, holding `NAME=URL` values
  separated by whitespace, and `ALAI_DEFAULT_INDEX`, holding `[NAME=]URL`

An index to resolve collections from. Repeat `[[index]]` to specify multiple
indexes with the order defined being the priority order. Custom indexes are
prioritized before the default index.

| Key | Type | |
|---|---|---|
| `name` | string | Required. The name of the index. Use lowercase letters, digits, `-` and `_`, starting with a letter or digit. `alai` is the default index's name, so an index named `alai` must also set `default = true` |
| `url` | string | Required. An `http://`, `https://` or `file://` URL of the index root, the directory holding `config.json`, without a query or fragment. A trailing `/` is added if missing |
| `default` | boolean | Optional, default `false`. Makes this index replace the built-in default index |

`--default-index` and `ALAI_DEFAULT_INDEX` replace the default index. Given
a bare URL, the index keeps the name `alai`, so a mirror of `index.alai.sh`
set this way still answers `[project.sources]` entries naming `alai`.

See [Configuration](configuration.md#indexes) for more information on how
the various configuration sources are applied as one final value.

### `[tool]`

Never read by alai, at any level. It is left open for other tools and scripts
to keep their own settings under `[tool.<name>]` without being flagged by
alai as invalid.

## Project

| Key | Type | |
|---|---|---|
| [`project.collections_dir`](#projectcollections_dir) | string | Where collections are installed, as a [path](configuration.md#paths) |
| [`project.requires_ansible`](#projectrequires_ansible) | string | The ansible-core versions the project targets |
| [`project.collections`](#projectcollections) | array of strings | The collections the project needs, as [requirements](#requirements) |
| [`[project.groups.<name>]`](#projectgroupsname) | table | Collections only some workflows need, one table per group |
| [`[project.overrides]`](#projectoverrides) | table | Fixes for wrong metadata in the collections you depend on |
| [`[project.sources]`](#projectsources) | table | Where particular collections come from |

Only `collections_dir` has a CLI flag and an environment variable. The
remaining options describe only what the project needs, so they can only be
defined in that project's `alai.toml`. See
[Managing collections](managing-collections.md) for how a project declares the
collections it needs.

### `project.collections_dir`

- **Type:** string, a [path](configuration.md#paths)
- **Default:** for a playbook project, `collections` in the project root; in a
  [collection project](projects.md#collection-project), a
  directory of its own in the cache
- **Flag:** `--collections-dir <dir>`
- **Environment variable:** `ALAI_COLLECTIONS_DIR`

The directory to install the collections for the project into. This directory
will contain the `ansible_collections/` directory with collections being
installed at `<collections_dir>/ansible_collections/<namespace>/<name>`. A
value ending in `ansible_collections` is taken as its parent, as Ansible does
with its collection paths. See
[Where collections are installed](projects.md#where-collections-are-installed)
for an example layout.

A relative value in `alai.toml` is relative to where the file is located. One
from the flag or environment variable is relative to the current directory.

### `project.requires_ansible`

- **Type:** string, a PEP 440 version specifier
- **Default:** none: any ansible-core

The ansible-core versions the project targets, as a
[PEP 440](https://peps.python.org/pep-0440/#version-specifiers) specifier
such as `>=2.17` or `>=2.17,<2.20`. This is the same syntax as
`requires_ansible` in a collection's `meta/runtime.yml`.

This currently cannot be set in a
[collection project](projects.md#collection-project) and will raise
an error. Instead, collection projects use `meta/runtime.yml` to declare this
value.

### `project.collections`

- **Type:** array of strings, each a [requirement](#requirements)
- **Default:** none

The collections the project needs, as an array of
[requirements](#requirements).

This currently cannot be set in a
[collection project](projects.md#collection-project) and will raise
an error. Instead, collection projects use `galaxy.yml` to declare the
collection's dependencies.

### `[project.groups.<name>]`

Collections only some workflows need, such as tests, in a table per group:

```toml
[project.groups.test]
collections = ["ansible.posix", "community.crypto>=2"]
```

| Key | Type | |
|---|---|---|
| `collections` | array of strings | The group's collections, as [requirements](#requirements) |

Group names use lowercase letters, digits, `-` and `_`, starting with a
letter or digit. `alai sync` installs the `dev` group with the project's
collections by default and the others when asked for. See
[Groups](managing-collections.md#groups) for more details.

### `[project.overrides]`

Used to override the metadata that may be incorrect in the collections the
project depends on:

```toml
[project.overrides]
"ansible.netcommon" = ">=6"
ignore_requires_ansible = ["some.collection"]
```

| Key | Type | |
|---|---|---|
| `"<namespace.name>"` | string | A [version requirement](#version-requirements) that replaces every requirement on that collection, including those in other collections' dependencies. `"*"` accepts any version |
| `ignore_requires_ansible` | array of strings | The names of collections whose own `requires_ansible` is ignored |

As collection names contain a dot, they must be quoted: `"ansible.netcommon" =
">=6"`.

### `[project.sources]`

Where particular collections come from, keyed by collection name.
[Collection sources](collection-sources.md) has more information around the
different source types and when they should be used.

```toml
[project.sources]
"corp.tools" = { index = "internal" }
# From the default index only, even if an earlier index has it too
"ansible.netcommon" = { index = "alai" }
"corp.dev" = { path = "../corp.dev" }
"corp.lib" = { path = "../corp.lib", editable = true }
"corp.net" = { path = "dist/corp-net-2.1.0.tar.gz" }
"corp.web" = { url = "https://artifacts.example.com/corp-web-1.4.0.tar.gz" }
"corp.win" = { git = "https://git.example.com/corp/windows.git", rev = "v3.0.0" }
```

| Value | |
|---|---|
| `{ index = "<name>" }` | Take the collection from the [`[[index]]`](#index) with that name, and only from there |
| `{ path = "<dir>" }` | Take the collection from a directory holding its `galaxy.yml`, built and copied into the collections directory, or from a directory holding a built collection's `MANIFEST.json`, copied as it is. A relative path is relative to the project root |
| `{ path = "<dir>", editable = true }` | The same, but linked to the directory rather than copied, so edits there take effect without installing again. `editable = false` is the same as leaving it out |
| `{ path = "<file>" }` | Take the collection from a built collection's tarball on disk, installed as an index's artifact is. A path to a file is a tarball, and a path to a directory a directory. It can't be editable |
| `{ url = "<url>" }` | Take the collection from a built collection's tarball at an `http` or `https` URL, downloaded when it's locked. For a file, use `path` |
| `{ git = "<url>" }` | Take the collection from a git repository, at the commit `rev` is at when it's locked, installed as a directory is. The URL is `https://`, `ssh://`, such as `ssh://git@github.com/org/repo.git`, or `file://`. See [Git repository source](collection-sources.md#a-git-repository) for more details |

A `path` source that's a directory, editable or not, and a `git` source,
can also have `version = "<version>"` set. This is a semantic version used
in place of the one in `galaxy.yml` of that source and is used for a
collection whose `galaxy.yml` has a placeholder its release fills in. See
[Overriding galaxy.yml versions](collection-sources.md#overriding-galaxyyml-versions).

An entry applies wherever the collection appears, whether the project lists
it in `collections` or another collection depends on it. Without an entry, a
collection comes from the first index that has it (see
[Configuration](configuration.md#indexes)). Naming the index yourself is
also how to be sure a collection can't come from somewhere else that
happens to have one by the same name.

## Requirements

A requirement is a collection name followed by an optional version
requirement, the same syntax as ansible-galaxy's `requirements.yml` and
`galaxy.yml`.

```text
NAMESPACE.NAME[VERSION REQUIREMENT]
```

| Requirement | Means |
|---|---|
| `ansible.windows` | Any version |
| `community.general>=9,<11` | 9.0.0 or later, but before 11.0.0 |
| `community.general >= 9` | The same as `community.general>=9`, with spaces around each part |

### Version requirements

A version requirement is `*`, for any version, or one or more clauses
separated by commas, all of which must be met. A clause is an operator and a
version:

| Operator | Means |
|---|---|
| `==` | Exactly this version |
| `!=` | Any version but this one |
| `>=`, `>` | This version or later, or only later |
| `<=`, `<` | This version or earlier, or only earlier |

Collection versions are [semantic versions](https://semver.org/). A version
in a clause can leave out its minor and patch parts, which count as `0`:
`>=9` is `>=9.0.0`, and `==9` is exactly `9.0.0`. Pre-releases are supported,
for example `>=2.0.0-rc.1` means `2.0.0-rc.1` as the minimum and any future
RCs or a final release are also valid.

The following forms are not supported, use the suggested alternatives instead:

| Written | Write instead |
|---|---|
| `1.2.3` or `=1.2.3` | `==1.2.3` |
| `^1.2.0`, `~1.2.0` | `>=1.2.0,<2.0.0`, `>=1.2.0,<1.3.0` |
| `==1.0.0+build1` | `==1.0.0` |
