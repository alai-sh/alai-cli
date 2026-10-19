# Versions and version requirements

Two version schemes meet in a project, and they compare differently:

| | Collections | ansible-core (`requires_ansible`) |
|---|---|---|
| Versions | SemVer 2.0.0 (`1.2.3-rc.1+build`) | PEP 440 (`2.19.0rc1`) |
| Requirement syntax | ansible-galaxy's (`>=1.0,<2`, `*`, bare `1.2.3` means `==`) | PEP 440 specifiers (`>=2.17`, `~=2.17`, `==2.18.*`) |
| ansible-core's code | `meets_requirements` in `lib/ansible/galaxy/dependency_resolution/versioning.py` | `_does_collection_support_ansible_version` in `lib/ansible/plugins/loader.py`, using `packaging` |
| alai | `src/alai/_semver.py` | `src/alai/_pep440.py` |

The index spec's "Requirement syntax" section covers `deps[].req`, and its
"`requires_ansible` syntax" section the PEP 440 specifiers.

Checked against ansible-core at `616e7c05e0` (2026-10-15) and `packaging`
26.3, on 2026-10-19.

## Decisions

- **Two parsers for collection requirements.** `VersionReq.parse_index`
  follows `meets_requirements` exactly, for an index's `deps[].req`, which
  galaxy-sync copies verbatim from each collection's metadata. Index data is
  whatever authors published, and resolving it differently from
  ansible-galaxy would install something else. `VersionReq.parse` is the
  stricter form `alai.toml` takes. It drops the parts that read as something
  else: a bare version (`1.2.3`, easily read as "1.2.3 or later"), `=`, build
  metadata (which comparisons ignore, so `==1.0.0+a` matches `1.0.0+b`), and
  `LooseVersion` input such as `1.2.3.4`, which silently means `1.2.3`. It adds
  whitespace around parts, which ansible-galaxy fails on. Each rejected form
  gets an error saying what to write instead.
- **`parse_index` rejects a bad clause up front.** ansible-galaxy checks
  clauses in order and stops at the first that fails, so a bad later clause
  (`>=1.0.0,` has an empty one) only raises for versions that pass the clauses
  before it. alai rejects the whole requirement.
- **A version with an unreadable `deps[].req` or `requires_ansible` is
  skipped**, as if yanked, with a warning naming it. ansible-galaxy can never
  install it, and failing instead would make anything that resolves to it
  uninstallable while an older version might work. The index spec's client
  rules say the same.
- **Pre-releases match ranges** in `contains`, as in `meets_requirements`:
  `>=1.0.0` contains `2.0.0-rc.1`. ansible-galaxy skips pre-releases in its
  resolver unless asked or pinned exactly, and the index spec's client rules
  ask the same, so that's resolver policy too.
- **Collection versions sort by precedence, then text.** SemVer precedence
  ignores build metadata, so `1.0.0+a` and `1.0.0+b` tie; the index spec asks
  clients to break the tie by string so the order is stable. `Version.__eq__`
  compares text to stay consistent with that order. Requirements compare
  precedence alone.
- **Our own PEP 440 code, not `packaging`.** `packaging.specifiers` costs
  ~30 ms to import (it pulls in `packaging.tags`), which would land on every
  command that reads a manifest with `requires_ansible`. The resolver also
  needs to know whether two specifier sets overlap, which `packaging` doesn't
  offer. `_pep440` implements PEP 440 ordering and every specifier operator,
  except local versions (`+local`), which only `==` and `!=` allow and which
  no `requires_ansible` uses.
- **`SpecifierSet.contains` compares the base version**, as ansible-core
  does: it drops its own pre-release, post-release and dev parts before
  checking, so ansible-core 2.19.0rc1 meets `>=2.19`.

## Checked against

Both parsers were compared with the code they follow, using every
`dependencies` value and `requires_ansible` in a sample of 24,157 Galaxy
collection versions, the same strings galaxy-sync writes into the index:

- `parse_index` against `meets_requirements`: 1,083 distinct requirements
  (the corpus plus edge cases), each against 407 versions. All 440,781 results
  agree, apart from the 97 that differ only as described above: alai raises
  where ansible-galaxy returns false.
- `_pep440` against `packaging`: 255 specifier sets against all 359 ansible-core
  releases on PyPI, matching ansible-core's base-version call. All 89,391
  results agree. The one parse difference is `==2.17+local`, by design.
  Version ordering matches `packaging` for every pair of a set of pre, post,
  dev and epoch versions.

What the corpus showed:

- **Caret ranges are in the wild.** About 100 versions, nearly all in the
  `finallycoffee` namespace, declare dependencies like `^3.0.0`. Installing
  one with ansible-core 2.21 fails the whole install, with
  `Non integer values in LooseVersion ('^1.0.0')`, rather than trying another
  version. Two use `=11.4.0`, which ansible-galaxy reads as `==`.
  `alai.toml` rejects `^` and `~` with a hint.
- **`requires_ansible` uses PEP 440 pre-releases**, such as `>=2.19.0b1`
  and `>=2.17.0rc2`, so a SemVer reading of it would be wrong.
- Every collection version in the sample is valid SemVer. 1,594 are
  pre-releases and 40 carry build metadata.

## Open questions

- **Whether galaxy-sync should flag unreadable requirements** in the index,
  so clients needn't each work them out.
- **A shorthand in `alai.toml` requirements.** The base stays
  ansible-galaxy's operators, the same in `alai.toml`, `deps[].req`,
  `galaxy.yml` and `requirements.yml`, so requirements copy between them
  unchanged; `alai export` writes exact versions from the lock, so it doesn't
  constrain this. The common requirement, up to the next major, is verbose
  (`>=9.1.0,<10.0.0`). Candidates:

  | Shorthand | Expands to | From | Against |
  |---|---|---|---|
  | `~=9.1` | `>=9.1.0,<10.0.0` (`~=9.1.0` is `<9.2.0`) | PEP 440 | None strong; `requires_ansible` in the same file already uses it |
  | `^9.1.0` | `>=9.1.0,<10.0.0` | Cargo, npm | Narrows below 1.0 (`^0.2.3` is `<0.3.0`); people already write it in Galaxy metadata, where ansible-galaxy fails on it |
  | `~9.1.0` | `>=9.1.0,<9.2.0` | npm | Means other things in Ruby (`~>`) and Cargo |
  | `9.*` | `>=9.0.0,<10.0.0` | PEP 440 | Reads as a pattern rather than a range |

  Leaning: none for now. If one is added, `~=` first, as a pure expansion
  into existing clauses that errors name; existing operators keep their
  meaning and bare versions stay rejected. `alai add` writing explicit
  bounds from the version it resolves (`community.general>=11.2.0,<12.0.0`)
  removes most of the typing. Sources a string can't express go in the
  planned `[sources]` table, not the requirement.
