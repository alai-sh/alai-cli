# Resolver

How alai chooses one version of every collection a project needs, in
`src/alai/_resolve.py`. Its output is the lock's `[[collection]]` entries;
`alai lock`, the command around it, is in [lockfile.md](lockfile.md).

## Decisions

- **resolvelib**, as pip, PDM and ansible-galaxy use, behind one function
  (`resolve`), so another resolver (a PubGrub, or a compiled extension for
  speed) can replace it without touching the rest. Writing PubGrub now was
  rejected: 1,000 to 1,500 lines of subtle code for better conflict
  explanations, when Ansible dependency graphs are a few levels deep and
  resolvelib's conflict causes, turned into messages, read well enough. The
  only pure-Python PubGrub, `mixology`, hasn't had a release since 2019.
- **One resolution for the project and every group**, as uv does; see
  [lockfile.md](lockfile.md).
- **Highest version first**, except that a version in the current lock
  comes first while it still fits and its index URL still matches, so an
  unchanged manifest gives an unchanged lock. `upgrade` (all, or by name)
  drops that preference.
- **Where a collection comes from: its `[project.sources]` entry, else the
  first configured index whose `collections.json` has it**, whether the
  project lists it or something depends on it, and only that index's
  versions are candidates. That's uv's default `index-strategy`,
  `first-index` ("limiting the candidate versions to those present in the
  first index that contains the package", to prevent dependency
  confusion): custom indexes are searched before the built-in default, so a
  public mirror can't shadow a private name, even with a newer version. The
  index spec's rule, a dependency from its parent's index, was tried first
  and dropped (2026-10-20): it made the same name reachable from two
  indexes through different parents, which had to be an error, where
  deciding by name alone can't conflict. A higher-priority index with an
  incomplete copy of a collection gives a normal resolution error, which a
  `[project.sources]` entry fixes. A source naming an index that isn't
  configured, and a listed collection no index has, are errors with a
  did-you-mean; a dependency no index has rules out the version needing it,
  so the resolver can backtrack. uv's other strategies
  (`unsafe-first-match`, `unsafe-best-match`) and its `explicit = true`
  indexes, only used for pinned packages, could be added as settings.
- **Which versions are candidates**, among those every requirement accepts:
  - **Overrides** replace every requirement on their collection, the
    project's own included, before anything is compared.
  - **Yanked** versions are skipped unless locked, or pinned exactly
    (`==`) by the user: the project's or a group's requirement, or an
    override. That's uv's behaviour, after PEP 592; a dependency's `==` pin
    doesn't count, so a collection can't pull a yanked version in. Every
    lock that uses one, pinned or kept from the lock, warns that it's
    yanked, since yanks are often for security. Tested with uv 0.13 against
    a local simple index with `demo 2.0` yanked: `demo` and `demo>=1.5`
    left it out (the second with no solution), `demo==2.0` chose it with
    "warning: `demo==2.0` is yanked", a dependency's `demo==2.0` had no
    solution, and a locked 2.0 was kept, with the warning, after loosening
    the requirement. The index spec says a yanked version MUST NOT be
    chosen in a new resolution; this repo decides client behaviour.
  - **Unreadable metadata** (a `deps[].req` or `requires_ansible` that
    can't be parsed, such as `^3.0.0`) skips the version; a warning names
    it when it's newer than what was chosen, since that's when it changed
    the outcome ([versions.md](versions.md)).
  - **Pre-releases come after every stable version**, so one is chosen
    only when no stable version fits, at any depth: uv's default,
    `if-necessary` ("prefers stable versions over pre-releases, falling
    back to pre-releases only if every stable candidate" is ruled out).
    Tested with uv 0.13: with `demo 1.0` and `demo 2.0rc1`, `demo` gives
    `1.0`, while `demo>=1.5`, directly or from a dependency, gives
    `2.0rc1`. ansible-galaxy's rule (pre-releases only when pinned exactly
    or nothing else exists) was the first version here; uv's covers both of
    its cases and doesn't fail where only a pre-release fits. No warning
    when one's chosen. uv's other modes (`disallow`, `allow`, `explicit`)
    and per-collection overrides can be a `prerelease` setting later.
  - **`requires_ansible` filters by lower bounds only; upper bounds are
    reported, never used to rule a version out.** A version is a candidate
    when its `requires_ansible` starts no later than the project's.
    That's uv's rule for `requires-python` ("only considers lower bounds and
    ignores upper bounds entirely", in its resolution docs), checked to hold
    for `uv lock`, `uv sync` and `uv pip install` alike. Containment, the
    earlier leaning in [environments.md](environments.md), would reject
    every collection with an upper bound whenever the project's range is
    open-ended. Where alai differs from uv is the reporting: a chosen version
    whose upper bound ends inside the project's range gives a warning, so a
    lock that doesn't cover all of `requires_ansible` says so, where uv is
    silent. The installed ansible-core is checked against every loaded
    collection's own range, upper bounds included, by ansible-core itself
    at load time, not by alai ([run.md](run.md#checking-ansible-core)). A project range with
    no lower bound filters nothing; `ignore_requires_ansible` skips the
    check and the warning for the collections it lists; no
    `requires_ansible` in the project skips both for all.
- **Resolving for one ansible-core version** (`ansible_version`, not
  exposed yet): when the exact version is known, each candidate's own
  `requires_ansible` must contain it, upper bounds included, compared by
  its base version as ansible-core compares itself (2.19.0rc1 counts as
  2.19.0), the way ansible-galaxy filters by the running ansible-core. That
  replaces the lower-bound rule and its upper-bound warning, which only
  exist because the version isn't known. It must fall inside the project's
  `requires_ansible`, or resolution fails before it starts;
  `ignore_requires_ansible` still applies. The command line, environment
  and `alai.toml` can feed it later (see [environments.md](environments.md)).
- **Prefetching**: when a collection's index file arrives, the index files
  of every dependency any of its versions names are fetched at once, on the
  index each would come from, rather than when resolvelib reaches them. It's
  index files only, one per collection however many versions it has;
  resolving never downloads an artifact, and `sync` later downloads only the
  locked versions'. Taking every version's dependencies, rather than only
  those of versions that fit, costs a few extra small files (13 fetched for
  11 locked collections on the project below, the extras named only by old
  versions) and is kept for simplicity. With the listing's
  `sha256`, a warm resolve fetches no index files at all. Measured on
  `index.alai.sh` with 11 collections (7 listed, 2 in a group, 2 pulled in):
  2.5 s cold, 0.2 s warm.
- **Order of decisions** (resolvelib's `get_preference`): collections that
  caused backtracking, then those pinned with `==`, then those the project
  lists, then by name, so a run is repeatable.
- **Failures explain themselves.** For each collection no version fits, the
  error lists every requirement on it and where it came from (`the
  project`, `group 'test'`, or the collection and version that depends on
  it, with any override shown), and how many versions that met them were
  left out and why (yanked, unreadable, or needing a newer ansible-core). A dependency an index doesn't have is reported as such.
- **Each change has a reason** (`Resolution.reasons`), worked out once the
  resolution is known rather than traced through resolvelib's search: what
  needs an added collection; that nothing needs a removed one; and for a
  locked version not kept, the first of: upgrading was asked for, its
  index changed, a requirement on it it doesn't meet, it's no longer on its
  index, a filter leaves it out, or a dependency it can't have. For the
  last, a dependency conflicting with a requirement from something else
  (the project, another collection) is preferred over one only the
  collection's replacement asks differently for: with a bundle pinning
  everything exactly, every other pin moved because the bundle did, and
  naming one of those would be circular. A locked version resolvelib
  passed over for none of these reasons gets none rather than a guess.
- **Dependencies' bare versions show with `==`**: the index allows `2.8.0`
  for an exact version, and messages show it as `ns.name==2.8.0`, as a
  manifest would need it.
- **5,000 rounds** before giving up, each pinning or backtracking from one
  collection.
- **Index-file warnings** (lines needing a newer alai, unreadable lines) are
  passed back with the result rather than printed, so the command decides
  how to show them.

## Sources other than an index

Implemented for directories (2026-10-22). A collection read from a directory,
the project's own or a path source, has exactly one candidate, at the
version its `galaxy.yml` says (`_LOCAL` and `_local_version` in
`_resolve.py`), and never looks at an index, whatever the indexes have. A
path source's `galaxy.yml` dependencies are its dependencies, as an index
version's `deps` are; the project's own has none here, as its are the
project's requirements. A path source's `requires_ansible`, from its
`meta/runtime.yml`, is checked as an index version's is (2026-10-22): one
needing a newer ansible-core than the project's lower bound is left out,
which fails unless what needs it can backtrack (`ns.col 1.0.0, from ../col,
is left out (needing a newer ansible-core than the project's >=2.17), which
the project needs`); one whose range ends inside the project's is a
warning; `ignore_requires_ansible` skips both. The first version checked
nothing, as for the project's own collection, but that one *is* the
project's range, and a path source is just another collection. As for
every collection, its `requires_ansible` only filters its own candidates;
it doesn't narrow the range for anything else. A requirement its version
doesn't meet backtracks, or fails with `ns.col 1.0.0, from ../col, doesn't
meet:` and the requirements, as for the project's own collection. `-v` gives `ns.col now comes from ../col` when the
source changed, and `ns.col's galaxy.yml now says 1.1.0` when the version
did.

A git source will be the same, read from the checkout of its commit; a
tarball will be one candidate with its `cksum`.

## Not done yet

- **A `prerelease` setting** with uv's other modes, for the whole
  resolution or one collection, and an **index strategy** setting.
- **A way to set `ansible_version`** (a flag, `ALAI_ANSIBLE_VERSION`, an
  `alai.toml` key, or the installed ansible-core); see
  [environments.md](environments.md).
- **Git and tarball sources** as candidates, as a directory is now: see
  [Sources other than an index](#sources-other-than-an-index) and
  [lockfile.md](lockfile.md#other-sources).
- **Excluding newer versions** (asked for 2026-10-21): `--exclude-newer
  DATE` on the commands that resolve, and an `exclude_newer` setting, so a
  resolution only considers versions published before `DATE`, for
  reproducing an old lock or holding back versions too new to trust. In
  uv 0.13.0's help (not tried yet), `--exclude-newer` takes an RFC 3339
  timestamp, a local date (`2006-12-02`), a "friendly" duration (`1 week`,
  `30 days`) or an ISO 8601 one (`P7D`), with no months or years; it's
  `exclude-newer` in `uv.toml` and `UV_EXCLUDE_NEWER`, and
  `--exclude-newer-package PACKAGE=DATE` sets it for one package. uv
  compares each artifact's upload time; here the index gives each version
  a `published` time (`_index.py` already reads it), with the caveat in the
  index spec that a Galaxy version from before its 2023-05 migration has
  the migration time instead. Open: whether the cut-off is recorded in
  `alai.lock`, as uv records it in `uv.lock`'s `[options]`, so `lock
  --check` and `sync` know the lock was made with it; whether a duration is
  stored as given or as the date it resolved to; and whether it's a
  project setting in `alai.toml` as well as a user one.
