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
- **Only the collections directory alai manages** (2026-10-21). alai
  works with the project's `collections_dir` and nothing else Ansible
  could load collections from: not `~/.ansible/collections`, other
  `ANSIBLE_COLLECTIONS_PATH` entries, or the `ansible` package's
  collections in `site-packages`. Anything outside it would let collections
  the lock doesn't list into a run, so the lock would stop describing what
  runs. `alai run` will point Ansible at that directory alone
  (`ANSIBLE_COLLECTIONS_PATH` set to it and
  `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False`); widening it would be an
  explicit opt-in later, never the default. `alai find` counts only it as
  installed, with `ansible.builtin`.
- **Linux and macOS only.** alai doesn't run on Windows, as Ansible's
  control node doesn't, so the code has no Windows paths. macOS has code of
  its own (`clonefile`), so CI tests it too, with the oldest and newest
  Python. Other Unix systems, such as the BSDs, aren't tested, but nothing
  stops a build from the sdist; they have no clone, so `auto` hardlinks.

## Roadmap

Next (2026-10-22):

1. **`alai build`, and the collection defined in `alai.toml`**, before the
   first release (raised 2026-10-21): `alai add` and `alai remove` can't
   edit `galaxy.yml` without a YAML editor that keeps its formatting, so a
   collection whose metadata lives in `alai.toml` is how they'll work in a
   collection project. The open questions are in
   [collection-development.md](collection-development.md#open-questions).
2. **Third-party licence notices in the wheels**, before the first release
   of alai or alai-core (raised 2026-10-29). The Rust is linked into each
   wheel, and nearly every crate's licence (MIT, BSD, ISC, Apache-2.0,
   Unicode) asks for its notice to go with binary copies, but a wheel holds
   only alai's own `LICENSE`. Generate a notices file from each repository's
   `Cargo.lock`, `cargo-about` being the usual tool (a dev tool to approve
   then), ship it in the wheel's `licenses/`, and check in CI that it's
   current. Every licence was checked on 2026-10-29 as compatible with MIT;
   the MPL-2.0 crates (`pubgrub`, `version-ranges`, and `priority-queue`,
   taken under MPL) also need their source to stay available, which
   crates.io gives, and any patch to their files is MPL too.

Done on 2026-10-21: `alai run` and `alai env` ([run.md](run.md)), and
collection projects
([collection-development.md](collection-development.md)); on 2026-10-22,
path sources, plain and editable, tarballs by path or URL, and git
repositories ([lockfile.md](lockfile.md#other-sources),
[install.md](install.md#git-sources)), `alai collection list`
([list.md](list.md)), and extracting and installing with a native
extension, in Rust and required
([performance.md](performance.md#the-native-extension)); on 2026-10-23, the
resolver moved from resolvelib to PubGrub in that extension
([resolver.md](resolver.md)), with what's left of those in each note's
"Not done yet"; on 2026-10-27, HTTP in the extension, with reqwest over
HTTP/2 and the OS verifying certificates
([index-client.md](index-client.md#decisions),
[performance.md](performance.md#http-in-the-extension)).

Moved out: plugin documentation, `alai doc`, built here first and kept as
a project of its own for later in `../alai-doc` (2026-10-25); what alai
would give it is in [find.md](find.md#not-done-yet).

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
| [resolver.md](resolver.md) | Choosing one version of every collection: PubGrub in the native extension, where each collection comes from, which versions are candidates, `requires_ansible`, failures explained in uv's words, and the measurements against resolvelib that decided it |
| [manifest-editing.md](manifest-editing.md) | `alai add` and `alai remove`, and the checked `toml_edit` editor they change `alai.toml` with, laying arrays out as uv does |
| [requirements-file.md](requirements-file.md) | Reading ansible-galaxy's `requirements.yml` for `-r`: its format, what uv does with sources in `requirements.txt`, the mapping proposed, and open questions. Shelved |
| [install.md](install.md) | Installing locked collections: downloading artifacts, the extracted-collection cache, extraction as ansible-galaxy does it, what counts as installed, link modes and exact syncing |
| [index-client.md](index-client.md) | Fetching and caching the index: the extension's HTTP client and HTTP/2, TLS verification, cache layout, revalidation, proxies, errors, and what `index.alai.sh` does |
| [performance.md](performance.md) | Where the time goes, measured on `sivel.acd`: extraction, cloning, HTTP/2, the Rust extension and how alai ships with it |
| [list.md](list.md) | `alai collection list`: what uv lists and where, comparing the directory with `alai.lock`, and the output |
| [find.md](find.md) | `alai find`: searching the plugin index, ranking without download counts, probable copies, following routing as ansible-core does, and the output |
| [output.md](output.md) | Verbosity, what goes to stdout and stderr, JSON, colour and progress |
| [documentation.md](documentation.md) | What the published docs cover, the generator, the generated CLI reference and its terminal recordings, and how they're deployed |
| [run.md](run.md) | `alai run` and `alai env`: bringing your own Python environment, environment variables only, syncing first, and what's open |
| [collection-development.md](collection-development.md) | Collection projects and `ansible-test`: future work, and what today's code must keep possible |
| [upstream.md](upstream.md) | Changes needed in ansible-test and ansible-core, mostly for collection projects: where, why, what alai does meanwhile and what to remove once each is released |
| [user-questions.md](user-questions.md) | Decisions taken for now that users may want the other way, and questions only users can answer: what to ask, and where the reasoning is |
