# Collection sources

A collection's source defines where and how alai gets the collection from.
Most come from an [index](indexes.md), found by name, and need nothing extra to be
set. A collection can also be retrieved from a specific index, a directory on
disk, a built collection's tarball, or a git repository, by naming it in
[`[project.sources]`](alai-toml.md#projectsources):

```toml
[project]
collections = [
    "cisco.ios",
    "corp.dev",
    "corp.lib",
    "corp.net",
    "corp.web",
    "corp.win",
]

[project.sources]
"ansible.netcommon" = { index = "internal" }
"corp.dev" = { path = "../corp.dev" }
"corp.lib" = { path = "../corp.lib", editable = true }
"corp.net" = { path = "dist/corp-net-2.1.0.tar.gz" }
"corp.web" = { url = "https://artifacts.example.com/corp-web-1.4.0.tar.gz" }
"corp.win" = { git = "https://git.example.com/corp/windows.git", rev = "v3.0.0" }
```

| Source | Written as | Installed by | Use it for |
|---|---|---|---|
| [An index](#an-index) | Nothing, or `{ index = "<name>" }` | Downloading and installing | Published collections |
| [A directory](#a-directory) | `{ path = "<dir>" }` | Copying it, as if built and installed | A collection that isn't published, or a change to one you want to try |
| [An editable directory](#an-editable-directory) | `{ path = "<dir>", editable = true }` | Linking to it | A collection you're working on alongside the project |
| [A tarball](#a-tarball) | `{ path = "<file>" }` or `{ url = "<url>" }` | Extracting it | A built collection that isn't on an index, such as a CI build or a release download |
| [A git repository](#a-git-repository) | `{ git = "<url>", rev = "<rev>" }` | Fetching the commit and installing like a directory | A collection that's only in a repository, or a fix that isn't released yet |

The defined source is also stored in the `alai.toml` and `alai.lock` files so
that everyone installing the project gets the same thing.

## An index

This is the default and is used when a collection has no `[project.sources]`
entry. The index used is the first configured index that contains the
specified collection (see
[Configuration](configuration.md#indexes)). `alai.lock` records the index's
URL, the version chosen and its checksum, and `alai sync` downloads exactly
that version and checks it against the checksum:

[Installing a collection, and what it needs, from the index](casts/source-index.cast){ .alai-cast }

`{ index = "<name>" }` takes a collection from the
[`[[index]]`](alai-toml.md#index) with that name, and only from there. The
sources can also be used to list a source for a dependency, for example
`ansible.netcommon` is a dependency of `cisco.ios` and will be pulled from the
`internal` index regardless of where `cisco.ios` was from.

```toml
[[index]]
name = "internal"
url = "https://ansible-index.example.com/"

[project]
collections = ["cisco.ios"]

[project.sources]
# Dependency of cisco.ios, will pull it from the internal index
"ansible.netcommon" = { index = "internal" }
```

To use the default index, either leave out `index`, so the indexes are
searched in order, or set `{ index = "alai" }` to use only the default index
and none of the custom ones defined.

Name the index only when:

- **A collection is on more than one index**, and it must come from one,
  such as an internal mirror.
- **Its name could be taken elsewhere.** A private collection pinned to the
  private index can't be swapped for a public one of the same name that
  happens to appear on an index searched first.

## A directory

A directory source is the path to a directory containing the collection to
install. The directory is copied to the collections directory, which ensures
any changes made to either the source or the copy don't affect each other.

To add a directory as a source use:

```console
$ alai add <dir>
```

This will add the following to the `alai.toml`:

```toml
[project]
collections = [
    "namespace.name",
]

[project.sources]
"namespace.name" = { path = "<dir>" }
```

[Adding a collection from a directory](casts/source-directory.cast){ .alai-cast }

How it is installed depends on what the directory holds:

- **A collection's source** (with a `galaxy.yml`): the collection is built,
  and the result copied into the collections directory, so what
  `galaxy.yml` leaves out of a build, such as `notes.txt` above, isn't
  copied.
- **A built collection** (with a `MANIFEST.json`): the directory is already
  considered built and is copied as is.

A directory with both is an error and cannot be installed normally.

The collection's version and dependencies are read from the `galaxy.yml`
or `MANIFEST.json` and the `requires_ansible` from `meta/runtime.yml`. Any
subsequent dependencies will go through the normal resolution process and are
treated as normal collections from an index unless a `[project.sources]` entry
is present.

Use one for:

- **A collection that isn't published**, such as one kept in the same
  repository as the playbooks that use it (see
  [Collections in your own repository](#collections-in-your-own-repository)).
- **Trying a change** to a collection, including one the project only gets
  as a dependency.
- **A built collection** you have unpacked, or one installed elsewhere.

Where it falls short:

- **Edits aren't picked up straight away.** The collection is copied again
  when its version changes, when the contents of `galaxy.yml`,
  `MANIFEST.json` or `meta/runtime.yml` change, or with
  `alai sync --reinstall-collection NAME`, but not when another file
  changes. To see every edit, make it editable.
- **The directory must be there for everyone.** `alai.lock` records its
  path, so a teammate or CI job needs the same directory at the same place
  relative to the project, such as a git submodule, or a sibling checkout
  your CI clones first.
- **`galaxy.yml` must be valid.** Installing a directory must build the
  collection, which means the `galaxy.yml` must be valid, including fields that
  may not be used at runtime like `readme` and `authors`.

!!! warning "How a directory is built may change"

    The alai build process is still a work in progress (see the [roadmap](roadmap.md)).
    If you depend on a collection directory having a specific file structure that the
    build process might skip, use an editable directory instead.

## An editable directory

An editable directory is like a normal directory source, but the source is not
built and copied. Instead, it will use a symlink to link the editable target
into alai's collection directory to ensure any changes made to the edit target
are seen by Ansible.

To add a directory as an editable source use:

```console
$ alai add --editable <dir>
```

This will add the following to the `alai.toml`:

```toml
[project]
collections = [
    "namespace.name",
]

[project.sources]
"namespace.name" = { path = "<dir>", editable = true }
```

[Adding a collection from a directory, editable](casts/source-editable.cast){ .alai-cast }

Use one for:

- **A collection you're developing** alongside a project that uses it, or
  alongside another collection that depends on it.
- **Debugging.** `alai run` tells a debugger to resolve the link, so
  breakpoints set in the directory hit (see
  [What breakpoints hit](editors.md#what-breakpoints-hit)).

Where it falls short:

- **Nothing is built.** Ansible sees the directory as it is. Anything a build
  would leave out is still there, even though a real release won't have it.
- **The directory must be there for everyone**, as for a copied one.
- **It's a link**, so anything that copies the collections directory
  somewhere else, such as into a container image, copies the link, not the
  collection. Use a copied directory for that.

`alai add` of the same directory with or without `--editable` switches
between the two.

## Collections in your own repository

A playbook project can keep collections it doesn't publish in its own git
repository. Commit each collection's source, with its `galaxy.yml`, in a
directory of the project, and point a source at it:

```toml
[project]
collections = ["corp.tools", "community.general>=10"]

[project.sources]
"corp.tools" = { path = "collections_src/corp/tools", editable = true }
```

`alai add --editable collections_src/corp/tools` writes both for you. The
path is relative to the project root and is recorded in `alai.lock`, so
anyone who clones the repository gets the same collection with `alai sync`.

An [editable directory](#an-editable-directory) is usually the best fit as
edits take effect straight away and nothing is built. If the collections
directory is copied somewhere else, such as into a container image, leave
out `editable` to use a [copied directory](#a-directory) instead.

!!! warning "Keep them out of the collections directory"

    Don't commit a collection inside the
    [collections directory](projects.md#where-collections-are-installed),
    such as `collections/ansible_collections/corp/tools`. `alai sync` makes
    that directory hold only what `alai.lock` lists, so it removes a
    collection that has no source. A source pointing inside it is an error.
    Keep your collections in another directory and let alai link or copy them
    in as a project source.

## A tarball

A tarball is either a file path or HTTP URL to a prebuilt collection `.tar.gz`.
alai will extract the tarball and place it in the collections directory like
an index sourced collection.

To add a tarball as a source use:

```console
$ alai add dist/corp-local-2.1.0.tar.gz
$ alai add "https://artifacts.example.com/corp-web-1.4.0.tar.gz"
```

This will add the following to the `alai.toml`:

```toml
[project]
collections = [
    "corp.local",
    "corp.web",
]

[project.sources]
"corp.local" = { path = "dist/corp-local-2.1.0.tar.gz" }
"corp.web" = { url = "https://artifacts.example.com/corp-web-1.4.0.tar.gz" }
```

The tarball must be gzipped, as `ansible-galaxy collection build` writes it.
Anything else is refused before it's read or downloaded whole.

[Adding a collection from a tarball](casts/source-tarball.cast){ .alai-cast }

The tarball is installed exactly as one from an index is. Only the files
declared in the `FILES.json` are created and each file is checked against its
checksum. Its version and dependencies are what its `MANIFEST.json` says,
its `requires_ansible` what its `meta/runtime.yml` says, and its dependencies
come from indexes, unless they have a source of their own.

`alai.lock` records the tarball's checksum and size, as it does an index's
artifact.

Use one for:

- **A collection published somewhere other than an index**, such as a
  release on a git host, or an internal artifact store.
- **Testing a build** before publishing it, as users of the published
  collection will get it.

Where it falls short:

- **A file must be there for everyone**, as a directory must.
- **Credentials aren't sent**, so a URL has to be readable without them.
- **It can't be editable**: a tarball is a release. Use a directory to work
  on the collection.

## A git repository

A git source is a collection stored in any local or remote git repository.
It can specify the commit/tag/branch to use as well as a few extra options like
`subdirectory` or `version`.

To add a git collection as a source use:

```console
$ alai add 'git+https://github.com/corp/monorepo@main#subdirectory=collections/corp.net'
$ alai add 'git+ssh://git@github.com/ansible-collections/community.general@13.5.0'
```

This will add the following to the `alai.toml`:

```toml
[project]
collections = [
    "community.general",
    "corp.net",
]

[project.sources]
"corp.net" = { git = "https://github.com/corp/monorepo", subdirectory = "collections/corp.net", rev = "main" }
"community.general" = { git = "ssh://git@github.com/ansible-collections/community.general", rev = "13.5.0" }
```

[Adding a collection from a git repository](casts/source-git.cast){ .alai-cast }

The following values can also be set with the git source type:

|Key|Purpose|
|---|---|
|rev|The branch, tag, or full commit hash to check out. If unset, the default branch is used|
|subdirectory|The collection's directory (where its `galaxy.yml` is) to use in the repository if not in the root.|
|version|Override the `version` of the `galaxy.yml` if it isn't a valid semver value. See [Overriding galaxy.yml versions](#overriding-galaxyyml-versions).|

The repository is given as a URL:

| Over | In `alai.toml` | On the command line |
|---|---|---|
| HTTPS | `https://github.com/corp/windows.git` | `git+https://github.com/corp/windows.git@v3.0.0` |
| SSH | `ssh://git@github.com/corp/windows.git` | `git+ssh://git@github.com/corp/windows.git@v3.0.0` |
| SSH, another port | `ssh://git@git.example.com:2222/corp/windows.git` | `git+ssh://git@git.example.com:2222/corp/windows.git@v3.0.0` |
| A repository on disk | `file:///srv/git/windows.git` | `git+file:///srv/git/windows.git@v3.0.0` |

git's shorter SSH form, `git@github.com:corp/windows.git`, which GitHub
gives you to clone over SSH, is not supported. Use the `ssh://` URL format
instead.

alai fetches the repository using the `git` you have installed and will fetch
the `rev` specified into the cache. From there the collection is installed
like [a directory source](#a-directory) is. Submodules are fetched, but Git LFS
artifacts are not.

`alai.lock` records the commit `rev` was at when it was locked, and keeps
it. This means a branch that has moved on, or a tag that was moved, doesn't
change what `alai sync` installs. `alai lock -P corp.net`
(`--upgrade-collection`), or `-U` for everything, looks `rev` up again. A
new commit is installed in place of the old one even when the version in
`galaxy.yml` is the same. Changing the entry in `alai.toml` locks it again.

Use one for:

- **A collection that's only published as a repository**, such as many on
  GitHub.
- **A fix that isn't released yet**: point the collection at the branch or
  commit with it, then back at an index once it's out.
- **A repository holding several collections**, each taken from its
  directory with `subdirectory`.

Where it falls short:

- **It needs `git`**, and access to the repository for everyone who locks
  the project or installs it with nothing cached.
- **Edits aren't seen**: a commit is fixed. Use an editable directory, a
  checkout of your own, to work on the collection.
- **A commit's abbreviated hash can't be used**, as finding it needs the
  whole history: give the full hash.

!!! warning "ssh may ask questions"

    alai doesn't stop ssh from asking for a key's passphrase, or whether to
    trust a host it hasn't seen before, as `git clone` would. When several
    repositories are fetched at once, their questions can arrive together.
    Without a terminal, as in CI, ssh can't ask, and the fetch fails. Use the
    various mechanisms `git` has for silencing prompts like
    `GIT_SSH_COMMAND="ssh -o BatchMode=yes"` in these environments.

## Overriding galaxy.yml versions

Some collections' `galaxy.yml` have a `version` that is not a valid semver
value, for example a placeholder for a release process to fill in. alai
refuses to build a collection with such a value, as it can't know what
version it is for. The `version` source value can be used to explicitly set a
version for such scenarios and is supported for sources that build the
collection from source, like a directory or git source.

For example, the following will clone and build the git URL at the tagged
commit `1.5.0`. The `galaxy.yml` at that tag has
`version: '{{ REPLACE_VERSION }}'`, so the `version` key will override it.

```console
$ alai add 'git+https://github.com/chocolatey-community/chocolatey-ansible@1.5.0#subdirectory=chocolatey&version=1.5.0'
```

```toml
[project.sources]
"chocolatey.chocolatey" = { git = "https://github.com/chocolatey-community/chocolatey-ansible", subdirectory = "chocolatey", rev = "1.5.0", version = "1.5.0" }
```

It's used in place of `galaxy.yml`'s, in `alai.lock` and in the installed
collection's `MANIFEST.json`, so requirements other collections have on it
mean what they say.
