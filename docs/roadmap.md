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
- **Collection projects.** Using alai in a collection's own repository,
  with its `galaxy.yml` as the list of what it needs, installing its
  dependencies into a tree alai manages with the checkout linked in, so
  `ansible-test` and the other Ansible tools find everything.
- **`alai run`.** Running `ansible-playbook` and the other Ansible commands
  with the project's collections, checking first that the installed
  ansible-core is one every locked collection supports.
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
- **JSON output** for commands that report data, such as `config show`,
  for scripts.
- **Checking installed collections** against the checksums each one
  carries for its files, and **cleaning the cache**.

## Not planned yet

- **Roles.** Only collections are installed; `alai.toml` leaves room to add
  roles later.
- **Verifying signatures**, until the index format carries them.
