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
