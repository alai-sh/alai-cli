# Coming from ansible-galaxy

alai replaces `ansible-galaxy collection install` for a project's
collections. It installs the same collections, extracted the same way, so
Ansible loads them as it would any others. What changes is how a project
says what it needs, where collections go, and how they're kept in step.
This page covers moving a project over, then the differences you may run
into.

## Moving a project over

1. **Create an `alai.toml`** in the directory holding your playbooks. It
   marks the project's root, and can start empty:

    ```console
    $ touch alai.toml
    ```

2. **Add the collections from your `requirements.yml`**, each with its
   version requirement, translated as in
   [the table below](#requirementsyml-to-alaitoml):

    ```console
    $ alai add 'community.general>=10,<12' ansible.posix 'cisco.ios==11.6.0'
    ```

    This writes them into `alai.toml`, resolves them into `alai.lock` and
    installs them into `collections/ansible_collections/` in the project.

3. **Check Ansible finds them.** Ansible looks in a `collections/` directory
   next to the playbook it runs, so with playbooks at the project root
   there's nothing to do. With playbooks in a subdirectory, set
   [`collections_dir`](alai-toml.md#projectcollections_dir) to install next
   to them, or point `collections_path` in your `ansible.cfg` at alai's
   directory. See [Where collections are installed](projects.md#where-collections-are-installed).

4. **Commit `alai.toml` and `alai.lock`**, ignore `collections/`, and
   replace `ansible-galaxy collection install -r requirements.yml` in CI
   and docs with `alai sync --locked` (see
   [Recommended workflows](managing-collections.md#recommended-workflows)).

5. **Keep `requirements.yml` for roles**, if it lists any: alai only
   installs collections. Its `collections:` entries can go.

## Commands

| ansible-galaxy | alai |
|---|---|
| `ansible-galaxy collection install -r requirements.yml` | `alai sync`, from `alai.toml` and `alai.lock` |
| `ansible-galaxy collection install NAME`, then adding it to `requirements.yml` | `alai add NAME` |
| `ansible-galaxy collection install NAME -p DIR`, for a one-off | `alai collection install NAME`, with `--collections-dir DIR` outside a project ([Installing by hand](installing-by-hand.md)) |
| `ansible-galaxy collection install --upgrade NAME` | `alai sync -P NAME`, or `alai lock -P NAME` to only update the lock |
| `ansible-galaxy collection install --force NAME` | Delete the collection's directory, then `alai sync`: `sync` leaves a collection at its locked version as it is, edited or not, so it only reinstalls one that's missing |
| `ansible-galaxy collection install --pre NAME` | Nothing extra: a pre-release is chosen when no stable version fits (see [Pre-releases](#pre-releases)) |
| Deleting a collection's directory | `alai remove NAME`, or `alai collection uninstall NAME` |
| `ansible-galaxy role install` | Not handled: keep using ansible-galaxy for roles |

## `requirements.yml` to `alai.toml`

alai can't read a `requirements.yml` yet, so its collections are moved by
hand, with `alai add` or by editing `alai.toml`. Each entry becomes one
[requirement](alai-toml.md#requirements), the name followed by its
version:

| `requirements.yml` | `alai.toml` |
|---|---|
| `- community.general`, or `version: "*"` | `"community.general"` |
| `version: ">=10.0.0,<12.0.0"` | `"community.general>=10.0.0,<12.0.0"` |
| `version: "10.7.9"`, a bare version meaning exactly it | `"community.general==10.7.9"`: alai needs the `==` |
| `version: "!=10.7.0"` | `"community.general!=10.7.0"` |
| `source:` naming a Galaxy server | No equivalent; see [Indexes, not Galaxy servers](#indexes-not-galaxy-servers) |
| `type: git`, `url`, `file` or `dir` | Not supported yet |
| `signatures:` | Not supported |

```yaml
# requirements.yml
collections:
  - name: community.general
    version: ">=10.0.0,<12.0.0"
  - name: cisco.ios
    version: 11.6.0
  - ansible.posix
```

```toml
# alai.toml
[project]
collections = [
    "ansible.posix",
    "cisco.ios==11.6.0",
    "community.general>=10.0.0,<12.0.0",
]
```

## Differences

### There's a lock file

ansible-galaxy installs whatever versions fit when it runs, so two runs a
month apart can install different collections. alai resolves once, into
`alai.lock`, and installs from that until you change `alai.toml` or ask to
upgrade, so every machine and CI run gets the same versions. Upgrading is
explicit: `-U` for everything, `-P NAME` for one collection.

### Collections live in the project

ansible-galaxy installs into the first entry of Ansible's collections path,
`~/.ansible/collections` by default, shared by every project on the
machine. alai installs into the project, in
`collections/ansible_collections/`, so each project has its own versions.

Collections installed elsewhere still load: Ansible searches the
playbook's `collections/` first, then its configured collections path. A
collection the project forgot to list can work on your machine, from
`~/.ansible/collections`, and fail elsewhere. Run it in CI with only the
project's collections, or clear out the shared path, to catch that.

### `sync` removes what the lock doesn't list

`alai sync` makes the collections directory match `alai.lock`, so
collections installed there some other way, with ansible-galaxy or
`alai collection install`, are removed. Use `alai add` for anything the
project needs, `--inexact` to keep the extras for one run, or a different
directory for collections managed by other tools.

### Versions already installed are kept

ansible-galaxy skips a collection that's already installed at a version
that fits, and reinstalls with `--force`. alai does the same by version:
a collection at its locked version is left as it is, even if its files were
edited. There's no `--force`; delete the collection's directory and run
`alai sync` to get a clean copy.

### Collections without a `MANIFEST.json`

ansible-galaxy treats a directory with only a `galaxy.yml`, such as a source
checkout, as installed at the version it gives, or any version if it gives
none, and leaves it alone. alai only reads `MANIFEST.json`, so it replaces
such a directory when it installs that collection, by `sync` or by hand.
`alai collection install --keep-unversioned` keeps it, as ansible-galaxy
does (see [Installing by hand](installing-by-hand.md#collections-whose-version-cant-be-read)).

### Pre-releases

ansible-galaxy ignores pre-releases unless given `--pre`, pinned exactly, or
no stable version exists. alai chooses a pre-release whenever no stable
version fits: with `2.5.0` and `3.0.0-rc.1` released, `>2.5.0` gives the
release candidate, where ansible-galaxy fails without `--pre`. The version
chosen is listed when locking, so a pre-release shows there.

### `requires_ansible`

ansible-galaxy ignores a collection's `requires_ansible` when installing;
Ansible warns when it loads one that doesn't support the running version.
With [`requires_ansible`](alai-toml.md#projectrequires_ansible) set in
`alai.toml`, alai doesn't choose a version whose own `requires_ansible`
starts later than the project's, and warns about one whose range ends inside
the project's.

### Version requirements are stricter

`alai.toml` takes ansible-galaxy's requirement syntax, minus forms that
read as something else: a bare version (`1.2.3`) or `=1.2.3` needs
`==1.2.3`, and `^` and `~`, which ansible-galaxy fails on, need writing as
ranges. Spaces around each part are allowed. Collections' own dependencies
are read exactly as ansible-galaxy reads them. See
[Version requirements](alai-toml.md#version-requirements).

### Indexes, not Galaxy servers

alai reads collections from an index, by default `index.alai.sh`, a static
mirror of Galaxy, rather than from the Galaxy API. Galaxy servers set with
`--server`, `source:` or `ansible.cfg`'s `[galaxy]` settings aren't used,
and Automation Hub and private Galaxy servers can't be installed from.

### `ansible.cfg` isn't read

alai uses an `ansible.cfg` only to find the project's root. Its settings,
such as `collections_path` or Galaxy servers, aren't read: alai's settings
come from [`alai.toml`](alai-toml.md), environment variables and flags.
If your `ansible.cfg` sets `collections_path` to somewhere other than
`collections/`, set alai's
[`collections_dir`](alai-toml.md#projectcollections_dir) to the same place.

### Not supported yet

- Roles.
- Collections from git, a directory, a URL or a tarball.
- Verifying signatures.
- Reading `requirements.yml`.
