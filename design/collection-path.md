# `alai collection path`

Where a collection's files are on disk, for tools that read them. It exists
for alai-doc, a plugin documentation viewer that's its own project: it
reads docs with alai-core's extractor, and calls alai through the CLI for
everything that needs alai's settings, project, lock and cache, rather than
importing alai's private modules.

## Decisions

- **Found as a play would run it** (2026-10-30): the collection project's
  own checkout (from its `galaxy.yml`), else installed in the collections
  directory, else the version `alai.lock` has (from the cache, fetched as
  `alai sync` fetches it if it isn't there; git and local sources are
  skipped, as they're installed when locked), else the newest version in
  the artifact cache, else the latest usable version on the first index
  with the collection, downloaded into the cache. Each result's `origin`
  says which: `project`, `installed`, `locked`, `cached` or `downloaded`.
- **`ansible.builtin` and `eda.builtin` are the latest ansible-core and
  ansible-rulebook** on `python_index`, the same releases `alai find`
  reads ([find.md](find.md#ansiblebuiltin-and-edabuiltin)). The sdist's
  extracted source is kept at `<cache_dir>/python-index-v1/<host>-<hash>/<project>/<version>/`
  beside its lines, for alai-core's `extract_ansible_core` and
  `extract_ansible_rulebook`, so either command's download serves both.
  Offline or unreachable, the newest cached source, with a warning.
- **`latest` only for a cached collection**: the one origin where the
  version is alai's guess rather than the project's choice, so the only one
  a viewer should say is out of date. It costs an index request.
- **`--no-download`** downloads nothing and checks no index: a collection
  not on disk is `null`. alai-doc uses it to check a name against
  `alai find` before downloading a whole collection for a typo.
- **Several names in one call**, as a page needs its doc fragments'
  collections too, and each call pays alai's start-up. One that can't be
  had doesn't fail the rest: in JSON it's `{"error": "..."}`, and the exit
  code is 2; one that isn't found is `null`, exit code 1.
- **JSON can leave NAME out**, giving only `project`, the collection
  project's own collection or `null`: alai-doc needs it to find a short
  name as a play does, and only alai finds the project. Text needs a NAME,
  and prints each path on a line.

## Output

```json
{
  "v": 1,
  "project": "ns.coll",
  "collections": {
    "community.general": {"version": "10.7.3", "path": "/…", "origin": "cached", "latest": "13.5.0"},
    "nope.nope": null
  }
}
```

## Not done yet

- uv has nothing like it to follow; `uv python find` (a path on stdout) is
  the closest, and the text output matches it.
- It isn't in `docs/` yet: it's for tools, and its only user isn't
  released.
