# Finding plugins

`alai find` answers "which collection has this?" for a module, plugin,
role or playbook, and "where did it go?" for one that was moved or removed.
It searches a list of every plugin in every collection on the index, so it
finds collections you haven't installed, and lists the ones you have first.

[Finding plugins](casts/find.cast){ .alai-cast }

!!! note "Index-only plugins"

    alai will only find collections present in the index and does not scan
    collections stored locally. A future change will implement support for
    finding plugins that are stored locally and not present in the index.

## What to search for

| Query | Finds | Example |
|---|---|---|
| A word | Every plugin whose name contains it, exact names first | `alai find user` finds `user`, `win_user` and `user_info` |
| A glob, with `*`, `?` or `[...]` | Short names that match it, or with `namespace.collection.*` the plugins in that collection | `alai find '*_info'`, `alai find 'community.general.*'` |
| A full name (FQCN) | That plugin's [details](#a-plugins-details) | `alai find community.general.docker_container` |

A glob is matched against full names once it has a namespace and a
collection in front, and against short names otherwise. So
`'mgdis.ovh.*'` finds every plugin in `mgdis.ovh`, including ones in
subdirectories such as `mgdis.ovh.cloud.block_storage.block_storage`. To
search every collection in a namespace, give both parts, as in
`alai find 'community.*.*'`. `'community.*'` on its own matches short
names, so it finds only plugins in a `community/` subdirectory. That's
also how to search a subdirectory, as in `alai find 'cloud.*'`.

`-x/--exact` matches whole names only, so `alai find -x user` leaves out
`win_user`. `-t/--type` limits the search to one type, using the names
`ansible-doc -t` takes (`module`, `filter`, `lookup`, `callback`…), plus
`role` and `playbook`.

When nothing matches, alai suggests similar names and exits with code 1.
For a full name it suggests full names, such as
`ansible.windows.win_disk_info` for `ansible.legacy.win_disk_info`.

### Quoting globs

Put a glob in single quotes, as in `alai find '*_info'` rather than
`alai find *_info`. Without quotes, `*`, `?` and `[...]` will be expanded by the shell before alai
sees them. Single quotes keep everything as typed. A word or a full name with
no glob characters does not need to be quoted.

Here `docker_container_info` is a file in the current directory, so the
unquoted `*_info` becomes `docker_container_info` before alai runs, and
alai searches for that one name:

[Why globs need quotes](casts/find-quoting.cast){ .alai-cast }

## Examples

Search by part of a name, or the whole name with `-x`, or only what's
installed with `--installed`:

[Searching by part of a name, or the whole name](casts/find-search.cast){ .alai-cast }

Narrow it to a type, or several:

[Searching one type, or several](casts/find-types.cast){ .alai-cast }

Globs, quoted, match short names, or full names when they have dots:

[Searching with globs](casts/find-globs.cast){ .alai-cast }

Give a full name, and `alai find` shows that plugin's details, such as
whether it's installed, where it redirects, and more.

[An old name that no longer leads anywhere, and an ansible.legacy name](casts/find-redirects.cast){ .alai-cast }

You can control the color and output format to make it more friendly when
piping to other tools like [jq](https://jqlang.org/):

```console
$ alai find user --color always | less -R
$ alai find user --output-format json | jq -r '.plugins[] | select(.matched) | .fqcn'
$ alai find -x ipmath --output-format json \
    | jq -r '.collections | to_entries[] | select(.value.installed) | .key'
```

Piped, alai lists every match rather than the first 20, and
`--color always` can be used to force colors when piping.

## What's listed first

Matches are in two sections. **Installed** has the collections in the
project's collections directory, plus `ansible.builtin` and `eda.builtin`,
which come with ansible-core and ansible-rulebook. **Not installed** has
everything else on the index. Within each section, matches are listed in
this order:

1. Exact names before partial matches.
2. Plugins that aren't redirects, then redirects, then ones that are
   removed or lead nowhere.
3. `ansible.builtin` and `eda.builtin`.
4. Collections present in alai's cache, for example one installed in a
   separate project.
5. Collections in the [`ansible`](https://pypi.org/project/ansible/)
   package. This is a package containing multiple pre-vetted collections by
   the Ansible community.
6. Other collections in the same namespaces as the `ansible` package's
   collections.
7. Everything else, with collections that look like copies of a
   collection in the `ansible` package last. Their names are gray, and a
   plugin's details say which collection it seems to copy. That's a guess,
   from at least half their plugins having the same names, so it's only
   used to rank them lower, not to hide them.
8. Modules before other types, then by collection and name.

This order tries to provide a priority based listing to show more relevant
entries first before the remaining matches.

On a terminal, only the first 20 matches are listed, installed ones
first. Use `--limit N` to change the limit and `--limit 0` to remove the
limit entirely and list them all. When the output goes to a pipe or a file,
such as `alai find user | less` or `| grep`, there is no limit unless `--limit`
is explicitly provided. The `--installed` argument lists only the plugins
that are installed.

Plugins that have been removed or are just an alias/redirection to another
plugin are not listed by default unless it is an alias to a plugin with a
different name. Use `-a`/`--all` to list removed plugins and aliases for
partial matches too.

## What the markers mean

Each line starts with a marker:

| Marker | Means |
|---|---|
| `✓`{ .ansi-green } | In an installed collection |
| `·`{ .ansi-dim } | In a collection that isn't installed |
| `↑`{ .ansi-yellow } | Added in a newer version of the collection than the one installed. Upgrade it to use it |
| `↪`{ .ansi-cyan } | Redirects to the plugin shown after `→`. The name still works, as Ansible loads that plugin instead |
| `↪`{ .ansi-yellow } | Installed, but redirects through a collection that isn't, so Ansible can't load it until that's installed too |
| `⚠`{ .ansi-yellow } | Deprecated, or redirects through a deprecated name. Its details say when it's due to be removed, and usually what to use instead |
| `✗`{ .ansi-red } | Removed, or its redirect leads nowhere Ansible can load |

When more than one applies, the marker shows the one that matters most
for using the plugin, in this order:

1. `✗` means you can't use it.
2. `⚠` means you shouldn't use it.
3. `↑` or a yellow `↪` means you can use it once something is upgraded or
   installed.
4. A cyan `↪` means you can use it under another name.
5. `✓` or `·` means you can use it.

In place of the description, a line that isn't simply usable says why that is
the case. For example a redirect shows the plugin it ends at, with anything
that stands in the way in brackets:

```
↪ module    ansible.builtin.nmcli               → community.general.nmcli
↪ module    ansible.builtin.docker_container    → community.docker.docker_container (not installed)
⚠ module    awx.awx.tower_project               → awx.awx.project (deprecated)
↑ module    ansible.builtin.disk_info           → community.general.disk_info (needs 3.0.0, local 2.5.0)
✗ module    ansible.builtin.win_domain_user     → community.windows.win_domain_user (removed)
```

Otherwise the description is a short reason why it is not a normal target,
such as `needs 3.9.1 (local 2.8.0)`, `deprecated`, `removed in 13.0.0` or
`redirect loop`. The "Local" part here refers to the version installed in
the project. A plugin's [details](#a-plugins-details) say when it moved or
was renamed, and show each step of a redirect.

`alai find` renders
[Ansible's documentation markup](https://docs.ansible.com/projects/ansible/latest/dev_guide/ansible_markup.html)
as best it can. For example, `C(lxc)` is shown in cyan, and `U()` as a link.

!!! note "It depends on your terminal"

    How much of this you see depends on the terminal. Italics, and links
    (OSC 8 hyperlinks), aren't supported everywhere. A terminal that
    doesn't support one shows the plain text instead. Some terminals only
    mark a link while you hold Ctrl (Cmd on macOS) and hover over it, as
    Ghostty does. Inside tmux, links only reach the terminal when tmux is
    told it supports them, with `set -as terminal-features
    ",xterm-ghostty:hyperlinks"` (using your terminal's `TERM`, or `*`)
    in its config, and you detach and reattach afterwards.

## A plugin's details

Searching for a full name shows the details of that plugin, such as:

- **Installed**: Whether the plugin is installed for the project. For
  `ansible.builtin` and `eda.builtin` it means Ansible sees the plugin
  wherever ansible-core or ansible-rulebook is. alai doesn't check that
  either is in your Python environment, and the line says so.
- **Versions**: The versions the plugin is present in.
- **Removal**: When a deprecated plugin is planned to be removed, if its
  collection provides the version or date.
- **Route**: The plugin, then each name it redirects to. The plugin it
  ends at has its description under it. A step's deprecation or removal
  message from its collection is shown under that step, as Ansible warns
  at every step it passes.
- **Hint**: Extra information for how to install/use the plugin.

[A plugin's details and where its redirects lead](casts/find-detail.cast){ .alai-cast }

A redirect can lead to another that redirects again. alai follows and displays
the whole redirection chain so it is clear how Ansible resolves the plugin
name.

## For scripts

With `--output-format json`, the output holds the facts alai knows about
each plugin, rather than the human-friendly conclusions the text output
shows. It is up to the caller to interpret and present the information in
whatever format it wishes. It has no limit unless `--limit` is given. The
following is the JSON output of
`alai find ansible.builtin.docker_container --output-format json`:

```json
{
  "v": 1,
  "plugins": [
    {
      "type": "module",
      "name": "docker_container",
      "collection": "ansible.builtin",
      "routing": {
        "redirect": "community.docker.docker_container"
      },
      "fqcn": "ansible.builtin.docker_container",
      "index": "ansible-core 2.21.5",
      "matched": true
    },
    {
      "type": "module",
      "name": "docker_container",
      "collection": "community.docker",
      "since": "0.1.0",
      "short_description": "manage Docker containers",
      "fqcn": "community.docker.docker_container",
      "index": "alai",
      "matched": false
    }
  ],
  "collections": {
    "ansible.builtin": {
      "installed": true,
      "version": null,
      "path": null
    },
    "community.docker": {
      "installed": true,
      "version": "5.4.0",
      "path": "/home/me/project/collections/ansible_collections/community/docker"
    }
  },
  "omitted": 0
}
```

| Field | |
|---|---|
| `v` | The format's version, which changes if a field is removed or changes meaning |
| `plugins` | The same matches the text output lists, in the same order, then every plugin their redirects lead to that the index has, each listed once. Like the text output, it leaves out removed plugins and aliases unless they're named exactly or `-a`/`--all` is given |
| `collections` | Every collection the plugins are in or redirect to, by name, with whether it's [installed](#whats-listed-first) and, if so, its `version` and the `path` of its directory. `ansible.builtin` and `eda.builtin` are always installed, with no version or path. `version` is `null` if it can't be read |
| `omitted` | How many matches `--limit` left out |

Each plugin has:

| Field | |
|---|---|
| `type` | Its type, as `-t` takes it, such as `module`, `filter` or `role` |
| `name` | Its short name |
| `collection` | The collection it's in, or `ansible.builtin` for ansible-core's own and `eda.builtin` for ansible-rulebook's |
| `since` | The lowest version of its collection that has it. For `ansible.builtin` and `eda.builtin`, it's the ansible-core or ansible-rulebook version it was added in. Only if known |
| `until` | The version of its collection it was removed in. Only if it was removed |
| `short_description` | One line from its documentation, with any [Ansible markup](https://docs.ansible.com/projects/ansible/latest/dev_guide/ansible_markup.html) such as `C(...)` as written. Only if it has one |
| `routing` | Its entry in its collection's `meta/runtime.yml` `plugin_routing` (`redirect`, `deprecation`, `tombstone`), as the collection wrote it. Only if it has one |
| `fqcn` | Its full name |
| `index` | The name of the index it was found in, or for `ansible.builtin` and `eda.builtin`, the release it was read from, such as `ansible-core 2.21.5` |
| `matched` | Whether it matched the search, rather than being listed because a match redirects to it |

Everything but `fqcn`, `index` and `matched` comes from the index, or for
`ansible.builtin` and `eda.builtin`, from the release.

Whether a plugin can be used, as the details' **Installed** line says, isn't
in the JSON. alai uses the JSON facts, following each `routing.redirect` as
ansible-core does, and checking each step's `since` and `until` against
the version of its collection that's installed.

## Where the list comes from

The index publishes the list, `plugins.jsonl`, alongside its collections.
alai downloads it once and then only again when it changes. It holds each
plugin's name, type, collection, short description, the versions that have
it, and its collection's routing for it, from `meta/runtime.yml`. With
several [indexes](configuration.md#indexes), each one's list is searched, and
where two have the same plugin, the first index's is used. An index without a
list is skipped, and if none has one, `alai find` says so.

## ansible-core's own plugins

`ansible.builtin`, ansible-core's own plugins, and `eda.builtin`,
ansible-rulebook's event sources and filters, aren't collections on an
index. alai reads them from each project's latest release on PyPI, or on
the [`python_index`](alai-toml.md#python_index) set in its place, and
ignores any lines an index has for either name. The first
`alai find` downloads each sdist (ansible-core's is about 5 MB), reads its
plugins and routing without running anything in it, and keeps the result in
the cache, so later runs only ask the index whether there's a new release.
Both count as installed, as each is part of the tool that runs its content.
Offline, or when the index can't be reached, alai uses the last release it
read, or warns that they aren't searchable until a run online reads them.
