# Design notes

Why things work the way they do, what's planned, and open questions. Not
published; the user documentation is in [docs/](../docs/).

These notes are the source of truth for how the client behaves: its
configuration, manifest, lockfile and resolution. The index spec, in
galaxy-sync's `docs/index-spec/`, defines the index format and how it's
served; its "Client rules" were a starting point and aren't kept in step, so
where they differ from a decision here (pinning with `[project.sources]`
rather than `<index>:ns.name`, say), the decision here holds.

## Principles

- **Follow uv.** alai is uv for Ansible collections, so a command's
  defaults, flags, wording and output start from what uv does. The notes
  record what was tried against uv (with the version and date), and the
  reason wherever alai differs, such as `-P/--upgrade-collection` rather
  than uv's `--upgrade-package`, since alai says "collection" throughout.
- **Linux and macOS only.** alai doesn't run on Windows, as Ansible's
  control node doesn't, so the code has no Windows paths. macOS has code of
  its own (`clonefile`), so CI tests it too, with the oldest and newest
  Python.

## Roadmap

What comes before `alai run`, which needs these settled first (2026-10-20):

1. **Path, editable and git sources** in `[project.sources]`, likely as
   local paths (plain and editable), then git, then tarball URLs and
   files: [lockfile.md](lockfile.md#planned-other-sources) and
   [install.md](install.md).
2. **Collection projects**: `galaxy.yml` as the manifest, installing into a
   tree alai owns with the checkout linked in:
   [collection-development.md](collection-development.md).
3. **`alai run`**, and with it the ansible-core check against the locked
   collections' `requires_ansible` ([environments.md](environments.md)).

Shelved: `-r requirements.yml` ([requirements-file.md](requirements-file.md));
what to ask users about it is in [user-questions.md](user-questions.md).
Smaller items wait in each note's "Not done yet".
`docs/roadmap.md` is the published summary of this and the larger of
those items; change it with them.

| Note | |
|---|---|
| [configuration.md](configuration.md) | Settings and project discovery decisions, the project manifest, planned error positions, and what isn't read yet |
| [versions.md](versions.md) | Collection and ansible-core versions and requirement syntax, and how they were checked against ansible-core |
| [environments.md](environments.md) | `requires_ansible` as a range versus an ansible-core pin, several environments per project, and how one lock covers a range: future work |
| [resolver.md](resolver.md) | Choosing one version of every collection: resolvelib, where each collection comes from, which versions are candidates, `requires_ansible`, and failures |
| [manifest-editing.md](manifest-editing.md) | `alai add` and `alai remove`, and the surgical, checked editor they change `alai.toml` with |
| [requirements-file.md](requirements-file.md) | Reading ansible-galaxy's `requirements.yml` for `-r`: its format, what uv does with sources in `requirements.txt`, the mapping proposed, and open questions. Shelved |
| [install.md](install.md) | Installing locked collections: downloading artifacts, the extracted-collection cache, extraction as ansible-galaxy does it, what counts as installed, link modes and exact syncing |
| [index-client.md](index-client.md) | Fetching and caching the index: the HTTP library, cache layout, revalidation, proxies, errors, and what `index.alai.sh` does |
| [performance.md](performance.md) | Where the time goes, measured on `sivel.acd`: extraction, cloning, and the planned Rust extension and how it's packaged |
| [output.md](output.md) | Verbosity, what goes to stdout and stderr, JSON, colour and progress |
| [documentation.md](documentation.md) | What the published docs cover, the generator, the generated CLI reference and its terminal recordings, and how they're deployed |
| [collection-development.md](collection-development.md) | Collection source repos and `ansible-test`: future work, and what today's code must keep possible |
| [user-questions.md](user-questions.md) | Decisions taken for now that users may want the other way, and questions only users can answer: what to ask, and where the reasoning is |
