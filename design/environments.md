# Environments (future work)

**Status: not implemented.** Today a project has one collections directory,
`collections_dir`, and alai doesn't manage ansible-core or Python. This note
records how `requires_ansible`, a pinned ansible-core and several environments
per project would fit together, so the manifest, the lockfile and the layout
don't block them.

## What an environment is

uv's closest equivalent is the project environment: `.venv` in the project by
default, moved with `UV_PROJECT_ENVIRONMENT`, holding the interpreter chosen
by `.python-version`, `--python` or `UV_PYTHON`, within the project's
`requires-python`. alai's `collections_dir` plays the same part for
collections: where the lock is installed, `<root>/collections` by default,
moved with `--collections-dir` or `ALAI_COLLECTIONS_DIR`.

If alai manages ansible-core, an environment becomes:

- a collections tree;
- an ansible-core version, and the Python virtual environment it's installed
  in;
- which [groups](configuration.md#project-manifest) are installed.

## A range and a pin

uv keeps two separate things, and alai should too:

| | uv | alai |
|---|---|---|
| The versions the project supports, a range | `requires-python` in `[project]` | `requires_ansible` in `[project]` |
| The version to run, one version | `.python-version`, `--python`, `UV_PYTHON` | `ansible_version`, if alai manages ansible-core |

Decisions so far:

- **`requires_ansible` stays the range**, and keeps its name: it matches
  `requires-python` and `requires_ansible` in a collection's
  `meta/runtime.yml`, and it's an input to resolution. It's PEP 440 (see
  [versions.md](versions.md)).
- **A pin is a setting, not part of the manifest.** It's how alai runs, not
  what the project needs. As a setting it gets the flag and environment
  layering for free, so a CI matrix sets `ALAI_ANSIBLE_VERSION` per job
  against one lockfile. A pin outside `requires_ansible` is an error.
- **Named `ansible_version`** (decided 2026-10-20), for the ansible-core
  version. `ansible_core_version` was considered first, since `ansible` is
  also the community package (11.x, 12.x) that bundles ansible-core, so
  `ansible_version = "2.18"` could read as the wrong package; the shorter
  name was preferred, matching how `requires_ansible` already names
  ansible-core. A `.ansible-version` file, like `.python-version`, could
  feed it too. The resolver already takes it (`resolve(...,
  ansible_version=...)`, see [resolver.md](resolver.md)); nothing sets it
  yet.
- **Installing ansible-core belongs in `[python]`**, reserved in
  [configuration.md](configuration.md#project-manifest) for Python
  requirements, since ansible-core is a Python package.

## When `requires_ansible` isn't set

uv defaults a missing `requires-python` to the interpreter it finds
(`warning: No requires-python value found in the workspace. Defaulting to
>=3.15`). That makes the lock depend on the machine: two people with
different ansible-core versions would resolve different locks from one
manifest. Instead:

- **Unset means no constraint.** The resolver doesn't filter on
  `requires_ansible`, so the lock is the same everywhere. A one-line note
  that it isn't set is enough.
- **Detect when writing the file, not when resolving.** A future `alai init`
  can write `requires_ansible = ">=2.19"` from the ansible-core it finds, as
  `uv init` writes `requires-python`. Once committed, it's the same for
  everyone.
- **The installed ansible-core isn't used to choose, or checked.**
  ansible-core checks each collection's `requires_ansible` as it loads
  it, so `alai sync` and `alai run` leave that to it rather than find and
  run ansible-core from outside its environment (decided 2026-10-21, see
  [run.md](run.md#checking-ansible-core)).

## Several environments

The use case: running a project against several ansible-core versions, each
with its own collections tree, locally or as a CI matrix.

- **One at a time works without a new feature**, as `UV_PROJECT_ENVIRONMENT`
  does for uv: `ALAI_COLLECTIONS_DIR` (and, later, `ALAI_ANSIBLE_VERSION`)
  per job.
- **Named environments** would let a project list them once, like Hatch or
  tox environments, and pick one with `--env NAME`. Each could set an
  ansible-core version, groups and a directory:

  ```toml
  [environments.core-2-17]
  ansible_version = "2.17"
  groups = ["test"]

  [environments.core-2-19]
  ansible_version = "2.19"
  groups = ["test"]
  ```

  Open: whether these live in the manifest (the team shares the matrix) or
  in settings (the pin is a setting).
- **Only one can be `collections/`.** `ansible-playbook` finds the
  collections next to the playbook with no setup, so the default environment
  keeps that place. The others need somewhere else, inside the project
  (`.alai/envs/<name>/`) or in the cache, and are found through `alai run` or
  `ANSIBLE_COLLECTIONS_PATH`.
- **Collection projects' test trees are environments too.**
  [collection-development.md](collection-development.md) reserves
  `<cache_dir>/envs/<hash>/` for them: a tree holding the checkout and its
  dependencies, kept outside the checkout. Named environments for a playbook
  project and test trees for a collection project should share one design and
  one layout rather than grow two.

## One lock across the range: overlap, containment or forks

`requires_ansible` makes the lock valid for a range of ansible-core versions,
not one. How each locked collection's own `requires_ansible` has to relate to
it decides whether environments can hold different collection versions:

- **Overlap**: keep a collection version if its range overlaps the
  project's. The weakest rule. A project declaring `>=2.17` could lock a
  collection that needs `>=2.18`, so the lock fails on 2.17. Not enough.
- **Containment**: keep a collection version only if its range covers the
  project's, as uv requires a dependency to support all of `requires-python`.
  One version per collection, valid in every environment; environments only
  differ in ansible-core. Most collections only give a lower bound, so this
  mostly means a collection's minimum must be at or below the project's. The
  cost: a wide range holds every environment back to collections that
  support its oldest ansible-core.
- **Forks**: uv's universal resolution splits the lock where requirements
  differ by Python version, using markers, so one lockfile holds, say,
  community.general 10.x for ansible-core below 2.18 and 11.x from 2.18.
  Each environment installs its own slice. This is what gives environments
  their own collection versions from one lock, at the cost of a forking
  resolver and markers in the lock format.
- **A lock per environment** gets the same result with a simple resolver:
  each environment resolves against its pinned ansible-core into its own
  lockfile. Several lockfiles to keep in sync, and nothing guarantees they
  agree where they could.

Decided (2026-10-20, [resolver.md](resolver.md)): neither overlap nor strict
containment, but uv's rule for `requires-python`: a version is usable when
its `requires_ansible`, upper bounds ignored, starts no later than the
project's. Containment would reject every collection with an upper bound
whenever the project's range is open-ended (`>=2.17`). Unlike uv, alai warns
when a chosen version's upper bound ends inside the project's range, and
ansible-core checks each loaded collection's own range at run time
([run.md](run.md#checking-ansible-core)). Forks
or per-environment locks remain open for when environments exist; the
lockfile format leaves room for them (see [lockfile.md](lockfile.md)).
