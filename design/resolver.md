# Resolver

How alai chooses one version of every collection a project needs, in
`src/alai/_resolve.py` and `src/rust/src/resolve.rs`. Its output is the lock's `[[collection]]` entries;
`alai lock`, the command around it, is in [lockfile.md](lockfile.md).

## Decisions

- **PubGrub, in the native extension** (the `pubgrub` crate, 0.4, as uv and
  Cargo use), decided 2026-10-23 on the measurements in
  [PubGrub, measured](#pubgrub-measured-2026-10-23). resolvelib, as pip,
  PDM and ansible-galaxy use, was the first choice: Ansible dependency
  graphs are a few levels deep, and writing a PubGrub in Python looked
  like 1,000 to 1,500 lines for better conflict explanations alone. With
  the Rust extension there anyway, the crate costs a provider and a
  formatter, and what it buys is the search: it proves a manifest
  unsatisfiable in a millisecond where resolvelib, which backjumps without
  learning, ran past ten minutes on 90 unpinned collections with one
  conflict, and the proof is the explanation, naming the transitive cause
  resolvelib's conflict causes never reached. The division of labour:
  `_resolve.py` fetches every index file the requirements can reach (the
  same closure the prefetching already fetched), applies every rule below,
  and hands one table across (`_native.resolve`): each collection's
  versions with their dependencies as clauses, why a version can't be
  chosen (a predicate: `is yanked`), the version to prefer, and the words
  for the roots. The Rust side chooses, or explains; nothing crosses the
  boundary during the search. Versions cross as `Version._key` and their
  text, so Rust orders them exactly as Python does without parsing, and a
  requirement's bound is the first or last text a precedence can have, so
  `==1.0.0` matches `1.0.0+build` as ansible-galaxy's does. The lock,
  `reasons` and the warnings are computed in Python from the result, as
  before, and the first-index rule, overrides, `requires_ansible` and the
  kept-version preference are Python's, so a strategy setting later
  changes nothing in Rust. A version that can't be chosen is still offered
  and then reported unavailable with its reason, which is how uv gets
  `a.a==2.0.0 is yanked` into an explanation. `pubgrub` 0.4 needs Rust
  1.92, so that's the crate's `rust-version` now
  ([performance.md](performance.md)).
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
  index each would come from, rather than when the search reaches them;
  the search runs in one native call, so the table is complete before it
  starts. It's
  index files only, one per collection however many versions it has;
  resolving never downloads an artifact, and `sync` later downloads only the
  locked versions'. Taking every version's dependencies, rather than only
  those of versions that fit, costs a few extra small files (13 fetched for
  11 locked collections on the project below, the extras named only by old
  versions) and is kept for simplicity. With the listing's
  `sha256`, a warm resolve fetches no index files at all. Measured on
  `index.alai.sh` with 11 collections (7 listed, 2 in a group, 2 pulled in):
  2.5 s cold, 0.2 s warm.
- **Order of decisions** (PubGrub's `prioritize`): a collection with no
  candidate first, so the conflict is found at once; then by conflicts so
  far, an exact pin, a requirement of the project or a group, the fewest
  candidates, and the name, so a run is repeatable. Ties go breadth first.
- **Failures explain themselves, in uv's words.** The error is PubGrub's
  derivation, one sentence a line (`Because cisco.ios>=11.0.0,<11.4.0
  depends on ansible.netcommon>=8.1.0 and ansible.netcommon>=8.1.0 depends
  on ansible.utils>=3.0.0, we can conclude that cisco.ios>=11.0.0,<11.4.0
  depends on ansible.utils>=3.0.0.` ... `And because the project depends on
  ansible.utils<3.0.0 and the project depends on cisco.ios>=11.0.0, we can
  conclude that the project's requirements are unsatisfiable.`), written by
  a formatter in `resolve.rs` that follows uv's `PubGrubReportFormatter`:
  ranges simplified to the versions that exist (`>=11.0.0` rather than
  every gap), a version's reason (`a.a==2.0.0 is yanked`, `needs a newer
  ansible-core than the project's >=2.17`, `doesn't support ansible-core
  2.19.0`, `has unreadable metadata`), `no configured index has c.c` or
  `index 'alai' doesn't have c.c` for a dependency with no versions, `only
  a.a<=11.0.0 is available` or `only a.a==1.0.0 and a.a==2.0.0 are
  available` where what exists is the point, with `, from ../col,` or `,
  the project's own collection,` after a local collection,
  `([project.overrides])` after an overridden dependency, `group 'test'
  depends on` and `the project has group 'test'` for a group's requirement,
  and `you require` and `your requirements are unsatisfiable` for the
  command line (uv's words for `uv pip`). Before it's written, the tree is
  pruned, as uv prunes it (`error.rs` in uv-resolver), in one pass from the
  bottom up, so each rule sees causes the others have already reduced, by
  three rules: versions a rule left out for one reason, each its own fact,
  merged into one about them all (`every version of
  ansible.netcommon>=5.0.0 needs a newer ansible-core than the project's
  >=2.17`; without it, a `requires_ansible` case of the common shape was 18
  lines naming 32 versions, so uv's `collapse_unavailable_versions` came
  back, 2026-10-23); two statements that versions of a collection depend
  on the same thing merged into one about their union; and a "no versions"
  fact dropped wherever the other cause already states the conclusion on
  the versions that exist, which is every time it only cut gaps between
  versions out of a range. Sets are compared on the versions that exist,
  so nothing is simplified first; the formatter simplifies what it prints.
  uv has more rules, one per shape; four others were ported and then
  measured to change nothing once the last rule was made general, so they
  went (2026-10-23). That rule is wider than uv's: uv keeps a fact that
  leaves one version, for `only a.a==1.0.0 is available`, where `a.a==1.0.0
  depends on` says as much here, and a fact beside a derivation it narrows
  is dropped here where uv's leave it, which on the `ansible` package's
  collections was a list of every `ansible.netcommon` 8.x version.
  pubgrub's own `collapse_no_versions` was tried first and merged a
  dependency having no versions at all into the dependency, leaving
  `Because a.a depends on c.c>=1.0.0 and the project depends on a.a, ...
  unsatisfiable`. Other differences from uv: up to three available
  versions are named where uv gives a range, as `a.a<1.5.0, a.a==2.0.0`
  for two versions reads worse, and a range's bounds are moved in to the
  versions there are (`a.a<=1.2.0, a.a>=2.0.0`, not uv's `a.a<1.3.0,
  a.a>1.3.0`); what a sentence is about is named exactly when its set
  covers one version (`b.b==1.0.0 depends on`, `b.b==1.0.0 cannot be
  used`) where uv writes the range (`b.b<2.0.0 depends on`), but a
  requirement reads as written, `!=` included (`the project depends on
  a.a!=1.1.0`), as it's what the user or the collection wrote; a
  conclusion that something is incompatible with the root or a group
  reads `b.b cannot be used`, as the root is always chosen; and an `==`
  pin is recognised by precedence (`there is no version of a.a==1.3.0`),
  as a requirement's `==` is a range of texts (build metadata), not
  pubgrub's singleton. The first line is `no solution found when resolving
  collections:`, after uv's `No solution found when resolving
  dependencies:`.
- **Each change has a reason** (`Resolution.reasons`), worked out once the
  resolution is known rather than traced through the search: what
  needs an added collection; that nothing needs a removed one; and for a
  locked version not kept, the first of: upgrading was asked for, its
  index changed, a requirement on it it doesn't meet, it's no longer on its
  index, a filter leaves it out, or a dependency it can't have. For the
  last, a dependency conflicting with a requirement from something else
  (the project, another collection) is preferred over one only the
  collection's replacement asks differently for: with a bundle pinning
  everything exactly, every other pin moved because the bundle did, and
  naming one of those would be circular. A locked version the search
  passed over for none of these reasons gets none rather than a guess.
- **Dependencies' bare versions show with `==`**: the index allows `2.8.0`
  for an exact version, and messages show it as `ns.name==2.8.0`, as a
  manifest would need it.
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
which fails unless what needs it can backtrack (`Because only
ns.col==1.0.0, from ../col, is available and ns.col==1.0.0 needs a newer
ansible-core than the project's >=2.17, we can conclude that ns.col cannot
be used.`); one whose range ends inside the project's is a
warning; `ignore_requires_ansible` skips both. The first version checked
nothing, as for the project's own collection, but that one *is* the
project's range, and a path source is just another collection. As for
every collection, its `requires_ansible` only filters its own candidates;
it doesn't narrow the range for anything else. A requirement its version
doesn't meet backtracks, or fails with `only ns.col==1.0.0, from ../col, is
available and the project depends on ns.col>=2.0.0`, as for the project's
own collection. `-v` gives `ns.col now comes from ../col` when the
source changed, and `ns.col's galaxy.yml now says 1.1.0` when the version
did.

A git source is the same, read from its commit's files, as is a tarball,
read from its `MANIFEST.json`. `-v` gives `git+<url> is now at commit
<short>` when `-U` or `-P` moved a git source's `rev` to another commit.

## PubGrub, measured (2026-10-23)

Decided for PubGrub the same day, on these numbers; the resolvelib
column is what the Python resolver did on the day it was replaced. They
are from the
`scratch/acd` project, the 90 collections in the `ansible` 15 package
(ansible-build-data's `15/ansible.in`) with no version specifiers, which is
what a project that lists everything it uses and pins nothing looks like.
It resolves to 92 collections (`everpure.flasharray` and
`everpure.flashblade` come in through `purestorage.*`), from 94 index
files holding 5,199 versions; only 41 of the 90 have any dependencies, and
almost all of those are on `ansible.netcommon`, `ansible.utils` or
`ansible.posix`. The PubGrub side is a 250-line standalone Rust program
(not in the repository) reading the same cached index files, with
alai's candidate order (stable before pre-release, highest first), yanked
versions dropped, and a `ReportFormatter` that simplifies each range to
the versions that exist, as uv does; no `requires_ansible`, overrides or
locked-version preference. Both chose the same versions in every case
that resolved. Timings are warm, on the cached index; resolvelib's are
after the `find_matches` fixes below, with what they were before in
brackets.

| Project | resolvelib | PubGrub prototype |
|---|---|---|
| `scratch/acd` as is | 92 rounds, no backtracking; `alai lock` 0.07 s warm, of which resolvelib ~40 ms, the rest parsing the index files | 0.2 ms to resolve, 93 `choose_version` calls; 7 ms to load the files |
| plus `ansible.netcommon<2.6.0` | 92 rounds, 148 candidates rejected; 80 ms | 1.6 ms, 274 calls |
| plus `ansible.utils<3` | 294 rounds, 101 conflicts, 1,794 rejected; 0.36 s (1.03 s) | 1.1 ms, 251 calls |
| `ansible.netcommon<2.6.0` and `cisco.ios>=11` alone (no solution) | 297 rounds; 0.04 s (0.19 s) | 0.1 ms, 13 calls |
| `ansible.utils<3` and `cisco.ios>=11` alone (no solution, two levels deep) | 1,533 rounds; 0.11 s (0.59 s) | 0.25 ms, 29 calls |
| the 90 with `ansible.utils<3` and `cisco.ios>=11` (no solution) | gives up at the 5,000-round limit after 1.8 s (4.8 s); with the limit raised to 100,000, still running when killed at 10 minutes | 0.6 ms, 58 calls |

Before the fixes, the 1.03 s case was mostly alai's own provider, not
resolvelib. On each of its 2,400 calls `find_matches` was handed every
candidate resolvelib had rejected for that collection, a list resolvelib
extends with duplicates on each backjump (12.8 million entries over the
run), and hashed each one's `Version` to build the set it filters by, a
Python-level `__hash__` call each; it then re-filtered and re-sorted the
file's versions against requirements it had seen before. Now (2026-10-23)
the set is of version texts, kept on each candidate, whose hashes Python
caches; the filtered, ordered list is cached per collection and set of
requirements, with only the rejected ones taken out per call; and the
versions a filter left out are recorded once by version, not counted
again on every call, which also stops a failure's `versions left out`
counts growing with the backtracking. What's left in that case is
resolvelib's own bookkeeping, `_backjump` copying states and that list.
The resolver's share of every resolving case is well under a second, so
speed alone doesn't decide it: these graphs are shallow, and the cost is
in network and parsing. With the move done, `alai lock` on `scratch/acd`
is 0.08 s warm, the three unsatisfiable cases 0.15 to 0.2 s of wall time
with Python's start-up, each explained in two or three sentences, the
one that gave up after 5,000 rounds included.

What does differ is failure. resolvelib stops with the requirements on the
one collection it couldn't pin, one line per version that asked:

```
error: no version of ansible.utils meets every requirement on it:
  ansible.utils<3 (from the project)
  ansible.utils>=3.0.0 (from ansible.netcommon 8.7.1)
  ansible.utils>=3.0.0 (from ansible.netcommon 8.7.0)
  ... 12 more ansible.netcommon versions ...
  ansible.utils>=3.0.0 (from ansible.netcommon 8.1.0)
```

That never mentions `cisco.ios>=11`, which is what needs ansible.netcommon
8 and so is the thing to change; with a longer chain, or more than one
conflict, the causes resolvelib hands back are whatever it last backtracked
over. And past the round limit there's nothing at all: `gave up after
5000 rounds of backtracking without finding a resolution`, where the same
requirements with everything else listed were proved unsatisfiable in
under a millisecond; raising the limit doesn't help, as ten minutes
weren't enough for 100,000 rounds. PubGrub's derivation for the same case, through the
prototype's formatter:

```
Because ansible.netcommon >=8.1.0 depends on ansible.utils >=3.0.0 and
cisco.ios >=11.0.0, <=11.3.0 depends on ansible.netcommon >=8.1.0,
cisco.ios >=11.0.0, <=11.3.0 depends on ansible.utils >=3.0.0. (1)

Because ansible.netcommon >=8.5.2 depends on ansible.utils >=3.0.0 and
cisco.ios >=11.4.0 depends on ansible.netcommon >=8.5.2, cisco.ios >=11.4.0
depends on ansible.utils >=3.0.0.
And because cisco.ios >=11.0.0, <=11.3.0 depends on ansible.utils >=3.0.0
(1), cisco.ios >=11.0.0 depends on ansible.utils >=3.0.0.
And because the project depends on ansible.utils <3.0.0 and the project
depends on cisco.ios >=11.0.0, the project's requirements can't all be met.
```

uv 0.13.5 on the same shape (local wheels named for these collections,
`uv lock --no-index --find-links`) reads the same, with its own words
("we can conclude that", "your project's requirements are unsatisfiable")
and `>=11.0.0,<=11.3.0` without the spaces. Without the simplification
step, PubGrub's default formatter prints every gap between versions
(`cisco.ios >=11.0.0, <11.4.0 | >11.4.0, <11.4.1 | ...`), so the formatter
isn't optional.

What changed in `_resolve.py` with the move: `explain` and the round limit
went, replaced by the formatter and pruning in `resolve.rs`;
`get_preference` became `prioritize`, and `find_matches` the table's
`_entry`, so the yanked, pre-release, `requires_ansible`, override and
kept-version rules stayed as they were, in Python; `reasons` works off
the result and didn't change. The code got no smaller, and is split
across two languages, with the stub to keep in step; what was bought is
the failure output and the end of the round limit. The prototype, the
scenarios and a round-counting runner for the old resolver are in
`scratch/pubgrub/` (ignored by git, as `scratch/acd` is).

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
