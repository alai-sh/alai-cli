# Roadmap

!!! warning "Plans, not features"

    Nothing on this page works yet. The rest of the documentation describes
    what alai does today; this page is what's planned, in no particular
    order, and any of it may change. Feedback on any of it is welcome in the
    [issue tracker](https://github.com/alai-sh/alai-cli/issues).

## Planned

- **Collections from other sources.** Installing a collection from a
  directory on disk, linked in so edits take effect without reinstalling,
  from a git repository, or from a tarball URL or file, named in
  [`[project.sources]`](alai-toml.md#projectsources) and pinned in
  `alai.lock` like any other.
- **Building collections**: `alai build`, with the collection's
  metadata in `alai.toml` instead of `galaxy.yml`, so `alai add` and
  `alai remove` can edit a collection project's dependencies too.
- **A sibling checkout of a dependency** linked into a collection
  repository's tree as an editable source.
- **Faster installs of large collections**, by moving extraction and
  linking into native code.
- **Several ansible-core versions per project**: resolving for one
  ansible-core version, and named environments, each with its own
  ansible-core version, groups and collections directory, for testing a
  project against several releases.
- **Private and authenticated indexes**, with credentials for an index
  that needs them.
- **`requirements.yml`**: importing one into `alai.toml` with
  `alai add -r`, and installing from one with
  `alai collection install -r`. Waiting on how people use the file's
  `source:` and `signatures:` entries; if you depend on them, say so in the
  issue tracker.
- **A pre-release setting**, to always allow or never allow pre-releases,
  for the whole project or one collection.
- **Excluding newer versions**: `--exclude-newer DATE`, and an
  `exclude_newer` setting, to resolve only from versions published before a
  date or a duration ago, for reproducing an old lock or holding back
  releases until they've been out a while.
- **JSON output** for the other commands that report data, such as
  `config show`, for scripts, as `alai find` has.
- **Plugin documentation**: a command to show a plugin's full
  documentation, options and examples, as `ansible-doc` does, without
  installing its collection first.
- **More from installed collections in `alai find`**: listing the plugins
  of installed collections that aren't on an index, such as source
  checkouts, and reading routing from the version installed.
- **Checking installed collections** against the checksums each one
  carries for its files, and **cleaning the cache**.

## Not planned yet

- **Roles.** Only collections are installed; `alai.toml` leaves room to add
  roles later.
- **Verifying signatures**, until the index format carries them.
