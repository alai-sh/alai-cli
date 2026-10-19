# Design notes

Why things work the way they do, what's planned, and open questions. Not
published; the user documentation is in [docs/](../docs/).

| Note | |
|---|---|
| [configuration.md](configuration.md) | Settings and project discovery decisions, the project manifest, planned error positions, and what isn't read yet |
| [versions.md](versions.md) | Collection and ansible-core versions and requirement syntax, and how they were checked against ansible-core |
| [environments.md](environments.md) | `requires_ansible` as a range versus an ansible-core pin, several environments per project, and how one lock covers a range: future work |
| [index-client.md](index-client.md) | Fetching and caching the index: the HTTP library, cache layout, revalidation, proxies, errors, and what `index.alai.sh` does |
| [output.md](output.md) | Verbosity, what goes to stdout and stderr, JSON, colour and progress |
| [documentation.md](documentation.md) | What the published docs cover, the generator, and how they're deployed |
| [collection-development.md](collection-development.md) | Collection source repos and `ansible-test`: future work, and what today's code must keep possible |
