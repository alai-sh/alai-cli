# requirements.yml

Reading ansible-galaxy's `requirements.yml`: `alai collection install -r`
to install one by hand, and `alai add -r` to import one into `alai.toml`,
as `uv pip install -r` and `uv add -r` take a `requirements.txt`.

**Shelved, 2026-10-20.** The mapping below is worked out, but what the
file's `source:` and `signatures:` should mean to alai needs input from
people using Automation Hub and signed collections, and its git, path and
URL entries need the sources that aren't built yet
([lockfile.md](lockfile.md#other-sources); directories exist now). Decided so far:
`alai_core.yaml` is the parser, as for `galaxy.yml`
([performance.md](performance.md#yaml-in-the-extension)), both commands take `-r`, and roles are warned about and
skipped.

## The format, as ansible-galaxy reads it

From `_parse_requirements_file` and `_init_coll_req_dict` in
`ansible/cli/galaxy.py`, and `Requirement.from_requirement_dict` in
`ansible/galaxy/dependency_resolution/dataclasses.py`:

- **The top level** is a mapping with only `collections` and `roles`;
  another key is an error ("Expecting only 'roles' and/or 'collections' as
  base keys"). A list at the top level is the older, roles-only format. An
  empty file is "No requirements found".
- **A collection entry** is a string or a mapping. A string is taken as
  `{name: <string>}`.
- **What an entry's keys mean depends on its `type`**, which is what makes
  the format confusing:

  | Key | `galaxy` (the default for an FQCN) | `git` | `dir`, `file`, `url`, `subdirs` |
  |---|---|---|---|
  | `name` | the collection, `ns.name` | the repository URL | a path or URL |
  | `version` | a version spec: `*` by default, clauses separated by `,`, a bare version meaning exactly it | a branch, tag or commit | unused |
  | `source` | a Galaxy server: a URL, or a name from `ansible.cfg`'s `GALAXY_SERVER_LIST` | (an older form put the URL here) | (the same) |
  | `signatures` | signature sources to verify the collection with, needing a keyring | | |

  With no `type`, it's `galaxy` when `name` is a valid FQCN, else inferred
  from `name` (or `source`) looking like a git URL, an HTTP URL, a
  tarball or a directory.
- **`roles:` entries** take `src`, `name`, `scm`, `version`, and `include`
  for another file of roles.

## What uv does

Tested with uv 0.13 on 2026-10-20, with a `requirements.txt` holding
`--index-url https://pypi.org/simple`, `idna==3.10`,
`six @ git+https://github.com/benjaminp/six@1.16.0` and `-e ./lib`:

- **`uv add -r` splits what from where.** `dependencies` got the names and
  specs only (`"idna==3.10"`, `"six"`, `"mylib"`), and the locations went to
  `[tool.uv.sources]`: `six = { git = "https://github.com/benjaminp/six",
  rev = "1.16.0" }` and `mylib = { path = "../lib", editable = true }`. The
  lock pinned the git commit (`six==1.16.0 (from
  git+…@65486e4…)`).
- **The file's `--index-url` wasn't kept** in `pyproject.toml`; it applied
  to that resolution only.
- **`uv pip install -r` resolves a relative path against the current
  directory**, not the file's (pip's rule): `-e ./lib` failed run from
  elsewhere with "Distribution not found at: file://…/pipenv/lib".

## The mapping proposed

alai's model is uv's: a requirement is a name and a version spec in
`collections`, and where it comes from is a separate `[project.sources]`
entry. Importing splits each entry into those two, which leaves the
file's ambiguity behind rather than carrying it into `alai.toml`.

- **Galaxy entries**: `name` and `version` become a requirement, with
  ansible-galaxy's meaning: a bare `1.0.0` is written `==1.0.0`, since
  `alai.toml` rejects a bare version ([versions.md](versions.md)), and `*` is
  no spec at all.
- **`source:` on a Galaxy entry**: Galaxy itself
  (`https://galaxy.ansible.com`, or the default `galaxy` server name)
  dropped silently, as alai's default index mirrors it; anything else
  warned about, since alai doesn't speak the Galaxy API: the collection
  comes from the configured indexes, which may not have it (a private
  Automation Hub's certified content, say).
- **`signatures:`**: warned that they aren't verified; alai checks each
  artifact's `cksum` against the index.
- **`git`, `dir`, `file`, `url`, `subdirs` entries**: a name in
  `collections` and a `[project.sources]` entry, as uv writes them, once
  those sources exist; until then an error naming the entry. A git entry's
  `name` is its URL, so the collection's own name is only known once its
  `galaxy.yml` has been read.
- **`roles:`**, and the roles-only list format: "skipping 3 roles: alai
  installs collections only"; a file with nothing else also gets "no
  collections to install".
- **Relative paths** against the file's own directory, as ansible-galaxy
  takes `include`, not pip's current directory.

## Open questions

- **`source:`**: is ignoring a non-Galaxy server, with a warning, what users
  of Automation Hub or a private Galaxy want, or should such a file be
  refused until an index can stand in for that server (a
  `[project.sources]` index entry per server, say)?
- **`signatures:`**: warn, refuse, or wait for signature support in the
  index format (the index spec leaves signatures for later)?
- **Strict or forgiving**: refuse a whole file over one entry alai can't
  honour, or install what it can and report the rest?
- **`subdirs`** (a namespace directory of collections) has no uv
  equivalent: one `[project.sources]` path entry per collection found, or
  a source kind of its own?
- **Export the other way**, `alai export --format requirements.yml`
  ([configuration.md](configuration.md)), should round-trip with this.
