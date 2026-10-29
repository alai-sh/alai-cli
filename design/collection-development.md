# Collection development

**Status: implemented** (2026-10-21), as the user docs describe
([docs/projects.md](../docs/projects.md#a-collections-repository),
[docs/running-ansible.md](../docs/running-ansible.md#in-a-collections-repository)):
`galaxy.yml` beside `alai.toml`, its dependencies and `meta/runtime.yml` as the
manifest (`_galaxy.py`, `load_project` in `_project.py`), the collection as
an editable root in the lock, the tree in the cache with the overlap check
(`_config.py`), the link (`link_editable` in `_install.py`), and
ansible-test's variables in `alai run` and `alai env`. What isn't done is
under [Not done yet](#not-done-yet). The findings were checked against
ansible-core 2.21.5 (PyPI) and the ansible-core source at `616e7c05e0`, on
2026-10-19, and again at devel `8cb65a623a` on 2026-10-21.

**Reading the metadata is alai-core's** (`alai_core.collection`, since
2026-10-28): `galaxy.yml`, `MANIFEST.json`'s `collection_info` and
`meta/runtime.yml`, and the collection name check, which
`_manifest.collection_name` calls. Core's readers only fail when the file
can't be read at all (decided with the user, 2026-10-29). Each value is
kept as written, and checked by a `get_*` method when a caller asks for it,
by the rules ansible-galaxy installs with. So each use picks what it needs:
installing, from a tarball or a directory, needs the name, version and
dependencies, and `requires_ansible` too, as building would fail on it.
galaxy-sync needs only the name and version, and `alai find` nothing. alai
calls each getter and lists every problem at once, with its hints. With a
version from `[project.sources]`, alai doesn't check the file's, which may
be a placeholder, and only reads it to warn when it's valid.

Tried against ansible-galaxy 2.21.5 on the same day, building and
installing from a source tree and from the tarball. `collection build`
only enforces its schema's mandatory keys (`namespace`, `name`,
`version`, `readme`, `authors`) and warns about unknown ones. It builds a
`1.0` version, quoted or not, a `null` one as `*`, and any
`dependencies` as written, a list included. Installing refuses a version
that isn't SemVer, a dependency name that isn't a valid collection name,
and a requirement it can't read, such as `^1`. A requirement that isn't a
string, or `dependencies` that isn't a mapping, crashes it. A
self-dependency it can satisfy, such as `'*'`, installs. Core refuses
every self-dependency, as it's never what the author meant. Installing
from a source tree needs the mandatory keys too, which `_build.py`'s
`_REQUIRED` checks, as it builds path sources. Core leaves out `readme`
and `authors`: a command that builds or publishes checks what it needs
itself. Core's rules passed 4,569 of the latest collections' 4,576
`MANIFEST.json` files. The 7 refused are `finallycoffee.*`, with `^`
requirements ansible-galaxy can't install. galaxy-sync isn't affected, as
it reads dependencies from Galaxy's API as they are.

`meta/runtime.yml` is read as ansible-core reads it, with PyYAML's
`BaseLoader` (`_collection_meta.py`): every scalar is a string, so an
unquoted `requires_ansible: 2.15` is the string `"2.15"`, refused as an
invalid specifier set rather than as not a string. A top level that isn't
a mapping, `null` and `~` included as they're strings, is read as empty,
though ansible-core can't load the collection then, as alai only needs
`requires_ansible` from it. ansible-core only warns about a
`requires_ansible` it can't read; alai refuses it, as it can't choose
versions by it, and the message says so. Core's reader matched `BaseLoader`
on all 3,871 `meta/runtime.yml` files in the latest collections.

Run end to end on 2026-10-21 with a collection checked out outside any
`ansible_collections` path, depending on `ansible.utils` from the index
through its module_utils, with `alai run ansible-test …` from the checkout:

| Command | ansible-core 2.21.5 | devel `8cb65a623a` with the [fixes](#upstream-fixes) |
|---|---|---|
| `sanity --local` (`import`, `validate-modules`, `pep8`) | Works | Works |
| `integration --local` | Works | Works |
| `units --local` | Fails | Works |
| `sanity`, `units`, `integration` with `--venv` | Fail | Work |
| `units --docker` | | Works |
| `ansible-doc ns.col.hello`, from a subdirectory | | Works |

## Collection projects

**Terms** (decided with the user, 2026-10-21), used the same way in the docs,
these notes and messages:

- **Collection project**: a project whose root defines a collection, today
  by a `galaxy.yml` beside its `alai.toml`, later perhaps a
  `[collection]` table in `alai.toml`. What it defines is **the project's own
  collection**.
- **Playbook project**: any other project, whatever it holds (playbooks,
  roles, inventories, or only an `alai.toml`). The name follows
  ansible-creator's `init playbook` and AWX's projects, which use it for any
  repository of content that uses collections. "Ansible", "content" and
  "user" project were considered: every project is Ansible, Red Hat calls
  collections themselves content, and collection authors are users too.
- **The checkout**: only the directory on disk, never the project.
- **Workspace**: kept for several collections in one project, as uv and
  Cargo use it.

**Decided (2026-10-21): one mechanism, as uv's root package.** uv installs a project with a build system into its own `.venv`,
editable, with no flag; `uv add -e .` is refused (`Requirement name `lib`
matches project name `lib`, but self-dependencies are not permitted`). alai
does the same: a collection project installs its own collection, editable, as part of every sync, and its
`galaxy.yml` `dependencies` are read as that source's own metadata, as uv
reads a path dependency's `pyproject.toml`. That's the mechanism editable
path sources will use too, so a sibling checkout of a dependency works the
same way. Groups, sources and overrides work as for any project; only where
the tree lives (below) differs.

| | Playbook project | Collection project |
|---|---|---|
| Marker | `alai.toml` | `alai.toml` with `galaxy.yml` beside it |
| Requirements | `[project] collections` in `alai.toml` | `dependencies` in `galaxy.yml`; `[project] collections` is an error ([Decisions](#decisions)) |
| Target ansible-core | `[project] requires_ansible` | `requires_ansible` in `meta/runtime.yml` |
| In the lock | No entry: `[project]` holds its inputs | The root entry, `source = { editable = "." }`, as uv locks its own package |
| Installs to | `<root>/collections` | A tree alai owns, with the checkout linked in: see [Where the tree lives](#where-the-tree-lives) and [Checkouts already in the layout](#checkouts-already-in-the-layout) |
| Run with | `ansible-playbook`, `alai run` | `ansible-test`, plus `ansible-playbook`, `ansible` and `ansible-doc` against the checkout, through `alai run` |

### A dependency on the project's own collection

Decided (2026-10-21): resolved to the checkout, as uv does. The resolver
has one candidate for the collection's name, the checkout at its
`galaxy.yml` version (`_PROJECT` in `_resolve.py`), and never looks for it
on an index. A requirement that version doesn't meet backtracks to other
versions of whatever asked for it, or fails with `ns.col 1.0.0, the
project's own collection, doesn't meet:` and the requirements.

Tried with uv 0.13.0 on 2026-10-21: `dep`, a path dependency of the project
`lib`, requiring `lib>=0.1` is locked with `lib` as its dependency, and `lib`
stays the `editable = "."` root. Requiring `lib>=9` fails: "dep==0.1.0
depends on your project ... your project's requirements are
unsatisfiable", with a hint that the name is shadowed by the project.

### What makes the root a package

Tried with uv 0.13.0 on 2026-10-21:

- **`[build-system]` present**: the root is locked as `source = { editable
  = "." }` and installed into `.venv`.
- **No `[build-system]`**: the root is locked as `source = { virtual = "."
  }` and not installed; only its dependencies are.
- **`[tool.uv] package = true`** without a `[build-system]` makes it a
  package anyway (`editable = "."`), and `package = false` the reverse.

Cargo draws the same line by table: a `Cargo.toml` with `[package]` is a
crate, one with only `[workspace]` is a virtual manifest.

For alai today, `galaxy.yml` beside `alai.toml` plays `[build-system]`'s
part: it's what ansible-galaxy builds from. If [`alai build`](#open-questions)
one day reads the collection from `alai.toml` instead, the signal would be
a table of its own, as Cargo's `[package]`: a `[collection]` table with
`namespace`, `name` and `version` (and later what `galaxy.yml` holds) makes
the root a collection, and a project without one isn't. A playbook project
has no name or version to give ([configuration.md](configuration.md)), so
the table is never ambiguous, and it needs no separate switch such as uv's
`package`. Both a `[collection]` table and a `galaxy.yml` would be an
error, or `galaxy.yml` generated from it; that's for the `alai build`
question.

`galaxy.yml` is the collection's equivalent of `pyproject.toml`'s
`[project]`, but alai settings shouldn't go in it. ansible-galaxy warns, rather than fails, on keys
it doesn't know (`_normalize_galaxy_yml_manifest` in
`lib/ansible/galaxy/collection/concrete_artifact_manager.py`: "Found unknown
keys in collection galaxy.yml"), so anything of alai's there would print that
warning on every build and install. A collection project's alai settings and
test-only groups go in an `alai.toml` beside `galaxy.yml`.

## What the tools require

Developing a collection uses two kinds of tool, with different needs: the
everyday ansible-core CLIs, and ansible-test.

### ansible, ansible-playbook and the other CLIs

`ansible`, `ansible-playbook`, `ansible-doc`, `ansible-inventory` and
`ansible-console` share the collection loader (`_configure_collection_loader`
in `lib/ansible/plugins/loader.py`, `_AnsibleCollectionFinder` in
`lib/ansible/utils/collection_loader/_collection_finder.py`). It is looser
than ansible-test:

- **Search path.** In order: `<playbook dir>/collections` for each playbook
  given (`ansible-playbook` only), `--collections-path` where a CLI has it,
  `COLLECTIONS_PATHS` (`ANSIBLE_COLLECTIONS_PATH`, `collections_path` in
  `ansible.cfg`, default `~/.ansible/collections:/usr/share/ansible/collections`)
  and then `sys.path` (`COLLECTIONS_SCAN_SYS_PATH`). An entry may name the
  `ansible_collections` directory or its parent. The first match wins.
- **No layout needed for the working directory.** Unlike ansible-test, the
  current directory doesn't matter. So `alai run` only has to set
  `ANSIBLE_COLLECTIONS_PATH` to the tree; it replaces the user's paths,
  with `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False`, as alai uses only the
  collections directory it manages ([README.md](README.md#principles)).
  `<playbook dir>/collections` is still searched by `ansible-playbook`
  whatever the path says; how `alai run` treats it is open.
- **Symlinks are fine.** Paths are compared with `os.path.abspath`, never
  resolved. A tree that symlinks to the checkout loads the collection, by
  FQCN, through `ANSIBLE_COLLECTIONS_PATH` or through
  `<playbook dir>/collections`.
- **Playbooks inside the collection.** `ansible-playbook` treats a playbook
  as part of a collection (the collection becomes the default for short
  names, and adjacent `collections/` isn't searched) only when its path, as
  given, sits under the path the loader imports that collection from
  (`_get_collection_name_from_path`). With a tree symlinking to the checkout:

  | Command | Result |
  |---|---|
  | `ansible-playbook ns.col.site` | Works |
  | `ansible-playbook <tree>/ansible_collections/ns/col/playbooks/site.yml` | Works |
  | `ansible-playbook <checkout>/playbooks/site.yml` | Runs, but short names such as `hello` don't resolve: the path isn't under the tree. FQCNs work |
  | `ansible localhost -m ns.col.hello`, `ansible-doc ns.col.hello` | Work |

  The [reversed layout](#the-reversed-layout), rejected, has none of these
  problems, since the checkout's own path is inside the tree.
- **`requires_ansible`** from `meta/runtime.yml` is checked when a collection
  loads. A mismatch is a warning by default
  (`COLLECTIONS_ON_ANSIBLE_VERSION_MISMATCH`: `warning`, `error` or `ignore`).

### ansible-test

- **The layout.** The content root must be a path ending
  `ansible_collections/<namespace>/<name>`. ansible-test checks the path
  string (`CollectionLayout.is_content_root`). It starts from the current
  directory, or from `ANSIBLE_TEST_CONTENT_ROOT` when set. That variable is
  internal (delegation sets it for the child ansible-test) and undocumented.
- **Dependencies.** ansible-test sets `ANSIBLE_COLLECTIONS_PATH` to the
  directory above `ansible_collections` (`ansible_util.py`), replacing the
  user's. Integration tests add to it but keep that root first. So
  dependencies have to be installed next to the collection, in the same
  `ansible_collections` tree.
- **Delegation.** `--docker` and `--remote` copy every collection in that
  tree into the container (`DataContext.create_collection_layouts`), so
  dependencies travel with the collection under test.
- **The working directory.** ansible-test calls `os.chdir()` into the content
  root. `os.getcwd()` always returns the physical path, whatever `PWD` says,
  so after that anything relative to the working directory sees the path
  with symlinks resolved.
- **Collection requirements.** ansible-test doesn't install collections. The
  `tests/integration/requirements.yml` and `tests/unit/requirements.yml`
  files in many repos are a convention CI scripts read.

## A collection checked out anywhere

The goal: `alai run ansible-test …`, `alai run ansible-playbook …` and the
other CLIs working from a checkout in any directory, with edits picked up
immediately. **Decided (2026-10-21): this is the one way alai supports
collection projects**, a tree alai builds and owns, outside the checkout
(see [Where the tree lives](#where-the-tree-lives)):

```
<tree>/ansible_collections/<ns>/<name>  -> symlink to the checkout
<tree>/ansible_collections/<dep_ns>/<dep_name>  (linked from the cache)
```

with `ANSIBLE_TEST_CONTENT_ROOT=<tree>/ansible_collections/<ns>/<name>`,
run from the checkout, and nothing else set for ansible-test, so it fits
`alai run`'s environment-variables-only rule ([run.md](run.md#decisions)).
The [reversed layout](#the-reversed-layout) would work with every
ansible-core release, but a second mechanism doubles what has to be tested
and leaves users to choose. Instead, the modes that need the
[upstream fixes](#upstream-fixes) need the ansible-core release that has
them, and alai says so.

Tested on 2026-10-21 against ansible-core devel `8cb65a623a`, Python 3.14
and podman 5.8.7 (`default-test-container:v2.23-2`), with a collection
whose module, filter and module_utils import a dependency's module_utils,
two unit tests and one integration target:

| Command | Unpatched | With the fixes |
|---|---|---|
| `sanity --local` (`import`, `validate-modules`) | Works | Works |
| `integration --local` | Works; the dependency loads from the tree | Works |
| `sanity`, `units`, `integration` with `--docker` | Work; the container gets copies | Work |
| `units --local` | Fails: [why](#why-local-unit-tests-fail) | Works |
| `sanity`, `units`, `integration` with `--venv` | Fail: [why](#why-venv-delegation-fails) | Work |

A checkout already at `…/ansible_collections/ns/col` passes everything
before and after the fixes. Running from inside the tree, through the
symlink, without the variable fails everything (`FATAL: The current
working directory must be within the source tree being tested.`), as
`os.getcwd()` resolves the link; that's not a case alai needs.

### Why local unit tests fail

ansible-test calls `os.chdir(content.root)` in `main_internal`, and then
passes test paths relative to it (`commands/units/__init__.py`), so pytest
resolves them against the physical working directory. ansible-test's
pytest plugin (`ansible_pytest_collections.py`,
`collection_resolve_package_path`) then rejects the file as outside
`ANSIBLE_COLLECTIONS_PATH`: `File ".../checkout/.../test_helper.py" not
found in collection path ".../tree/ansible_collections"`.

### Why venv delegation fails

The origin host's working directory comes from `os.getcwd()`
(`OriginProfile.get_working_directory` in `host_profiles.py`), and
delegation passes it to the child as `ANSIBLE_TEST_CONTENT_ROOT`
(`delegation.py`). After the chdir that's the physical path, so the child
finds no `ansible_collections` in it.

### Upstream fixes

Tracked, with the others found since, in [upstream.md](upstream.md).

The working directory is always physical, so changing it or setting `PWD`
before calling ansible-test changes nothing. The fixes belong in
ansible-test, and both are a line each:

1. units passes absolute test paths built from the content root;
2. the origin host's working directory is the content root rather than
   `os.getcwd()`;

plus documenting `ANSIBLE_TEST_CONTENT_ROOT` so tools can rely on it. With
both, every mode in the table passes, ansible-core's own unit tests pass as
before, and pep8, pylint and mypy are clean on the changed files. They
aren't likely to be backported, so the release that first has them is
the minimum for `units --local` and `--venv` through alai. A repro kit
(collections, a script running every mode in three layouts, results and
the patch) was built to propose them; it isn't kept in this repo.

Seen while testing, not caused by the tree: with `--docker`, a repo that
doesn't ignore `tests/output/` sends earlier results in the payload
(`git ls-files --others`), and `units --docker` then fails on a root-owned
junit file.

### The reversed layout

Rejected (2026-10-21) in favour of one mechanism. Keep the real checkout
inside the tree, and put a symlink wherever is convenient to work from.
The physical path then has the layout ansible-test needs, so every mode
works on every release, and nothing is loaded through a symlink (see
[Debugging](#debugging)). But the checkout has to live in alai's tree,
and alai would need both this and the tree above for checkouts it
doesn't own.

### Where the tree lives

Not inside the checkout (decided 2026-10-21). The plan is to keep it in the
cache, keyed by the checkout's path (`<cache_dir>/envs/<hash>/`), as Poetry
keeps virtual environments outside the project.

`./collections` in the checkout, as uv keeps `.venv` in the project, was
tried on 2026-10-21 against ansible-core devel `8cb65a623a`, with
`<checkout>/collections/ansible_collections/ns/col -> ../../..` and a
dependency installed beside it:

| | Result |
|---|---|
| `ansible-galaxy collection build` | Puts the dependency in the artifact, and the link `collections/ansible_collections/ns/col -> ../../..` with it. ansible-galaxy doesn't read `.gitignore`, so every collection would need `collections` in `build_ignore` |
| `ansible-test sanity`, `collections/` untracked in git | Tests the dependency as part of the collection: a pep8 error put in it failed the run |
| The same, with `collections/.gitignore` holding `*`, as uv writes `.venv/.gitignore` | Leaves the dependency out |
| The same in a checkout without git | Tests the dependency again, with nothing to exclude it |
| `ansible-test integration --local` | Works |

Untested, but likely: tools that follow symlinks, such as an editor's
search or its own test discovery, find the checkout again inside itself.
And it doesn't help ansible-test: the content root is still reached
through a link, so the [upstream fixes](#upstream-fixes) and
`PYDEVD_RESOLVE_SYMLINKS` are needed all the same. So it adds problems and
removes none.

**One mechanism, two defaults** (2026-10-21). Both kinds of project sync
into `collections_dir` with the same code, and `alai run` and `alai env`
point Ansible at it the same way; the root collection's link is the only
addition. Only the default differs, as Poetry and uv differ on where a
virtual environment goes by default (`virtualenvs.in-project`,
`UV_PROJECT_ENVIRONMENT`) without it being two mechanisms. Moving playbook
projects into the cache too was considered and rejected: `<root>/collections`
is what plain `ansible-playbook` finds beside the playbook, and what
ansible-navigator and execution environments see when they mount the
project into a container (not tested), neither of which can run alai. Moving collection
roots into the checkout fails as tested above.

`collections_dir` moves it, from the same places as for a playbook
project: `[project]` in `alai.toml`, `--collections-dir` or
`ALAI_COLLECTIONS_DIR`, never user or system config
([configuration.md](configuration.md#decisions)). Wherever it comes from,
it's an error (planned, 2026-10-21) when the tree and the checkout overlap:

- **The tree inside the checkout** brings back the problems above, and
  the link to the checkout would contain the tree.
- **The checkout inside the tree**, such as `collections_dir = "~/dev"`
  for a checkout at `~/dev/ansible_collections/ns/col`, leaves no place
  for the link, and an exact sync would remove the sibling checkouts.

### Checkouts already in the layout

When the checkout already is at `…/ansible_collections/<ns>/<name>`, no tree
is needed for ansible-test itself: every mode works as it does today. But
alai must not install dependencies next to it. `sync` is exact
([install.md](install.md)): every `<ns>/<name>` beside the ones it installs
that the lock doesn't name is removed, and `~/dev/ansible_collections` often
holds many checkouts side by side, with `.git` and uncommitted work. So a
collection project syncs into a tree alai builds and owns, as above, with
the checkout linked in, wherever the checkout is. A sibling checkout of a
dependency is an editable path source, linked into that tree too (done
2026-10-22, [lockfile.md](lockfile.md#other-sources)).

## Debugging

**Debugging is a requirement for any layout or design here, not an
afterthought.** A developer should be able to open the checkout in an editor,
set a breakpoint in a plugin, and have it hit when they run
`alai run ansible-playbook …` or `alai run ansible-test …`. That shouldn't
need hand-written path mappings, environment variables or launch settings
alai could have provided. If two designs otherwise work equally well, prefer
the one that's easier to debug. Where some setup can't be avoided, alai should
do it, or print it ready to paste.

Two cases to cover:

- **Controller-side code** (filter, lookup, action, inventory and callback
  plugins, and the `module_utils` they import) runs in the `ansible-playbook`
  process, so a debugger attached to that process sees it.
- **Modules** are packaged by AnsiballZ and run in a separate process, on the
  target host. Even on `localhost` they're loaded from the package, not from
  the checkout, so breakpoints need extra support. Needs checking.

Not tested end to end; what's known:

- **debugpy matches breakpoints on real paths** (checked 2026-10-22 with the
  pydevd bundled in VS Code's debugpy 1.8.20): `canonical_normalized_path`
  is the `realpath`, so a breakpoint set in the checkout matches code loaded
  through the tree without any setting. `PYDEVD_RESOLVE_SYMLINKS=1`, which
  `alai run` and `alai env` set, only changes the path frames are shown
  with, so the call stack opens the checkout's file. `resolveSymlinks` in a
  launch configuration does the same, but VS Code's schema doesn't list it.
- **`ansible-test --dev-debug-on-demand`** configures debugpy itself and maps
  paths from the content root, which through a tree is the symlink path. It
  would need the same symlink resolution, passed through to the processes it
  starts. Needs checking.
- **Unit tests run from an editor without ansible-test** need
  `ansible_collections` importable: `PYTHONPATH=<tree>`, plus pytest's
  `--import-mode=importlib` so pytest doesn't derive module names from the
  test file's physical path. Untested.

Through the tree, all of this applies; the
[reversed layout](#the-reversed-layout), which avoids it, was rejected.
Seen while testing: run under
`python -m debugpy --listen` with no editor attached, ansible-test turns on
on-demand debugging and the `ansible-playbook` it starts hangs connecting
to the editor's port. Not tried with an editor attached.

## Decisions

- **`[project]` in a collection project's `alai.toml`** (2026-10-21).
  `[project] collections_dir` stays allowed there, for the test tree. Two
  keys say the same thing as the collection's own files: `[project]
  collections` and `galaxy.yml` `dependencies`, and `[project]
  requires_ansible` and `meta/runtime.yml` `requires_ansible`. Setting
  either in `alai.toml` is an error, so the collection files stay the one
  source of truth. Test-only collections go in `[project.groups.<name>]`.
  Overriding a dependency's metadata goes in `[project.overrides]`, which
  does that for playbook projects already. What `galaxy.yml` can't
  express, such as pinning a dependency to an index, goes in
  `[project.sources]`, which refers to collections by name without
  repeating `galaxy.yml`'s requirements, the way `[tool.uv.sources]` adds
  to `[project] dependencies` in uv. The alternatives were overriding the
  collection files (what alai resolves would then differ from what
  `ansible-galaxy` installs for users of the built collection), a
  fallback (adds little: a collection that needs either sets it in its
  own files for Galaxy) and ignoring them (the files drift apart).
  Overriding has a real use, testing against a different
  `requires_ansible` or dependency than the collection publishes, so an
  error leaves room to allow it later, when
  [`alai build`](#open-questions) is settled; see
  [user-questions.md](user-questions.md).

## Open questions

- **Whether `alai build` replaces `ansible-galaxy collection build`.** Two
  directions:
  - **A drop-in for `ansible-galaxy collection build`.** It reads
    `galaxy.yml`, honours `build_ignore` and the manifest directives, and
    produces the same artifact. `galaxy.yml` stays the source of truth, and
    a collection built with alai can still be built without it.

  Either way it shares `_build` with installing a directory source, which
  today copies what `ansible-galaxy collection build` builds, as a
  starting point rather than a requirement
  ([install.md](install.md#copying-a-directory-as-its-artifact-would-be)).
  - **A replacement, with the collection defined in `alai.toml`** (namespace,
    name, version, dependencies, build options), as `pyproject.toml`
    replaced `setup.py`. `galaxy.yml` becomes optional, or something alai
    generates. One file per project, but other tools read `galaxy.yml`
    from the source tree:
    - `ansible-galaxy` only treats a source directory as a collection if it
      has `galaxy.yml` (`_is_collection_src_dir` and `_get_meta_from_src_dir`
      in `lib/ansible/galaxy/`). Without one, installing the collection from
      a git URL or a directory fails for anyone not using alai.
    - ansible-test takes the collection version from `MANIFEST.json` or else
      `galaxy.yml` (`collection_detail.py`), for the version checks in the
      `validate-modules` and `pylint` sanity tests. Without either, those
      checks have no version to work with.
    - Other tools, such as ansible-lint and the antsibull tools, may read it
      too. Not checked.

    A generated `galaxy.yml` keeps those working, but it's a second copy that
    has to be committed and kept in sync.

  The answer may reopen the [decision](#decisions) on `[project]`. With a
  drop-in, `galaxy.yml` is the source of truth and `alai.toml` must not
  repeat it. With a replacement, `alai.toml` is the source and `galaxy.yml`
  follows from it.

- **What `alai build` checks beyond install's rules.** At least that every
  dependency requirement and `requires_ansible` can be read, so what alai
  builds installs. Routing in `meta/runtime.yml` and `eda_runtime.yml` that
  ansible-core or ansible-rulebook would ignore should probably only warn,
  as the formats change, EDA's most. Galaxy checks more on import, which a
  `--strict`-style option could add for authors publishing there. From
  galaxy-importer at `5f222ef` (2026-10-13), `schema.py` and
  `file_parser.py`:
  - `namespace`, `name`, `version`, `readme`, `authors` and `repository`
    are required.
  - The names match its name pattern and a maximum length, and the version
    passes `semantic_version.validate`, also within a maximum length.
  - `authors`, `tags` and `license` are lists of strings, and each license
    is an SPDX identifier.
  - Each dependency is a string `ns.name` other than the collection itself,
    with a string requirement that `semantic_version.SimpleSpec` reads.
    That takes `^1.0.0`, which ansible-galaxy can't install, which is how
    the 7 `finallycoffee.*` collections got onto Galaxy.
  - `meta/runtime.yml` has a `requires_ansible` that `packaging`'s
    `SpecifierSet` reads, within a maximum length.
  - The artifact's file name matches its namespace, name and version, and
    `FILES.json`'s checksums match the files.

## Not done yet

- **`alai add` and `alai remove`** in a collection project only edit
  groups (`--group`); without it they're an error. Editing `galaxy.yml`'s
  `dependencies` would need a YAML editor that keeps its formatting, as
  `toml_edit` does for `alai.toml`; instead, the plan is the collection
  defined in `alai.toml` ([README.md](README.md#roadmap)).
- **Cleaning trees** whose checkout has moved or gone, with the rest of
  `alai cache clean`; nothing records which checkout a tree belongs to
  except the hash.
- **Docs naming the ansible-core release** with the
  [upstream fixes](#upstream-fixes), once there is one.
- **Debugging end to end**: a controller-side breakpoint in the checkout
  hit under VS Code through `ansible-test shell --dev-debug-on-demand`
  (2026-10-22), once the shell's own `ansible` came first; modules not
  tried. Found on the way, all ansible-test or editor behaviour, now in
  docs/editors.md: `ansible-test shell` starts from an allow-list of
  variables (`common_environment()` in `util.py`); only its injector's
  commands are debugged; the Python Environments extension types
  `source .venv/bin/activate` into the debug terminal, shadowing the
  injector; and before ansible/ansible#85870 (2.22) the injected
  `python -m debugpy` needs debugpy installed, as only later releases put
  VS Code's bundled copy on `PYTHONPATH`.
- **Module breakpoints need an upstream fix**; alai works around it
  (2026-10-22, best effort). ansible-core records a module's source under
  the path the plugin loader found it at, the tree's link
  (`executor/module_common.py`), and the module side's debugger matches
  breakpoints against that by prefix, not real path, so one set in the
  checkout never hits. Its `source_mapping` setting, from paths the editor
  knows to paths Ansible knows (`_translate_path` in
  `_internal/_ansiballz/_builder.py`), would fix it, but ansible-test only
  fills it with the collection root mapped to itself, which it then drops
  (`get_source_mapping` in `host_profiles.py`). So `alai run` and `alai env`,
  in a collection project, add `<checkout>/` → `<tree link>/` to
  `_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG` or `_ANSIBLE_ANSIBALLZ_PYDEVD_CONFIG`
  when either is set (`_map_module_sources` in `_environ.py`). They're
  ansible-core's internal settings, so this may break with any release,
  and it misses `ansible-test integration`, which passes the setting as the
  `_ansible_ansiballz_debugpy_config` inventory variable. The fix belongs
  upstream, either of: ansible-test's `get_source_mapping` adding the
  content root's `realpath` mapped to the content root as given, which also
  makes tracebacks show the checkout; or ansible-core recording the
  `realpath` of a module's source as well. Remove the workaround once a
  release has one.

## What today's code must keep possible

- **Discovery**: only `alai.toml` marks a root, and a `galaxy.yml` beside it
  makes it a collection project (2026-10-27, see
  [configuration.md](configuration.md#decisions)). Until then `galaxy.yml`
  alone was a marker too, between `alai.toml` and `ansible.cfg`, so a
  checkout needed no `alai.toml`. An integration target's `ansible.cfg` and
  a source checkout inside a playbook project's `collections/` are part of
  the project above them, with nothing to rank. Installed collections
  (`MANIFEST.json`) are never markers. If `alai.toml` comes to define a
  collection (see [Open questions](#open-questions)), a repo without
  `galaxy.yml` is recognised from `alai.toml` instead.
- **`[project] collections_dir`** moves the tree, done.
- **`[project.groups.<name>]`** in `alai.toml` holds a collection project's
  test-only collections, done. `tests/*/requirements.yml` could be imported
  into groups once.
- **The installer** never writes through a symlink or deletes what's behind
  one ([install.md](install.md)). It does replace a directory it didn't
  install under a locked name, and an exact sync removes one the lock doesn't
  list, `.git` or not; the project's own collection's place is always
  replaced by the link ([install.md](install.md#the-projects-own-collection)).
  So a `collections_dir` set by hand mustn't share a directory with source
  checkouts, which the overlap check enforces only for the project's own.
- **`<cache_dir>/envs/<id>/`** holds the trees, `<id>` the first 16 hex
  digits of the SHA-256 of the checkout's resolved path. They should share a
  design with named environments: see [environments.md](environments.md).
- **Debugging.** Nothing in the layout, the installer or `alai run` should
  make breakpoints set in the checkout stop working. See
  [Debugging](#debugging).
- **`alai build`**, if it comes, leaves alai's own files (`alai.toml`, the
  lockfile) out of the artifact, as `MANIFEST.json` carries the collection's
  metadata. Whether it reads that metadata from `galaxy.yml` or `alai.toml` is
  an [open question](#open-questions).
