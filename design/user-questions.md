# Questions for users

Decisions taken for now that reasonable users could want the other way, and
questions only users can answer. Each says what alai does today, the
alternative, and what to ask; the reasoning is in the note linked. When one
is settled, record it as a decision there and remove it here.

## Collections whose version can't be read

**Today:** a collection with no `MANIFEST.json`, such as a source checkout
with only a `galaxy.yml`, has no version alai reads, so it's replaced
whenever alai installs that collection: `sync` replaces it if the lock
names it and removes it if not, and `collection install` replaces it if
it's asked for or needed by what's asked for. `collection install
--keep-unversioned` leaves it as it is instead, counting it as whatever
version is asked for (2026-10-21).

**The alternatives:**

- ansible-galaxy's behaviour as the default: read `galaxy.yml` when there's
  no `MANIFEST.json`, count a missing version as `*`, and skip what's
  installed unless `--force`.
- Replace only what's named on the command line, and keep, with a warning,
  an unversioned collection that's only a dependency, as that's where work
  in a checkout is lost without anyone asking for it.

**Ask:** do people keep source checkouts inside a project's
`collections/ansible_collections/`, or install into a directory of
checkouts (`~/dev/ansible_collections`) with `--collections-dir`? Did
anyone lose work to a replacement, and would they have found
`--keep-unversioned`? Should `sync` have it too, or should collection
projects ([collection-development.md](collection-development.md)) make
the question go away?

See [install.md](install.md#installing-by-hand) and what uv does there.

## Collections outside the collections directory

**Today:** alai uses only the project's collections directory. `alai find`
counts nothing else as installed, and `alai run`, when it exists, will set
`ANSIBLE_COLLECTIONS_PATH` to that directory alone and turn off
`ANSIBLE_COLLECTIONS_SCAN_SYS_PATH`, so collections in
`~/.ansible/collections`, other configured paths, or the `ansible`
package's `site-packages` aren't loaded (2026-10-21). This keeps the lock
the whole truth about what runs.

**The alternative:** an opt-in to add other paths, such as the `ansible`
package's collections or a shared directory.

**Ask:** does anyone rely on collections from `~/.ansible/collections` or
the `ansible` package alongside a project's? Would they rather add them to
the project, or need them left out of the lock (for example, collections a
platform provides)?

See [README.md](README.md#principles).

## How `alai find` ranks and marks collections

**Today:** with no popularity numbers, `alai find` ranks collections in the
`ansible` package first, then others in their namespaces, and greys out and
ranks last "probable copies": collections at least half of whose plugins
are named as in one package collection (2026-10-21). Removed plugins are
only listed when named in full.

**The alternatives:** rank by something else people trust, such as a
collection being certified or validated by Red Hat, or let a project or
user config list collections to prefer; flag copies some other way, or not
at all; list removed plugins for partial matches too.

**Ask:** does the order put the collection you were after near the top for
the names you search for? Has a collection you use been greyed out as a
copy? Do you look up old names to find where they went, and is listing
removed plugins only for whole names the right default?

See [find.md](find.md#ranking).

## Collections `alai run` doesn't remove

**Today (planned):** `alai run` syncs inexactly, as `uv run` does: a
collection in the directory that `alai.lock` doesn't list stays, with a
warning naming it, and Ansible can load it. `alai run --exact` and `alai
sync` remove it (2026-10-21).

**The alternatives:** exact by default, so only locked collections are ever
loaded; or an error before running, with a flag to run anyway.

**Ask:** do people keep collections they're working on in the project's
collections directory before adding them? Does the warning get read, or do
unlisted collections end up in runs unnoticed?

See [run.md](run.md#what-uv-run-does).

## Where a collection's test tree lives

**Today (planned):** a collection project installs into a
tree in alai's cache, with the checkout linked in, never inside the
checkout; a `collections_dir` inside the checkout is an error
(2026-10-21).

**The alternative:** `./collections` in the checkout, as uv keeps `.venv`
in the project, with a `collections/.gitignore` alai writes.

**Ask:** do people want the tree where they can see it? Would they accept
adding `collections` to `galaxy.yml`'s `build_ignore`, which
`ansible-galaxy collection build` otherwise needs to keep the dependencies
out of the artifact?

See [collection-development.md](collection-development.md#where-the-tree-lives).

## `[project]` in a collection project

**Today (planned):** in a collection project's `alai.toml`, `[project]
collections` and `[project] requires_ansible` are errors, as `galaxy.yml`
and `meta/runtime.yml` already say them (2026-10-21).

**The alternative:** let them override the collection's files, to test
against a different `requires_ansible` or dependency than the collection
publishes.

**Ask:** do people test a collection against requirements other than the
ones it publishes, and how do they do it today?

See [collection-development.md](collection-development.md#decisions).

## Sources for collections the project doesn't list

**Today:** a `[project.sources]` entry applies to any collection in the
resolution, whatever asked for it, for directories as for index pins
(2026-10-22), so a dependency deep in the tree can be pointed at a checkout
of it.

**The alternative:** uv's rule, where `[tool.uv.sources]` only applies to
the project's direct dependencies, and the collection has to be listed in
`project.collections` (or a group) for its source to count.

**Ask:** do people point a dependency they don't list at a checkout, or
pin one to an index, and would listing it be a burden?

See [lockfile.md](lockfile.md#other-sources).

## Editing a copied directory

**Today:** a collection copied from a directory is only copied again when
its version, or the contents of `galaxy.yml` or `meta/runtime.yml`, change,
or with `--reinstall-collection`, as `uv sync` decides for a path
dependency (2026-10-22); editing a plugin needs one of those, or an
editable source. There's no setting like uv's `cache-keys` to add files.

**The alternatives:**

- Copy it again on every `sync` (and `alai run`), as `collection install
  <dir>` does.
- Compare every selected file's hash on each sync: exact, but reads the
  whole collection every time.
- A `cache_keys` setting, as uv's `tool.uv.cache-keys`, in the source
  collection's own `alai.toml`, naming more files to watch.

**Ask:** when people use a plain, not editable, directory source, do they
expect edits to show up, and is the copy on every run worth it? Is there
anything besides `galaxy.yml` and `meta/runtime.yml` whose change should
copy a collection again?

See [install.md](install.md#when-a-copy-is-made-again).

## A tarball that changed

**Today:** a `{ path = "<file>" }` tarball is pinned by its checksum, so
once the file at that path is rebuilt, `alai sync`, `alai lock` and `alai
add` stop with an error until `alai lock -P <name>` locks the new one, as
uv does for an archive (2026-10-22). A `{ url = ... }` tarball isn't
downloaded again once locked, so a change behind the URL only shows when
`sync` downloads it and the checksum fails.

**The alternatives:** treat a changed file as a directory is treated, and
lock again on the next `sync`; or let `alai add <file>` of the same file
lock it again without `-P`, as it names the file explicitly.

**Ask:** do people rebuild a tarball at the same path, say from CI into
`dist/`, and expect `sync` to take the new one? Or is the error the
protection they want against a file swapped under the same name?

See [lockfile.md](lockfile.md#tarballs).

## requirements.yml

**Today:** `-r requirements.yml` is shelved. The mapping onto `alai.toml`
is worked out, and the parser chosen, but these need people who use the
file in anger ([requirements-file.md](requirements-file.md#open-questions)):

- **`source:`** on an entry, naming Automation Hub or a private Galaxy:
  ignore it with a warning, or refuse the file until an index can stand in
  for that server?
- **`signatures:`**: warn, refuse, or wait for signature support in the
  index format?
- **One entry alai can't honour**: refuse the whole file, or install what it
  can and report the rest?
- **`subdirs`** entries, a directory of collections: one path source per
  collection found, or a source kind of their own?
- **Exporting the other way** (`alai export --format requirements.yml`):
  does anyone need the round trip?

**Ask:** who still has a `requirements.yml` as the source of truth, which
of its features they use, and whether `alai add -r` to import it once into
`alai.toml` is enough, or they need `alai collection install -r` to keep
installing from it.
