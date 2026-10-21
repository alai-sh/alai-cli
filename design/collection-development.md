# Collection development (future work)

**Status: not implemented.** alai targets playbook projects first: a project
that uses collections, with `ansible.cfg`, playbooks and `collections/`. This
note records what's known about the other kind, the source repo of a
collection being developed, so that work can start from here and so today's
choices don't get in its way. The findings were checked against ansible-core
2.21.5 (PyPI) and the ansible-core source at `616e7c05e0`, on 2026-10-19.

## Two kinds of project

| | Playbook project | Collection repo |
|---|---|---|
| Marker | `alai.toml`, `ansible.cfg`, `collections/` | `galaxy.yml` |
| Requirements | `[project] collections` in `alai.toml` | `dependencies` in `galaxy.yml` |
| Target ansible-core | `[project] requires_ansible` | `requires_ansible` in `meta/runtime.yml` |
| Installs to | `<root>/collections` | A tree alai owns, with the checkout linked in: see [Where the tree lives](#where-the-tree-lives) and [Checkouts already in the layout](#checkouts-already-in-the-layout) |
| Run with | `ansible-playbook`, `alai run` | `ansible-test`, plus `ansible-playbook`, `ansible` and `ansible-doc` against the checkout, through `alai run` |

`galaxy.yml` is the collection's equivalent of `pyproject.toml`, but alai
settings shouldn't go in it. ansible-galaxy warns, rather than fails, on keys
it doesn't know (`_normalize_galaxy_yml_manifest` in
`lib/ansible/galaxy/collection/concrete_artifact_manager.py`: "Found unknown
keys in collection galaxy.yml"), so anything of alai's there would print that
warning on every build and install. A collection repo's alai settings and
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

  The [reversed layout](#a-layout-that-works-today) has none of these
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
immediately. The way to do it is a tree alai builds and
owns:

```
<tree>/ansible_collections/<ns>/<name>  -> symlink to the checkout
<tree>/ansible_collections/<dep_ns>/<dep_name>  (linked from the cache)
```

with `ANSIBLE_TEST_CONTENT_ROOT=<tree>/ansible_collections/<ns>/<name>`, run
from the checkout. Tested with a collection whose module_utils import a
dependency's module_utils:

| Command | Result |
|---|---|
| `integration --local` | Works; the dependency loads from the tree |
| `sanity --local` (tests needing no extra requirements) | Works |
| `sanity --docker` (`validate-modules`, `import`, `pylint`) | Works; the container gets copies |
| `units --docker` | Works |
| `units --local` | Fails: see [Why local unit tests fail](#why-local-unit-tests-fail) |
| Anything with `--venv` | Fails: see [Why venv delegation fails](#why-venv-delegation-fails) |

Neither failure can be fixed from outside ansible-test; see
[Upstream fixes](#upstream-fixes).

### Why local unit tests fail

ansible-test passes test paths relative to the working directory, so pytest
sees the physical path. ansible-test's pytest plugin
(`ansible_pytest_collections.py`) then rejects the file as outside
`ANSIBLE_COLLECTIONS_PATH`. Run by hand with absolute test paths through the
symlink, the same tests pass, and the collection's code is loaded through the
symlink.

### Why venv delegation fails

The origin host's working directory comes from `os.getcwd()`
(`get_working_directory` in `host_profiles.py`), and delegation passes it to
the child as `ANSIBLE_TEST_CONTENT_ROOT`. The child gets the physical path and
finds no `ansible_collections` in it.

### Upstream fixes

The working directory is always physical, so changing it or setting `PWD`
before calling ansible-test changes nothing. The fixes belong in ansible-test,
and both are small:

1. units passes absolute test paths built from the content root;
2. the origin host's working directory is the content root rather than
   `os.getcwd()`;

plus documenting `ANSIBLE_TEST_CONTENT_ROOT` so tools can rely on it.

### A layout that works today

Reverse the link. Keep the real checkout inside the tree, and put a symlink
wherever is convenient to work from. The physical path then has the layout
ansible-test needs, so every mode works, and nothing is loaded through a
symlink (see [Debugging](#debugging)).

### Where the tree lives

Not inside the checkout. A link at `.alai/ansible_collections/<ns>/<name>`
inside the collection would point back at the collection itself, and sanity
file scans and the container copy would pick up the dependencies as part of
the collection. The plan is to keep it in the cache, keyed by the checkout's
path (`<cache_dir>/envs/<hash>/`), as Poetry keeps virtual environments
outside the project.

### Checkouts already in the layout

When the checkout already is at `…/ansible_collections/<ns>/<name>`, no tree
is needed for ansible-test itself: every mode works as it does today. But
alai must not install dependencies next to it. `sync` is exact
([install.md](install.md)): every `<ns>/<name>` beside the ones it installs
that the lock doesn't name is removed, and `~/dev/ansible_collections` often
holds many checkouts side by side, with `.git` and uncommitted work. So a
collection project syncs into a tree alai builds and owns, as above, with
the checkout linked in, wherever the checkout is. A sibling checkout of a
dependency can be a local path source, linked into that tree too.

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

- **debugpy matches breakpoints on paths as given**, unless symlink
  resolution is turned on: `"resolveSymlinks": true` in a VS Code launch
  configuration, or `PYDEVD_RESOLVE_SYMLINKS=1`. Code loaded through the tree
  has symlink paths, while an editor opened on the checkout sets breakpoints
  on real paths, so through a tree they won't hit without that option or a
  `pathMappings` entry from the tree to the checkout. `alai run` could set
  `PYDEVD_RESOLVE_SYMLINKS=1` for its children.
- **`ansible-test --dev-debug-on-demand`** configures debugpy itself and maps
  paths from the content root, which through a tree is the symlink path. It
  would need the same symlink resolution, passed through to the processes it
  starts. Needs checking.
- **Unit tests run from an editor without ansible-test** need
  `ansible_collections` importable: `PYTHONPATH=<tree>`, plus pytest's
  `--import-mode=importlib` so pytest doesn't derive module names from the
  test file's physical path. Untested.

[The reversed layout](#a-layout-that-works-today) avoids all of this, since
nothing is loaded through a symlink.

## Open questions

- **`[project]` in a collection repo's `alai.toml`.** `[project]
  collections_dir` stays allowed there, for the test tree. Two keys say the
  same thing as the collection's own files: `[project] collections` and
  `galaxy.yml` `dependencies`, and `[project] requires_ansible` and
  `meta/runtime.yml` `requires_ansible`. When `alai.toml` sets one, it could:
  - **override** the collection file. This lets alai test against something
    other than what's published, but what alai resolves then differs from
    what `ansible-galaxy` installs for users of the built collection.
  - **be a fallback**, used only when the collection file doesn't set it.
    This adds little: `galaxy.yml` `dependencies` and `requires_ansible` are
    optional, but a collection that needs them sets them there for Galaxy.
  - **be ignored**, or **ignored with a warning**. The two files then drift
    apart, and only the warning tells anyone.
  - **be an error.** This leaves one source of truth: the collection files.

  Leaning towards an error. Test-only collections go in `[project.groups.<name>]`.
  Overriding a dependency's metadata goes in `[project.overrides]`, which does that
  for playbook projects already. What `galaxy.yml` can't express, such as
  pinning a dependency to an index, goes in `[project.sources]`, which refers
  to collections by name without repeating `galaxy.yml`'s requirements, the
  way `[tool.uv.sources]` adds to `[project] dependencies` in uv.
  This leaning assumes `galaxy.yml` stays; see the next question.
  Raised on 2026-10-20: overriding has a real use, testing against a
  different `requires_ansible` or dependency than the collection publishes,
  so `[project]` there may become the way to override rather than an error.
  Still open.
- **Whether `alai build` replaces `ansible-galaxy collection build`.** Two
  directions:
  - **A drop-in for `ansible-galaxy collection build`.** It reads
    `galaxy.yml`, honours `build_ignore` and the manifest directives, and
    produces the same artifact. `galaxy.yml` stays the source of truth, and
    a collection built with alai can still be built without it.
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

  The answer decides the question above. With a drop-in, `galaxy.yml` is the
  source of truth and `alai.toml` must not repeat it. With a replacement,
  `alai.toml` is the source and `galaxy.yml` follows from it.

## What today's code must keep possible

- **Discovery** has room for `galaxy.yml` as a marker between `alai.toml` and
  `ansible.cfg`. That order matters because integration targets sometimes
  contain an `ansible.cfg`. Until collection repos are supported,
  `galaxy.yml` isn't a marker, so alai in a collection repo behaves as in any
  other directory. Installed collections (`MANIFEST.json`) are never markers.
  If `alai.toml` can define a collection (see
  [Open questions](#open-questions)), a repo without `galaxy.yml` is
  recognised from `alai.toml` instead.
- **`[project] collections_dir`** will mean the test tree for a collection
  repo, not `<root>/collections`.
- **`[project.groups.<name>]`** in `alai.toml` will hold a collection repo's test-only
  collections. `tests/*/requirements.yml` could be imported into groups once.
- **The installer** never replaces or removes a collection directory it
  didn't install (no alai marker, a `.git`, or a symlink). Collection repos
  depend on this; playbook projects benefit from it too.
- **`<cache_dir>/envs/`** is reserved for test trees, which should share a
  design with named environments: see [environments.md](environments.md).
- **Debugging.** Nothing in the layout, the installer or `alai run` should
  make breakpoints set in the checkout stop working. See
  [Debugging](#debugging).
- **`alai build`**, if it comes, leaves alai's own files (`alai.toml`, the
  lockfile) out of the artifact, as `MANIFEST.json` carries the collection's
  metadata. Whether it reads that metadata from `galaxy.yml` or `alai.toml` is
  an [open question](#open-questions).
