# Finding plugins

`alai find` answers "which collection has this?" for a module, plugin,
role or playbook, and "where did it go?" for one that was moved or removed.
It searches a list of every plugin in every collection on the index, so it
finds collections you haven't installed, and lists the ones you have first.

[Finding plugins](casts/find.cast){ .alai-cast }

## What to search for

| Query | Finds | Example |
|---|---|---|
| A word | Every plugin whose name contains it, exact names first | `alai find user` finds `user`, `win_user` and `user_info` |
| A glob, with `*`, `?` or `[...]` | Names that match it; with dots, full names that do | `alai find '*_info'`, `alai find 'community.general.*'` |
| A full name (FQCN) | That plugin's [details](#a-plugins-details) | `alai find community.general.docker_container` |

`ansible.legacy.NAME` is searched as `ansible.builtin.NAME`, as that's
what Ansible loads, unless a plugin with the same name is in a `library/`
or `*_plugins` directory beside the playbook, in a role, or configured,
which alai doesn't look in.

`-x/--exact` matches whole names only, so `alai find -x user` leaves out
`win_user`. `-t/--type` limits the search to one type, using the names
`ansible-doc -t` takes (`module`, `filter`, `lookup`, `callback`…), plus
`role` and `playbook`; give it more than once for several. On a terminal,
the part of each name that matched is underlined.

When nothing matches, alai suggests similar names and exits with code 1.

### Quoting globs

Put a glob in single quotes: `alai find '*_info'`, not `alai find *_info`.
`*`, `?` and `[...]` mean the same to the shell, which expands them to the
names of files in the current directory before alai sees them:

- **bash** (and `sh`) passes the pattern on unchanged when no file
  matches it, so it seems to work, until the day a file such as
  `docker_container_info` is in the directory and alai is asked for that
  name instead.
- **zsh** and **fish** stop with an error (`no matches found`) when no
  file matches, and alai never runs.

Single quotes keep everything as typed. A word or a full name with no
glob characters needs no quotes.

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

A plugin's details: here an old name whose route breaks, as
`ansible.netcommon` removed `ipmath` in 2.6.0 with nothing pointing on,
so alai suggests the plugin with the same name in `ansible.utils`; and an
`ansible.legacy` name as plays use it:

[An old name that no longer leads anywhere, and an ansible.legacy name](casts/find-redirects.cast){ .alai-cast }

Every match, through a pager in colour, or for a script with
[jq](https://jqlang.org/):

```console
$ alai find user --color always | less -R
$ alai find user --output-format json | jq -r '.plugins[] | select(.matched) | .fqcn'
$ alai find -x ipmath --output-format json \
    | jq -r '.collections | to_entries[] | select(.value.installed) | .key'
```

Piped, alai lists every match rather than the first 20, and
`--color always` keeps the colours `less -R` shows.

## What's listed first

Matches are in two sections: **Installed**, the collections in the
project's collections directory together with `ansible.builtin`, which
ansible-core always has, and **Not installed**, everything else on the
index. Within each, matches are listed in this order:

1. Exact names before partial matches.
2. Plugins you can use, then redirects to other plugins, then ones that
   were removed.
3. `ansible.builtin`.
4. Collections you've installed before, in any project: those in alai's
   cache.
5. Collections in the [`ansible`](https://pypi.org/project/ansible/)
   package, which the Ansible community reviews, then others from the same
   namespaces.
6. Everything else, with collections that look like copies of a
   collection in the `ansible` package last. Their names are grey, and a
   plugin's details say which collection it seems to copy. That's a guess,
   from at least half their plugins having the same names, so it's only
   used to rank them lower, not to hide them.
7. By name.

On a terminal, only the first 20 matches are listed, installed ones
first; `--limit N` changes that, and `--limit 0` lists them all. When the
output goes to a pipe or a file, such as `alai find user | less` or
`| grep`, they're all listed unless `--limit` is given. `--installed` lists only the
installed ones.

Only the project's collections directory counts: collections elsewhere
that Ansible might also load, such as in `~/.ansible/collections` or those
that come with the `ansible` Python package, are listed as not installed.
Outside a project, nothing but `ansible.builtin` counts as installed. alai
finds the project as [other commands do](projects.md#finding-the-project-root),
so a project with only an `ansible.cfg`, or only a
`collections/ansible_collections` directory, works too; alai reads what's
installed there, not `alai.lock`.

## What the markers mean

Each line starts with a marker, and where a plugin isn't simply there to
use, says why in place of its description:

| Marker | Means |
|---|---|
| `✓`{ .ansi-green } | In an installed collection |
| `·`{ .ansi-dim } | In a collection that isn't installed |
| `↑`{ .ansi-yellow } | Added in a newer version of the collection than the one installed: upgrade it to use it |
| `↪`{ .ansi-cyan } | Redirects to another plugin, shown after `→`: the name still works, as Ansible loads that plugin instead |
| `↪`{ .ansi-yellow } | Installed, but redirects through a collection that isn't, so Ansible can't load it until that's installed too |
| `⚠`{ .ansi-yellow } | Deprecated, to be removed in the version or on the date shown |
| `✗`{ .ansi-red } | Removed, or its redirect leads nowhere Ansible can load |

On a terminal, a legend under the list explains the markers it uses.

Descriptions that use [Ansible's documentation markup](https://docs.ansible.com/projects/ansible/latest/dev_guide/ansible_markup.html)
are shown as the docs show them, as far as a terminal can: code and
values such as `C(lxc)` in cyan, options in bold, links that can be
clicked in terminals that support them. Without colour, they're plain
text.

!!! note "It depends on your terminal"

    How much of this you see depends on the terminal. Italics, and links
    (OSC 8 hyperlinks), aren't supported everywhere; a terminal that
    doesn't support one shows the plain text instead. Some terminals only
    mark a link while you hold Ctrl (Cmd on macOS) and hover over it, as
    Ghostty does. Inside tmux, links only reach the terminal when tmux is
    told it supports them, with `set -as terminal-features
    ",xterm-ghostty:hyperlinks"` (using your terminal's `TERM`, or `*`)
    in its config, and you detach and reattach afterwards.

A plugin that was removed is only listed when its name is searched for in
full, as that's how to find where it went; `--include-removed` lists those
that match part of a name too.

## A plugin's details

Searching for a full name shows that plugin's details. The first line,
**Installed**, answers whether it's installed in this project, in the
sense that matters: whether Ansible would load it by that name, and if
not, why not: its collection, or one its redirects lead
through, isn't installed or is too old to have it, or it was removed.
Below that are the versions of its collection that have it, what its
collection says about its removal or deprecation, its **Route**, and the
command that makes it usable. The route is the plugin, then each name its
redirects lead to, one per line, each with whether its collection is
installed, which version and where: `✓` installed, `·` not installed,
`↑` installed but too old to have it, `✗` removed from the version
installed, and a `↪` before each redirect, yellow when that step's
collection isn't installed or is too old. A name that's only a redirect, such as
`ansible.builtin.win_copy`, says which plugin it's another name for.

[A plugin's details and where its redirects lead](casts/find-detail.cast){ .alai-cast }

A redirect can lead to another that redirects again. alai follows them the
way ansible-core 2.20 does, a step at a time: the redirect applies even if
the plugin's own file is still there, a removed plugin anywhere along the
way stops it, and so does a redirect back to a plugin already visited, or
to a name that isn't a full one. If the route breaks, the details say where
and why. Each step can be in a different collection, and Ansible needs all
of them installed, at a version that has the plugin: a plugin that's
installed but redirects through a collection that isn't, such as
`ansible.builtin.docker_container` without `community.docker`, is marked
with a yellow `↪`, one whose route reaches an installed collection that's
too old with `↑`, and its details name what to install or upgrade.

Where a step was removed, the route breaks there, unless the installed
version of that step's collection is from before the removal, as it still
has the plugin. When a plugin, or a step on its route, was removed with no
note saying what to use instead, the details list up to three other
plugins with the same type and name under **Same name**, those you can use
first: `ansible.builtin.ipmath` led to `ansible.netcommon.ipmath`, which
was removed in 2.6.0 with nothing pointing on, and `ansible.utils.ipmath`
is what replaced it. They aren't redirects, only a match on the name, so
check one does the same job before switching.

## For scripts

`--output-format json` prints what alai knows rather than what it
concludes: each plugin as the index gives it, and whether each collection
is installed. It has no limit unless `--limit` is given. For
`alai find ansible.builtin.docker_container --output-format json`:

```json
{
  "collections": {
    "ansible.builtin": {"installed": true, "path": null, "version": null},
    "community.docker": {
      "installed": true,
      "path": "/home/me/project/collections/ansible_collections/community/docker",
      "version": "4.8.1"
    }
  },
  "omitted": 0,
  "plugins": [
    {
      "collection": "ansible.builtin",
      "fqcn": "ansible.builtin.docker_container",
      "index": "alai",
      "matched": true,
      "name": "docker_container",
      "routing": {"redirect": "community.docker.docker_container"},
      "type": "module"
    },
    {
      "collection": "community.docker",
      "fqcn": "community.docker.docker_container",
      "index": "alai",
      "matched": false,
      "name": "docker_container",
      "short_description": "manage Docker containers",
      "since": "0.1.0",
      "type": "module"
    }
  ],
  "v": 1
}
```

| Field | |
|---|---|
| `collections` | Every collection the plugins are in or redirect to, by name, with whether it's [installed](#whats-listed-first) and, if so, its `version` and the `path` of its directory. `ansible.builtin` is always installed, with no version or path. `version` is `null` if it can't be read |
| `omitted` | How many matches `--limit` left out |
| `plugins` | The matches, in the order the text output lists them (installed first), then every plugin their redirects lead to that the index has, each listed once |
| `v` | The format's version, which changes if a field is removed or changes meaning |

Each plugin has:

| Field | |
|---|---|
| `collection` | The collection it's in, or `ansible.builtin` for ansible-core's own |
| `fqcn` | Its full name |
| `index` | The name of the index it was found in |
| `matched` | Whether it matched the search, rather than being listed because a match redirects to it |
| `name` | Its short name |
| `routing` | Its entry in its collection's `meta/runtime.yml` `plugin_routing` (`redirect`, `deprecation`, `tombstone`), as the collection wrote it. Only if it has one |
| `short_description` | One line from its documentation, with any [Ansible markup](https://docs.ansible.com/projects/ansible/latest/dev_guide/ansible_markup.html) such as `C(...)` as written. Only if it has one |
| `since` | The lowest version of its collection that has it; for `ansible.builtin`, the ansible-core version it was added in. Only if known |
| `type` | Its type, as `-t` takes it: `module`, `filter`, `role`… |
| `until` | The version of its collection it was removed in. Only if it was removed |

Everything but `fqcn`, `index` and `matched` comes from the index as it
gives it, along with any fields it adds that alai doesn't know yet. The
keys are sorted here to make them easier to find; alai doesn't print them
in any particular order.

Whether a plugin can be used, as the details' Installed line says, isn't
in the JSON: it's alai's reading of these facts, following each
`routing.redirect` as ansible-core does (with `ansible.legacy` as
`ansible.builtin`), and checking each step's `since` and `until` against
the version of its collection that's installed. A step the index doesn't
have has no entry.

## Where the list comes from

The index publishes the list, `plugins.jsonl`, alongside its collections;
alai downloads it once, about 1.5 MB, and then only again when it changes.
It holds each plugin's name, type, collection, short description, the
versions that have it, and its collection's routing for it, from
`meta/runtime.yml`. With several [indexes](configuration.md#indexes), each
one's list is searched, and where two have the same plugin, the first
index's is used. An index without a list is skipped, and if none has one,
`alai find` says so.

!!! note "Limits"

    - Only collections the index has are searched. A collection installed
      from somewhere else, such as a source checkout, isn't listed, even
      when it's installed.
    - Routing comes from the newest version of each collection on the
      index, not the version installed, which may route a plugin
      differently.
    - `ansible.builtin`'s plugins are those of the ansible-core release
      the index was built from, not the one you have, and alai counts
      them all as installed. If yours is older, a plugin added since is
      still marked `✓` (its details say which version added it); if it's
      newer, plugins added after the index's release aren't listed.
