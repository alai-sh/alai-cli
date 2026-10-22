# Collection sources

A collection's source is where alai gets it from. Most come from an
[index](indexes.md), found by name, and need nothing set. A collection can
also be taken from a particular index, a directory on disk, a built
collection's tarball, or a git repository, by naming it in
[`[project.sources]`](alai-toml.md#projectsources):

```toml
[project]
collections = ["cisco.ios", "corp.dev", "corp.lib", "corp.net", "corp.web", "corp.win"]

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
| [An index](#an-index) | Nothing, or `{ index = "<name>" }` | Downloading the version locked | Published collections: the default |
| [A directory](#a-directory) | `{ path = "<dir>" }` | Copying it, as if built and installed | A collection that isn't published, or a change to one you want to try |
| [An editable directory](#an-editable-directory) | `{ path = "<dir>", editable = true }` | Linking to it | A collection you're working on alongside the project |
| [A tarball](#a-tarball) | `{ path = "<file>" }` or `{ url = "<url>" }` | Extracting it, as an index's artifact is | A built collection that isn't on an index, such as a CI build or a release download |
| [A git repository](#a-git-repository) | `{ git = "<url>", rev = "<rev>" }` | Fetching the commit and copying it, as a directory is | A collection that's only in a repository, or a fix that isn't released yet |

An entry applies wherever the collection appears: in the project's
collections, a group's, or as what another collection depends on. It's
written in `alai.lock` too, so everyone installing the project gets the same
thing.

## An index

This is the default: a collection with no `[project.sources]` entry comes
from the first configured index that has it, the default index last (see
[Configuration](configuration.md#indexes)), and so does everything it
depends on. `alai.lock` records the index's URL, the version chosen and its
checksum, and `alai sync` downloads exactly that, checked against the
checksum:

[Installing a collection, and what it needs, from the index](casts/source-index.cast){ .alai-cast }

`{ index = "<name>" }` takes a collection from the
[`[[index]]`](alai-toml.md#index) with that name, and only from there,
whether the project lists the collection or, as `ansible.netcommon` here,
only needs it for another one:

```toml
[[index]]
name = "internal"
url = "https://ansible-index.example.com/"

[project]
collections = ["cisco.ios"]

[project.sources]
"ansible.netcommon" = { index = "internal" }
```

To use the default index, leave the entry out. To pin a collection to it,
skipping any `[[index]]` searched first, use `{ index = "alai" }`: `alai`
is always the default index's name, unless you
[replace the default](configuration.md#indexes) with an index of another
name.

Name the index only when:

- **A collection is on more than one index**, and it must come from one,
  such as an internal mirror.
- **Its name could be taken elsewhere.** A private collection pinned to the
  private index can't be swapped for a public one of the same name that
  happens to appear on an index searched first.

An index can't give you a version that hasn't been published to it. For a
change you're making, or a collection that's never published, use a
directory.

## A directory

`{ path = "<dir>" }` takes the collection from a directory on disk.
`alai add` writes it for you when given the directory, with a `/` in it so
it isn't read as a collection name. A relative path is relative to the
project root:

[Adding a collection from a directory](casts/source-directory.cast){ .alai-cast }

How it's installed depends on what the directory holds:

- **A collection's source**, with a `galaxy.yml`: the collection is built,
  and the result copied into the collections directory, so what
  `galaxy.yml` leaves out of a build, such as `notes.txt` above, isn't
  copied.
- **A built collection**, with a `MANIFEST.json` instead, such as one
  unpacked from a tarball or installed elsewhere: it's copied as it is.

A directory with both is an error, as it is to `ansible-galaxy`: remove
whichever is left over.

The collection's version and dependencies are what its `galaxy.yml`, or
`MANIFEST.json`, says, and its `requires_ansible` what its
`meta/runtime.yml` says, checked against the project's as an index
version's is. Its dependencies come from indexes,
unless they have a source of their own. `alai.lock` records all of that, so
when it changes, `alai sync` locks again, and `alai lock --check` lists
what changed.

Use one for:

- **A collection that isn't published**, such as one kept in the same
  repository as the playbooks that use it.
- **Trying a change** to a collection, including one the project only gets
  as a dependency: point its source at your checkout.
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
- **`galaxy.yml` needs what `ansible-galaxy` needs to build it**, including
  `readme` and `authors`.

!!! warning "How a directory is built may change"

    Building is still being designed (see the [roadmap](roadmap.md)), so
    exactly what ends up in the copy may change. Use an editable directory
    if you depend on the exact files.

## An editable directory

`{ path = "<dir>", editable = true }`, written by
`alai add --editable <dir>`, installs the collection as a link to the
directory instead of a copy, so an edit there takes effect straight away,
with nothing to install again:

[Adding a collection from a directory, editable](casts/source-editable.cast){ .alai-cast }

Use one for:

- **A collection you're developing** alongside a project that uses it, or
  alongside another collection that depends on it.
- **Debugging.** `alai run` tells a debugger to resolve the link, so
  breakpoints set in the directory hit (see
  [What breakpoints hit](editors.md#what-breakpoints-hit)).

Where it falls short:

- **Nothing is built.** Ansible sees the directory as it is: what a build
  leaves out, such as `.git`, `tests/output` and whatever `build_ignore`
  names, is there, and there's no `MANIFEST.json`. Check a release with a copied directory, or
  the published collection, before relying on what's in it.
- **The directory must be there for everyone**, as for a copied one.
- **It's a link**, so anything that copies the collections directory
  somewhere else, such as into a container image, copies the link, not the
  collection. Use a copied directory for that.

`alai add` of the same directory with or without `--editable` switches
between the two.

## A tarball

`{ path = "<file>" }`, a path to a file rather than a directory, takes the
collection from a built collection's tarball, such as one
`ansible-galaxy collection build` writes. `{ url = "<url>" }` downloads one
from an `http` or `https` URL. `alai add` writes either for you, given the
file, such as `alai add dist/corp-net-2.1.0.tar.gz` (a name ending in
`.tar.gz` is a file, with or without a `/`), or the URL. A `file://` URL is
written as the file's path. The tarball must be gzipped, as
`ansible-galaxy collection build` writes it; anything else is refused
before it's read or downloaded whole.

[Adding a collection from a tarball](casts/source-tarball.cast){ .alai-cast }

```toml
[project.sources]
"corp.net" = { path = "dist/corp-net-2.1.0.tar.gz" }
"corp.web" = { url = "https://artifacts.example.com/corp-web-1.4.0.tar.gz" }
```

The tarball is installed exactly as one from an index is: only what its
`FILES.json` lists, each file checked against its checksum. Its version and
dependencies are what its `MANIFEST.json` says, its `requires_ansible` what
its `meta/runtime.yml` says, and its dependencies come from indexes, unless
they have a source of their own.

`alai.lock` records the tarball's checksum and size, as it does an index's
artifact, so what's installed is what was locked:

- **A URL is downloaded when it's locked**, and not again: locking again
  keeps what `alai.lock` says about it, so if the file behind the URL
  changes, `alai sync` fails on the checksum when it next downloads it.
  `alai lock -P corp.web` (`--upgrade-collection`) downloads it again and
  locks what's there now. Give a new release a new URL.
- **A file is checked every time**, as it's cheap to: if it's no longer the
  tarball `alai.lock` has, `alai sync` and `alai lock` stop and say so.
  `alai lock -P corp.net` locks the file as it is now, as does replacing it
  with a release of another name, such as
  `alai add dist/corp-net-2.2.0.tar.gz`.

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

`{ git = "<url>" }` takes the collection from a git repository, at the
commit `rev` names: a branch, a tag, or a full commit hash, or the default
branch without one. `subdirectory` names the collection's directory when
it isn't the repository's root. `alai add` writes the entry for you, given
`git+<url>`, with `@<rev>` and `#subdirectory=<dir>` as uv takes them, and
[`version`](#a-version-that-isnt-one) after the `#` too, joined with `&`:

[Adding a collection from a git repository](casts/source-git.cast){ .alai-cast }

A collection in a directory of the repository, as in one holding several:

```console
$ alai add 'git+https://github.com/corp/monorepo@main#subdirectory=collections/corp.net'
```

```toml
[project.sources]
"corp.net" = { git = "https://github.com/corp/monorepo", subdirectory = "collections/corp.net", rev = "main" }
```

The repository is given as a URL:

| Over | In `alai.toml` | On the command line |
|---|---|---|
| HTTPS | `https://github.com/corp/windows.git` | `git+https://github.com/corp/windows.git@v3.0.0` |
| SSH | `ssh://git@github.com/corp/windows.git` | `git+ssh://git@github.com/corp/windows.git@v3.0.0` |
| SSH, another port | `ssh://git@git.example.com:2222/corp/windows.git` | `git+ssh://git@git.example.com:2222/corp/windows.git@v3.0.0` |
| A repository on disk | `file:///srv/git/windows.git` | `git+file:///srv/git/windows.git@v3.0.0` |

git's shorter SSH form, `git@github.com:corp/windows.git`, which GitHub
gives you to clone over SSH, isn't a URL, so it's refused, with the
`ssh://` URL to use instead, as uv refuses it. Its path is relative to the
user's home directory on the server, which GitHub, GitLab and the like
ignore: `git@github.com:corp/windows.git` is
`ssh://git@github.com/corp/windows.git`. For a repository under the home
directory on a server of your own, write `ssh://git@host/~/windows.git`.

alai runs the `git` you have installed, so whatever works for `git clone`
works here: an SSH URL with your SSH keys and config, an HTTPS URL with a
credential helper. It never asks for an HTTPS username or password itself.
The commit is fetched into the cache, then the collection is installed from
it as [a directory](#a-directory) is: built from its `galaxy.yml`, or copied
as it is if the repository holds a built collection. Submodules are fetched
too; files stored with Git LFS aren't.

`alai.lock` records the commit `rev` was at when it was locked, and keeps
it: a branch that has moved on, or a tag that was moved, doesn't change what
`alai sync` installs. `alai lock -P corp.win` (`--upgrade-collection`), or
`-U` for everything, looks `rev` up again. A new commit is installed in
place of the old one even when the version in `galaxy.yml` is the same.
Changing the entry in `alai.toml` locks it again.

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
    trust a host it hasn't seen before, as `git clone` would. While git
    reaches a repository, alai stops drawing progress, so the question
    isn't drawn over, and says what it's waiting on with an
    `Updating <collection> from <url>` line, then `Updated` once the commit
    is fetched: when an agent or password manager asks to use your key, the
    line above says which collection it's for. When several repositories
    are fetched at once, their questions can arrive together. Without a terminal, as in CI, ssh can't ask, and the
    fetch fails with `Host key verification failed` or
    `Permission denied (publickey)`; alai's error says which to fix.

To keep ssh from needing to ask:

- **Trust a new host first**: run `ssh -T git@<host>` once and answer, or
  add its key with `ssh-keyscan -t ed25519 <host> >> ~/.ssh/known_hosts`.
  Either way, check the fingerprint, which `ssh-keygen -lf ~/.ssh/known_hosts`
  shows, against the one the host publishes, as `ssh-keyscan` checks
  nothing. GitHub's ED25519 key is
  `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`.
- **Load your key into an agent**: `ssh-add`, or `AddKeysToAgent yes` in
  `~/.ssh/config`, so the first use adds it.
- **Accept a new host's key, but not a changed one**: set
  `StrictHostKeyChecking accept-new` in a `Host` block of `~/.ssh/config`,
  or for one command,
  `GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" alai sync`.
  It trusts whatever answers first.
- **Fail rather than ask**, as a CI job should:
  `GIT_SSH_COMMAND="ssh -o BatchMode=yes"`, with `known_hosts` provided.
- **Turn the check off**, in a throwaway environment only:
  `GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"`.
  Anyone between you and the host can then answer in its place.

alai uses one ssh connection per host for everything it fetches in a run,
through ssh's connection sharing (`ControlMaster`), so your key is used,
and an agent that confirms each use asks you, once per host rather than
for every repository. It's left to you when you set your own ssh command,
in `GIT_SSH_COMMAND`, `GIT_SSH` or git's `core.sshCommand`, or your
`~/.ssh/config` already shares connections to the host.

Other settings git reads that help here:

- **An HTTPS URL instead of an SSH one**, where only HTTPS and a credential
  helper are set up:
  `git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"`.
  `alai.lock` keeps the URL as it's written in `alai.toml`.
- **A setting for one command, without a config file**:
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.sshCommand GIT_CONFIG_VALUE_0="ssh -i ~/.ssh/deploy_key" alai sync`.
- **A private certificate authority**: `GIT_SSL_CAINFO=/path/to/ca.pem`, or
  `http.sslCAInfo`. git checks HTTPS certificates itself, so alai's
  [`system_certs`](configuration.md) doesn't change what it trusts.

## A version that isn't one

Some collections' `galaxy.yml` doesn't have a usable version in the
repository: a placeholder their release fills in, such as
`version: '{{ REPLACE_VERSION }}'`, or none at all. alai refuses one of
those, as it can't know what version it is. Give the version in the
source:

```toml
[project.sources]
"chocolatey.chocolatey" = { git = "https://github.com/chocolatey-community/chocolatey-ansible", subdirectory = "chocolatey", rev = "1.5.0", version = "1.5.0" }
```

It's used in place of `galaxy.yml`'s, in `alai.lock` and in the installed
collection's `MANIFEST.json`, so requirements other collections have on it
mean what they say. `version` can be given for a git source and for a
directory, editable or not, but not for a tarball or a built collection,
whose version was checked when it was built. `alai add` writes it for a
git repository given with it after the `#`, joined to `subdirectory` with
`&`, so quote it for the shell:

```console
$ alai add 'git+https://github.com/chocolatey-community/chocolatey-ansible@1.5.0#subdirectory=chocolatey&version=1.5.0'
```

For a directory, add it to the entry by hand. If `galaxy.yml` does have a
version, the source's is still used, with a warning, as it's probably no
longer needed.

## Installing by hand

[`alai collection install`](installing-by-hand.md) takes a directory too,
copied again every time, or `-e DIR` to link it, a tarball, by path or URL,
and a git repository, as `git+<url>`, without changing `alai.toml` or
`alai.lock`.

## What a directory can't be

A directory inside the collections directory, or one that holds it, is an
error: installing would replace it, and an exact `alai sync` could remove
it. A [collection project](projects.md#playbook-projects-and-collection-projects)'s
own collection is always its checkout, so it can't be given a source.
