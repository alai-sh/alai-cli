# Design notes

Why things work the way they do, what's planned, and open questions. Not
published; the user documentation is in [docs/](../docs/).

These notes are the source of truth for how the client behaves: its
configuration, manifest, lockfile and resolution. The index spec, in
galaxy-sync's `docs/index-spec/`, defines the index format and how it's
served; its "Client rules" were a starting point and aren't kept in step, so
where they differ from a decision here (pinning with `[project.sources]`
rather than `<index>:ns.name`, say), the decision here holds.

| Note | |
|---|---|
| [configuration.md](configuration.md) | Settings and project discovery decisions, the project manifest, planned error positions, and what isn't read yet |
| [versions.md](versions.md) | Collection and ansible-core versions and requirement syntax, and how they were checked against ansible-core |
| [environments.md](environments.md) | `requires_ansible` as a range versus an ansible-core pin, several environments per project, and how one lock covers a range: future work |
| [resolver.md](resolver.md) | Choosing one version of every collection: resolvelib, where each collection comes from, which versions are candidates, `requires_ansible`, and failures |
| [manifest-editing.md](manifest-editing.md) | `alai add` and `alai remove`, and the surgical, checked editor they change `alai.toml` with |
| [install.md](install.md) | Installing locked collections: downloading artifacts, the extracted-collection cache, extraction as ansible-galaxy does it, what counts as installed, link modes and exact syncing |
| [index-client.md](index-client.md) | Fetching and caching the index: the HTTP library, cache layout, revalidation, proxies, errors, and what `index.alai.sh` does |
| [performance.md](performance.md) | Where the time goes, measured on `sivel.acd`: extraction, cloning, and the planned Rust extension and how it's packaged |
| [output.md](output.md) | Verbosity, what goes to stdout and stderr, JSON, colour and progress |
| [documentation.md](documentation.md) | What the published docs cover, the generator, and how they're deployed |
| [collection-development.md](collection-development.md) | Collection source repos and `ansible-test`: future work, and what today's code must keep possible |
