# Finding plugins

`alai find`: which collection has a module, plugin, role or playbook, and
where a moved or removed one went. In `src/alai/_plugins.py` (reading,
searching, routing and ranking), `src/alai/_find.py` (the command's output)
and `src/alai/_commands/find.py` (its options). The user documentation is
[docs/finding-plugins.md](../docs/finding-plugins.md). Designed with the
user on 2026-10-21, from prototypes run against the live index.

The data is the index's plugin index, `plugins.jsonl` (galaxy-sync's
`docs/index-spec/plugins.md`): one line per plugin in every collection,
with its type, name, collection, `since`/`until`, `short_description` and
its collection's `plugin_routing` entry. Galaxy's API has no efficient way
to answer "which collection has X"; a plugin index alai downloads and
searches locally is the reason the index has one.

## What was compared

- **uv 0.13 has no search command** (`uv --help`, 2026-10-21), so there's
  nothing of uv's to match for the command itself. Its conventions are
  still followed where they apply: `--output-format text|json`, as
  `uv python list` and `uv version` have it, and a warning with exit 1
  when nothing is found, as `uv pip show nosuchpkg` gives
  (`warning: Package(s) not found for: nosuchpkg`).
- **`cargo search` (cargo 1.90)** is the closest command: `--limit` with a
  default of 10, and a line saying how many more there are. alai's
  `--limit 0` lists them all, which cargo can't (it caps at 100).
- **`apt-file search`** is the model for how it works: one file downloaded
  and searched locally, as the spec says.
- **`ansible-doc -t TYPE`** gives the type names `-t/--type` takes.
- **ansible-core 2.20's `PluginLoader`** (`lib/ansible/plugins/loader.py`)
  for how routing is followed (see [Routing](#routing)).

## Decisions

### The command

- **`alai find QUERY`**, the name the spec already uses. A listing only;
  full documentation is a separate command later (see
  [Not done yet](#not-done-yet)), so `find` stays quick and pipeable.
- **A word matches part of a name** by default, as forgetting the exact
  name is the common case (`user` finds `win_user`), with exact names
  ranked first. **A glob** (`*`, `?`, `[`) matches short names, or full
  names when it has dots (`community.general.*`). **A FQCN** shows that
  plugin's details. `-x/--exact` matches whole names only; asked for so
  scripts and counts aren't swamped by partial matches (`user` is 1,675
  modules partially, 92 exactly). With a glob it's an error, as no name
  contains `*`. No divider between exact and partial matches: the matched
  part is underlined on a terminal, which shows which is which.
- **`-t/--type`**, repeatable, takes any string: the spec says new types
  can appear without a format change and must be shown, so there's no
  fixed list to check against. Tab
  completion still offers the spec's types (`PLUGIN_TYPES` in
  `_commands/find.py`), through an argcomplete `ChoicesCompleter` set only
  when completing, so argparse never sees them as `choices` (2026-10-21).
- **Search everything, installed first.** Restricting to installed
  collections would defeat the point ("which collection has X" is mostly
  about collections you don't have). Two sections, **Installed** and
  **Not installed**; `--installed` shows only the first.
- **Installed means in the collections directory alai manages**, as alai
  uses no other ([README.md](README.md#principles)): collections in
  `~/.ansible/collections` or the `ansible` package's `site-packages`
  aren't counted, though plain `ansible-playbook` would load them.
  On disk: every collection under the project's
  `collections_dir`, with its version from `MANIFEST.json`, or for one
  without, such as a checkout, its `galaxy.yml` (2026-10-30; `find` only,
  as `sync` replaces a collection with no `MANIFEST.json` version), found the way
  `alai collection install` finds the project (an `alai.toml`, or
  `--collections-dir`), so it works
  without a lock. Outside any project, only `ansible.builtin` counts, and
  a dim status line says so. **`ansible.builtin` is always installed**, as
  it's part of ansible-core.
- **`--limit N`**, 20 by default for text on a terminal, unlimited for
  JSON (a script silently getting 20 would be a trap) and for text that
  isn't going to a terminal, `0` for all. It applies to every match,
  installed ones first, so a broad word such as `user` with many
  collections installed doesn't fill the terminal with the Installed
  section (asked for 2026-10-23). The default is twice `cargo search`'s
  10, as installed matches now count towards it and would otherwise
  crowd out the ones not installed, which "which collection has X" is
  mostly about; 20 rows with the headings, `… N more` lines and legend
  are about 26 lines, which fits a terminal window without scrolling
  (2026-10-23). It isn't sized to the terminal's height, so the same
  command lists the same matches in any window. A section's heading counts all its
  matches, a section cut short ends with `… N more`, and one with every
  match left out is still headed, to say there are some. Piped output is
  unlimited (asked for 2026-10-21, wanting a pager to get everything):
  whether a pager is reading can't be told portably (on Linux, only by
  finding the pipe's reader in `/proc`), but a pipe or file can, with
  `isatty()`, as colour and the legend already are, and `less`, `grep`,
  `fzf` and a file all want the whole list. `ls` likewise changes its
  output when it isn't writing to a terminal.
- **Removed plugins and aliases are listed only when named exactly** (a
  whole name or a FQCN), as looking one up is how to find where it went;
  `-a/--all` lists those matching part of a name too. This holds
  in the Installed section as well: an installed collection's removed
  plugin is listed when named, marked removed, since the user may be using
  it. An alias is a line that's only routing, with a redirect: no file in
  any version, or for `ansible.builtin` not one ansible-core ships. It's
  hidden only when the plugin its route ends at is listed too, so a row
  never just repeats another. An alias that renames stays, as the way to
  find the new name from the old (`ngine_io.vultr.vultr_region_facts` →
  `vultr_region_info`). Of the index's 1,162 aliases outside the builtin
  collections, 92 in 18 collections rename like that; most of the rest only
  drop an old subdirectory prefix (2026-10-30). Hiding only the builtin
  ones was weighed and rejected: any installed collection's aliases crowd
  the Installed section the same way, `community.general` having 260
  routing entries. Aliases were hidden on
  2026-10-30, as `ansible.builtin`'s 3,973 counted as installed and the
  limit fills the Installed section first: outside a project, `find copy`
  listed 12 aliases such as `ansible.builtin.win_copy` and no plugin that
  wasn't installed, and `find user` 15 aliases. The flag was
  `--include-removed`, renamed when aliases joined it. uv names such flags
  for what they widen (`uv python list --all-versions`, `--all-platforms`,
  `--all-arches`, uv 0.13.5) because it has several; `find` widens one
  thing, names that aren't a plugin of their own, so it's plain `--all`,
  as `ls -a` and `docker ps -a`.
- **Nothing found is a warning and exit 1**, as `uv pip show`, with up to
  three close names from `difflib` (`did you mean 'docker_container'?`).
  No configured index with a plugin index is an error (exit 2). For a
  FQCN the suggestions are full names (2026-10-30, as
  `ansible.legacy.win_disk_info` suggested `win_disk_info`, which didn't
  say where it is): the plugins with the closest short names, ordered by
  how close the short name is, then by how close the collection is
  (`ansible.windwos.win_copy` suggests `ansible.windows.win_copy` first).
  Comparing whole FQCNs was tried first, but let a similar collection
  outrank the right name (`community.general.win_disk_inf` suggested
  `stevefulme1.ddn.ddn_disk_info` first).

### Output

- **The result goes to stdout**, as [output.md](output.md) decides;
  colour follows `--color` and stdout being a terminal
  (`_output.color_enabled(stdout=True)`; a flag rather than a stream
  argument, as annotating one needs `typing`, which every command would
  then import at startup).
- **Rows, not a table.** Prototyped both (2026-10-21); the user chose rows:
  ` ✓ module  ansible.windows.win_copy  Copies files…`. A bordered table
  takes ~12 columns and gets in the way of copying names. The collection
  part is blue, the name bold with a partial match underlined, the type
  magenta. On a terminal the last column is cut to fit; piped, nothing is
  cut.
- **One marker per row**: `✓` installed, `·` not installed, `↑` in a newer
  version than installed, `↪` redirects (yellow when the route needs a
  collection that isn't installed), `⚠` deprecated, `✗` removed or a
  broken redirect. Where a plugin isn't simply usable, the row says why in
  place of its description.
- **A row gives the reason, not the history** (asked for 2026-10-30, as
  rows such as `removed in 2.0.0; → X (via 1 more); Y isn't installed;
  deprecated on the way` read as mechanical, the reason for the marker
  came last and was cut first, and a redirect started with `→` in one row
  and `moved in 2.0.0 →` in the next). A redirect is always `→ TARGET`,
  where its route ends, with what stands in the way in brackets:
  `(deprecated)`, `(not installed)`, `(needs 3.0.0, local 2.5.0)`, `(not
  on the index)`, joined with commas; a broken one `→ TARGET (removed)`,
  `→ TARGET (not a full name)` or `redirect loop`. Anything else is a
  short reason: `needs 3.9.1 (local 2.8.0)`, `deprecated`, `removed in
  13.0.0`, `removed in 2.0.0 (local 2.5.0)` (the local version only when
  it's past the removal), `removed in 2024-03-22, use X` for a tombstone
  that also redirects. "Local", not "installed", as the user found
  "installed 2.5.0" unclear. Moved, renamed, the steps and planned removal
  dates are in the details. Weighed against keeping the description with
  a status in front of it (better for browsing, but on 80 columns the
  description is what's cut whenever there's a status) and grouping
  aliases under the plugin they lead to (closest to "what are my
  options", but the limit, counts, sections and ranking would all work on
  groups, and piped output would nest).
- **The box and section headings stop a column short of the terminal**,
  as rows do (2026-10-30), so no line leaves the terminal waiting to wrap.
  Made while looking into a garbled bash prompt over SSH, which turned out
  to be the SSH session, not alai: the output's escapes, checked through a
  pseudo-terminal, are balanced and end in a newline.
- **A `warning_text` wraps inside the box** on a terminal (asked for
  2026-10-30), at the top and under a route step, with each line starting
  where the first does. Its markup is rendered as a description's is (14
  on the index have some, such as community.general.aix_devices's
  `C(ibm.power_aix)`, noticed by the user), in the warning's colour with
  the markup's style over it. So it's wrapped by `_wrap`, word by word
  over the styled pieces, as `textwrap` only takes plain text; a word can
  span pieces (`C(name),`), and pieces in the same style are joined. The
  description and route lines aren't wrapped yet; a path is one long word
  anyway. Piped, nothing is wrapped, as nothing is cut in the list either.
- **The details' top is what the collection says** (asked for 2026-10-30):
  the description, then the plugin's own tombstone or deprecation
  `warning_text`, before the fields, rather than under the verdict. The
  Versions line is only the range, `0.1.0 up to 2.0.0` for a removed one,
  as `0.1.0 until 2.0.0, which moved it` read oddly and `0.1.0 to 2.0.0,
  removed in 2.0.0` said the version twice and read as if 2.0.0 had it.
  Whether it's removed or moved is the verdict's and the route's. The
  route's first step is the plugin itself, so it says `this plugin`
  (grey) and where its collection is installed, if it is, and nothing the
  lines above already say. The last step's description is a sentence on
  its own line under it, like a step's `warning_text`, as `Execute tasks
  in pods running on Kubernetes., kubernetes.core isn't installed` read
  badly.
- **"Removed" only when the name fails** (asked for 2026-10-30, as "removed
  in" beside a working redirect read as if the name was gone). In the
  details, a plugin with `until` whose name still redirects, with the
  route intact, was `moved in X` to another collection or `renamed in X`
  within its own (652 lines, 744 more with a deprecation on the index), on
  a route step after the plugin's own. A
  deprecation on a redirect step is `old name deprecated`, as it's the
  name that goes, not the plugin it leads to (about 1,550 lines). Rows
  first said the same, then gave only the reason (above). "Moved in X"
  assumes the redirect came with the removal, which the index can't
  confirm, but nearly always holds.
- **A deprecation's planned removal is in the details only** (2026-10-30):
  the row says `deprecated`, and the details have a `Removal` line,
  `planned in 14.0.0` or `planned on 2022-01-23`, after Versions, with
  the `warning_text` at the top (asked for, so a deprecation with a known
  end is marked whether or not the plugin's installed; the verdict had it
  only when it was usable). Of the index's 2,231 deprecations,
  1,777 give a version and 409 a date, 309 of those already past (258 on
  redirects that still work, such as `awx.awx.tower_*`'s 2022-01-23), and
  versions slip the same way, so in a row it was mostly length and doubt.
  Flagging overdue ones was rejected: dates can be checked, but versions
  can't, as the index has no latest version per collection.
- **Each route step shows its own `warning_text`** (2026-10-30), under the
  step, yellow for a deprecation and red for a tombstone, and its planned
  removal: ansible-core warns at every step it passes, and only the
  plugin's own text was shown, so `ansible.builtin.tower_project` hid
  `awx.awx.tower_project`'s "use awx.awx.project instead". The index
  carries routing from each collection's latest stable version only
  (galaxy-sync's `plugins.py`), so a `warning_text` an earlier release had
  and a later one dropped isn't there, as for
  `community.general.docker_container`'s bare redirect in 10.7.3 and
  13.5.0. Kept that way: the plugin index is a snapshot per plugin, not
  per version, and the user would rather the merge rules keep such text
  (galaxy-sync's side) than the format grow. An installed collection's
  own routing is read from disk once local collections are searched
  ([Not done yet](#not-done-yet)).
- **The marker is the verdict that matters most** (asked for 2026-10-30):
  `✗` can't use it, then `⚠` shouldn't (a deprecation on the plugin or
  any step of its route, as ansible-core warns at each), then `↑` and the
  yellow `↪` (usable once something's upgraded or installed), then the
  cyan `↪` (works, under another name), then `✓` and `·`. Before, a
  redirect beat a deprecation, so a deprecated redirect such as
  `awx.awx.tower_project` was a cyan `↪` and the user, who searches for
  what they can use, wasn't warned off it. The notes still say everything
  (`→ awx.awx.project`), and one deprecated only further along its route
  says `deprecated on the way`. The marker is picked in the output, not
  by reordering the states: the details' verdict, the hints and ranking
  still read the state, so a plugin both deprecated and too old keeps its
  upgrade hint. One added after the installed version and gone from the
  latest is removed, not newer: no upgrade to the latest brings it.
- **A legend, one line, only of the markers shown**, and only on a
  terminal, not with `-q`. The user asked how people would know what the
  markers and colours mean without spending space; the notes already
  explain all but `✓` and `·`, and the section headings explain those, so
  a full-time key would be noise. Probable copies add `grey probably a
  copy of another collection`, `grey` itself greyed out.
- **The installed collection's path** (asked for 2026-10-21) in the
  details, after its version and shortened with `~`, and as the
  collection's `path` in JSON, absolute; `null` for `ansible.builtin`, as alai
  doesn't look up where ansible-core is. Not the plugin file's path: a
  filter or test's file needn't share its name, so finding it means parsing
  the collection's Python, which alai-doc will do anyway (see
  [Not done yet](#not-done-yet)). Not in
  the list, where it would repeat on every row of a collection.
- **The details lead with a verdict, `Installed`** (2026-10-21; first
  named `Usable`, renamed by the user once the Route carried each
  collection's own installed state, which freed the name): whether
  ansible-core would load the plugin by this name in this project, with
  its marker and why not. A green `✓ Installed with ansible-core` on
  `ansible.builtin.win_copy`, with `ansible.windows` missing noted lower
  down, read as usable at a glance. The alternatives were colouring the
  Installed line by the overall state (smaller, but still mixing "is it
  there" with "does it work") or only rewording routing-only names. The
  Status line and the route's failure lines are folded into the verdict.
  **There's no Installed line; the Route says it, step by step**
  (suggested by the user, 2026-10-21): the plugin, then each name its
  redirects lead to, each with its own marker (`✓`, `·`, `↑`, `✗`, and a
  `↪` before each redirect, yellow when that step's collection is missing
  or too old) and its collection's version and path, or that it isn't
  installed. Which collection is installed only matters per step, and a
  single Installed line could only describe the first. A plugin with no
  redirects has a one-line route; only the last step of a real redirect
  repeats a description, as the plugin's own is at the top. A
  routing-only line with no description says `Another name for <where it
  leads>.` in the description's place, as most of the 3,973 builtin
  redirects are. The verdict isn't in JSON (see below).
- **A step into a collection too old to have the plugin** makes the route
  `newer` (`↑`), with an upgrade hint for that collection: a redirect to
  `ansible.windows.win_disk_info` (added in 3.9.1) with 3.8.0 installed
  doesn't load. Found while adding the verdict; before, only the
  collections' presence was checked. Any route ranks after plain plugins,
  whatever its state.
- **A FQCN's details** in a panel: the description as a sentence
  (capitalised, ending in a full stop; asked for 2026-10-21, as
  `short_description` is conventionally neither and collections differ.
  On the live index 2,528 of 67,041 start lowercase and 46k have no full
  stop. A first word that isn't plain lowercase letters, such as `oVirt`,
  `floating_ips` or `tasks/main.yml`, is a name and isn't capitalised. The
  list keeps them as given, as fragments in a column), the versions that have it,
  whether it's installed (and which version), status, the tombstone's or
  deprecation's `warning_text`, each redirect hop with its own state, the
  "probably a copy" reasoning, and a hint: `alai add NAME` in a project
  with an `alai.toml`, else `alai collection install NAME`, or
  `alai sync -P NAME` / `alai collection install -P NAME` to upgrade to a
  version that has it. The hint names the collection the route ends in,
  not the one asked about.
- **Ansible markup in descriptions is interpreted** (2026-10-21): 215
  short descriptions use it, mostly `C()` (`Returns a path with C(~)
  translation.`). `_find.markup` shows what alai-core's markup parser
  (`alai_core.markup.parse`, since 2026-10-29) splits it into: the text of
  `C()`, `B()`, `I()`, `L()` and `R()`, the name in `M()` and `P()`, the
  URL in `U()`, the value in `V()` and `E()`, and the option or return
  value of `O()` and `RV()` without the plugin, entrypoint or `ignore:`
  before it. `HORIZONTALLINE` is a space, as the parser drops the spaces
  around it. Markup that doesn't parse, such as an unclosed one or
  `O(entrypoint:name)` outside a role, is shown as written. Only http(s)
  URLs become links. Before core had a parser, alai had its own regex
  one, which the core one matches but for those cases. With colour on,
  code and values (`C()`, `V()`, `E()`) are cyan, options and return
  values (`O()`, `RV()`) and `B()` bold, as docs.ansible.com shows
  options, `I()` italic, and plugin names (`M()`, `P()`) blue, as
  collections are in the list. `L()` and `U()` become OSC 8 links, only
  on a terminal, as `less` and files would show the codes; terminals
  without OSC 8 show the text. `R()`'s anchor is on docs.ansible.com, so
  its text is plain. With colour off, it's all plain text. A first
  version only stripped the markup; the user asked for it interpreted
  where possible ("return pong" lost that `pong` is a value). Lines are
  cut by visible characters, so styling and links never break the cut.
  The list doesn't style a probable copy's description, which is dimmed
  whole. JSON keeps the markup as the index gives it, being facts.
- **No pager, no TUI.** `--limit` keeps the list short and it stays usable
  in pipes. A picker can be built on it with fzf, taking the FQCN column
  and previewing with `alai find FQCN`; a built-in one isn't planned
  here, as plugin documentation is to be a project of its own (below).
- **No rich**, as [output.md](output.md) decides: the rule, panel and
  aligned rows are a few lines over `_output.styled`.
- **`--output-format json` gives facts, not conclusions** (2026-10-21):
  `{"v": 1, "plugins": [...], "collections": {...}, "omitted": N}`.
  `plugins` is the matches in ranked order, then every plugin their
  redirects reach that the index has, each once: the index's line as given
  (unknown fields included, `v` dropped) plus `fqcn`, `index` and
  `matched`. `collections` has every collection those plugins are in or
  redirect to, with `installed`, `version` and `path`. A first version
  carried alai's conclusions on each match (`status`, `usable`, `route`,
  `route_problem`, `route_not_installed`, `probable_copy_of`, and
  installed fields repeated per plugin); the user found that too magical
  for a machine format, as a script couldn't tell fact from alai's
  reading. Now a script follows `routing.redirect` and compares versions
  itself, as the details do; the order is the only judgement left. A
  `usable` per match could come back if scripts need the verdict. The
  first command with JSON output.
- **Where each line came from** (decided with the user 2026-10-30, once
  installed collections were read from disk). A plugin's `index` is the
  index's URL, as the lock records an index source, and only on a line
  read from an index. Without it, the line is from what its collection's
  entry describes: the installed collection's files, or for the builtins
  its `release` (`python_index`, `package`, `version`). An installed
  collection has one copy and one source, so its entry has a `source`,
  the lock's table for it when the installed version is the one locked,
  else alai's install record, else none. A plugin can't come from disk
  unless its collection is installed, so the only override is an index,
  as for a plugin only a newer version has. Weighed and dropped: `from`
  on every plugin (`{"installed": true}` says nothing the collection
  doesn't), and collections keyed by a version and source that plugins
  point to (an index's lines have no version, and nearly every plugin
  would point at its collection's default). `index` was the index's
  configured name, and the builtins' release as text, until then.

### Ranking

There's no popularity signal to rank by. Galaxy's download counts were
rejected (2026-10-21): an index is static files, and custom indexes have
no such numbers, so ranking is worked out from what alai has. Matches are
ordered by, in turn:

1. exact names before partial matches;
2. usable before removed or broken;
3. plugins before redirects;
4. `ansible.builtin`;
5. collections in the artifact cache (installed before, in any project);
6. collections in the `ansible` package, then other collections in those
   namespaces;
7. not a probable copy;
8. status, then modules before other types, then collection, type and
   name.

Modules first (asked for 2026-10-30), as they're what most searches are
for. Only within the same standing: a module in some other collection
doesn't outrank a filter in `ansible.builtin` or a lookup in the
`ansible` package. The rest of the types stay by name.

Redirects rank after plugins (2026-10-21, once the index had
`ansible.builtin`): `ansible_builtin_runtime.yml` redirects every module
that left ansible-core in 2.10, so ranking `ansible.builtin` first put 12
redirects for `copy` ahead of `ansible.windows.win_copy`. The plugin each
leads to is listed in its own right.

- **The `ansible` package list is hardcoded**, in
  `src/alai/_ansible_package.py`, written by `scripts/ansible_package.py`
  from ansible-build-data's `<major>/ansible.in`: the union of the last
  five majors (11 to 15, 103 collections). It's the community's reviewed
  set and changes slowly (11 to 15 dropped 13 collections and added 5);
  rerun the script when a major is branched. Collections dropped from the
  package stay trusted for five majors, which suits ranking, as they're
  still what people search for.
- **Both full names and namespaces count.** Galaxy namespaces have owners,
  so `cisco.*` is Cisco's, which lets a vendor's new collection
  (`cisco.catalystcenter`) rank above unrelated ones before it's in the
  package.

### Probable copies

Forks and bundles of popular collections crowd the results:
`anophelesgreyhoe.general` has 1,464 of `community.general`'s 1,466
plugin names, and ranked above it alphabetically. A collection is a
**probable copy** when it has at least 3 plugins and at least half its
`(type, name)` pairs are in one `ansible` package collection. Collections
in a package namespace are never judged: without that, `community.kubernetes`
(the old name of `kubernetes.core`) and `cisco.catalystcenter` (the
successor to `cisco.dnac`) were flagged. On the live index that flags 112
collections (2026-10-21), almost all forks and test copies; `juniper.device`
and `opennebula.cloud` are false positives, which is why it only lowers
the rank and greys the name, never hides, and the details show the numbers
(`80 of its 104 plugins have the same names`). It needs only
`plugins.jsonl`, so it works for any index.

### Routing

Followed as ansible-core 2.20's `PluginLoader._resolve_plugin_step` does,
read from its source:

- **Routing is read before the plugin's file**, so a redirect applies even
  where the file still exists. A removed plugin whose routing still
  redirects is shown `↪`, not `✗`: playbooks using the old name work.
- **A tombstone fails the load**, at the plugin or at any hop, before its
  redirect is considered.
- **A deprecation warns and carries on**, at every hop.
- **A redirect must be a FQCN**, or ansible-core fails with "Redirects must
  use fully qualified collection names". A FQCN is what
  `AnsibleCollectionRef.is_valid_fqcr` takes, `^\w+(\.\w+){2,}$`, so it
  can have subdirectories: `mgdis.ovh.cloud.block_storage.block_storage`
  is the module in `plugins/modules/cloud/block_storage/`, which the index
  names `cloud.block_storage.block_storage` (`is_fqcn` and `split_fqcn` in
  `_plugins.py`). alai first required exactly two dots, which showed the
  15 such redirects on the index as broken, with a "Same name" guess
  (found by the user, 2026-10-30).
- **A name reached twice is `AnsiblePluginCircularRedirect`.** There's no
  limit on hops. The type never changes.

**`ansible.legacy`** isn't a collection (asked for 2026-10-21, as plays
still use it). For `ansible.legacy.NAME`, `_resolve_plugin_step` calls
`_find_plugin_legacy`, which searches `library/` and the `*_plugins`
directories (configured, beside the playbook, or in a role), then
ansible-core's own plugins, then a deprecated `_NAME` alias, and as a last
resort `ansible.builtin.NAME` through `_find_fq_plugin`, routing and all.
alai can't see those directories, so `ansible.legacy.NAME` is searched as
`ansible.builtin.NAME`, with a dim status line saying a local plugin of
the same name would win. As a redirect target (the index's one case:
`ansible.builtin.smart` → `ansible.legacy.ssh`) a hop stops at a builtin
plugin ansible-core ships, as the file is found before routing is read,
and follows the builtin's routing when it's only routing. A shipped
builtin is one with a `short_description` or no redirect or tombstone:
the index has 3,973 bare redirects and 3 bare tombstones, against 261
shipped plugins (2026-10-21). With no builtin line at all the route is
unknown, not broken, as a local plugin may provide it. A builtin's `since`
is its `version_added`, absent when that's `historical`, so the details
say "in ansible-core" rather than treating it as routing only.

**Every collection on the route must be installed**, as ansible-core reads
each hop's routing from that hop's collection. An installed plugin whose
route goes through one that isn't (asked for 2026-10-21: with
ansible-core alone, `ansible.builtin.docker_container` looked usable
without `community.docker`) is `missing`: a yellow `↪`, its own legend
entry, `community.docker isn't installed` in its notes and on the hop, and
a hint installing every missing collection at once. It ranks with
redirects. A plugin that isn't installed itself stays `redirect`, as its
hint already says what to install.

**A removed step is checked against the installed version** (2026-10-21).
A step to a plugin with `until`, or a tombstone, breaks the route, unless
its collection is installed at a version before the removal (`until`, or
the tombstone's `removal_version`): that version still has the file, so
the route ends there. Before, a removal broke the route whatever was
installed, as only the index's latest facts were used. The installed
version's own routing is read from disk now ([Installed
collections](#installed-collections)).

**Same name, as a guess at a successor** (2026-10-21). The index has 278
routes broken at a step removed with no redirect onward, and 410 ending at
a tombstone, most from the 2.10 migration: `ansible.builtin.ipmath` →
`ansible.netcommon.ipmath`, removed in 2.6.0, whose latest `runtime.yml`
has no routing for it (while `ipaddr`'s still redirects to
`ansible.utils`). Whether it ever redirected and was dropped, the index
can't say. When a plugin is `removed` or `broken` and neither it nor the
step its route breaks at has a tombstone `warning_text` (which usually
names the replacement), the details list up to three other plugins of the
same type and name that aren't removed or broken, usable ones first, then
by rank, leaving out another name for one already listed. Labelled as not
redirects, since only the name matches. Not in JSON: a script can search
the name itself.

A route to a hop the plugin index doesn't have is shown as unknown
(yellow, "can't follow it further"), not broken: the index may just lack
it. A hop to a plugin with `until` and no redirect of its own is broken,
as its latest version no longer has it.

The live index (2026-10-21) has 2,817 one-hop redirects, 24 of two hops
(`community.aws.elb_classic_lb` → `amazon.aws.ec2_elb_lb` →
`amazon.aws.elb_classic_lb`), none longer, 2 that redirect to themselves,
57 to names that aren't FQCNs (broken in current ansible-core), and 39
into `ansible.builtin`, which the index lacked until it was synced. With
`ansible.builtin` in the index (2026-10-30), 349 routes have two hops or
more, 168 of them changing a short name, and some have three
(`ansible.builtin.ec2_vpc_peer` → `community.aws.ec2_vpc_peer` →
`amazon.aws.ec2_vpc_peer` → `amazon.aws.ec2_vpc_peering`).

### Reading the file

- **Fetched like any index file**, with an ETag and revalidated each run
  (`IndexClient.plugins()`), from the `plugins` path in `config.json`. An
  index without one is skipped. `find` passes `prefetch="plugins"`, so the
  plugin index is requested alongside `config.json`, not after it, and
  `collections.json` (724 KB), which it has no use for, isn't fetched
  ([index-client.md](index-client.md)).
- **Searched as bytes.** The file is 16.5 MB (1.5 MB compressed, 101,796
  lines on 2026-10-21); parsing every line cost ~570 ms cold in Python,
  and about 190 ms into `Plugin`s with the extension (17 MB, 106,077
  lines, 2026-10-23). A search finds the query's literal parts in the raw
  bytes and parses only the lines that hold all of them; parts JSON may have
  escaped (non-ASCII, `"`, `\`, `/`) aren't looked for, falling back to
  parsing more. Redirect targets are looked up a hop at a time with one
  pass per hop (`Catalog.preload_routes`): one pass per target made
  `community.general.*` take 1.2 s. A search takes ~0.3 s, most of it the
  two revalidation requests.
- **The scanning and parsing are alai-core's** (`alai_core.plugins`, moved
  from the extension on 2026-10-28). Its search finds the lines holding
  every token with `memchr`'s SIMD search, as `bytes.find` did, and reads
  them with `serde_json`. Its `named` replaces the redirect lookups' regex,
  an alternation of every target's name, which Python's `re` was slow to
  scan 17 MB with (0.87 s of `find a`), and `names` gives the suggestions'.
  Each returns the fields `Plugin` needs and the line itself, which
  `Plugin.raw` parses only when asked, for JSON output. A line is skipped
  as before, but for the stricter JSON in [index-client.md](index-client.md).
  `assess` shares each `since` and `until` version parsed, as a broad search
  read the same few hundred 151,452 times. `memchr` was already built, as
  `serde_json` depends on it; using it directly was agreed with the user. On
  the development machine, 2026-10-23, the 17 MB, 106,078-line file, five
  runs each, offline and online (one revalidation round trip, about
  0.08 s):

  | `alai find --limit 10` | Before, offline | Now, offline | Now, online |
  |---|---|---|---|
  | `copy` | 0.13 s | 0.10–0.11 s | 0.18–0.30 s |
  | `ansible.builtin.copy` | 0.15 s | 0.09 s | 0.17–0.25 s |
  | `*_info` (12,983 matches) | 0.43–0.45 s | 0.15–0.16 s | 0.23–0.33 s |
  | `a` (73,263 matches) | 1.89–1.94 s | 0.58–0.61 s | 0.65–0.75 s |
  | `zzzqqq` (none, so suggestions) | 0.19–0.20 s | 0.14 s | 0.21–0.30 s |

  What's left of `find a` is in Python: assessing and ranking 73k matches
  (about 0.3 s), then the extension's parsing (about 0.12 s). After the move to
  alai-core, `find a` took 0.62 s against 0.57 s just before, offline and
  best of five. The difference is copying each of core's entries into a
  `Plugin`. The other queries were unchanged.
- **What needs every line is cached per change to the file**: the probable
  copies, in `<cache_dir>/plugins-v1/<hash of the URL>.json`, keyed by the
  file's SHA-256 (~7 ms) and the `ansible` package list's releases. The
  user asked for "once per ETag change"; the content hash is the same
  thing, and works for `file://` indexes, which have no ETag or cache.
- **Several indexes**: each one's file is searched, in resolution order,
  and the first index's line wins for a `(type, collection, name)` in more
  than one, as the resolver takes a collection from the first index with
  it.
- **Bad lines are skipped silently**: unreadable JSON, missing fields, or a
  newer `v`. It's a search aid, not something to resolve from.

### Installed collections

Done on 2026-10-30, on the plan of 2026-10-24, with alai-core's
`extract_collection` in place of alai-plugin-extractor, so no new
dependency and no gap on Python 3.10 and 3.11.

- **Every installed collection is read from disk** (`_local_index` in
  `_find.py`), with `docs=False`, and its lines written by `_builtin`'s
  `render_lines`, as `ansible.builtin`'s are. That covers collections on
  no index (checkouts, path, tarball and git sources) and a collection
  project's own, which is linked into the collections directory, and the
  installed version's routing, which is what ansible-core follows.
- **Searched first, as one more plugin index named `installed`**, so a
  local line wins over an index's for the same `(type, fqcn)`. An index's
  lines for an installed collection aren't skipped: the ones the local
  version lacks still say a newer version has it (`↑`), as
  `ansible.windows.win_disk_info` with 3.8.0 installed. A local line has no
  `since` or `until`: its file is there, so it's never `↑` or removed. The
  cost is that the details' "since X" goes for an installed plugin, and an
  index line for a plugin the installed version should have but doesn't
  (an edited copy) still counts as installed.
- **Cached by `MANIFEST.json`'s SHA-256**, in
  `<cache_dir>/installed-v1/<name>/<sha256>.jsonl`. The manifest holds
  `FILES.json`'s checksum, so it changes with any file. One without a
  manifest, a checkout, is read every time rather than keyed by its files'
  modification times as planned: measured 2026-10-30 on the development
  machine, `community.general`'s checkout takes 0.15-0.3 s, and reading is
  in threads, as the extractor releases the GIL (all 92 collections of the
  `ansible` package: 1.5-2.8 s serially, 0.4-0.5 s in threads). A warm
  `find copy` in that tree took 0.41-0.49 s, against 0.42-0.49 s before.
  No progress is shown: the first search after an install is under a
  second for the whole package. alai-core `eb18167` made extracting with
  `docs=False` faster, with the same lines (all 92 compared): best of
  three, warm, all 92 in threads 0.44 s to 0.24 s, `fortinet.fortios`
  0.21 s to 0.12 s, `fortinet.fortimanager` 0.21 s to 0.10 s, and
  `community.general` 0.10 s to 0.07 s.
- **`--refresh` and `--no-index`, uv's names** (agreed 2026-10-30): uv
  0.13.5's `--refresh` is "Refresh all cached data" and `--no-index`
  "Ignore the registry index". `--refresh` re-reads every installed
  collection and rewrites its cache, for one edited in place, which leaves
  `MANIFEST.json` as it was; the plugin indexes are revalidated anyway and
  the builtin lines are per release, so nothing else needs it. No
  `--refresh-package`, as re-reading all 92 `ansible` package collections
  takes about 0.45 s. `--no-index` reads no index's plugin index, so it
  needs none configured to have one; the builtins are still read, as no
  index has them. Different from `--installed`, which still uses the index's
  lines for an installed collection's newer plugins.
- **A collection that can't be read is a warning**, and its index lines are
  used: a `MANIFEST.json` ansible-galaxy wouldn't build, or a `galaxy.yml`
  that isn't YAML.

### ansible.builtin and eda.builtin

- **Read from the sdists, not the index** (2026-10-30). The index's
  `ansible.builtin` lines were one ansible-core release's, and the spec is
  dropping them. alai reads ansible-core's and ansible-rulebook's own
  plugins itself (`_builtin`): alai-core's `extract_ansible_core` and
  `extract_ansible_rulebook` over the parts of each sdist they read
  (`artifact.extract_sdist`), written as plugin index lines in
  `<cache_dir>/python-index-v1/<host>-<hash>/<project>/<version>.jsonl`,
  named for `python_index` as `index/` is for an index, and searched as
  another index's file, first. The extracted source is kept beside them, in
  `<version>/`, for `alai collection path`
  ([collection-path.md](collection-path.md)). Every index's lines for either collection
  are ignored, always (`PluginIndex(skip=...)`), not only until the public
  index drops its `ansible.builtin` lines: a third-party index's would
  describe some release of its choosing, where alai's come from the
  release it downloaded (decided 2026-10-30). The lines are made as
  galaxy-sync made them: `since` is
  the documented `version_added` unless `historical`, there's no `until`,
  and routing with no plugin is a line of its own. `action` routing is left
  out: the spec leaves out action plugins that back a module, and
  ansible-core's `action:` entries mirror its `modules:` ones.
- **The latest stable release of each**, from the project's page on
  `python_index` (PEP 691 JSON, as uv asks for it; an index that serves
  only HTML is an error naming it): the highest version with an sdist that
  isn't a pre-release or yanked, which is what PyPI installs. The page is
  cached and revalidated as index files are, so a new release costs one
  download (~5 MB; under 4 s with extraction, cold) on the next run.
  galaxy-sync took only X.Y.0 releases and checked daily, which a
  conditional GET makes unnecessary. The user chose this over a pin setting
  (2026-10-30): the roadmap's ansible-core detection for `alai run` would
  supersede one
  ([user-questions.md](user-questions.md#pinning-ansible-core-for-find)).
- **Warn and go on without them** when offline with nothing cached, or the
  index can't be reached, with the newest cached release when there is one
  (2026-10-30): a search of the collections is still useful, where a
  missing collection index fails. A private index without ansible-rulebook
  is the common case.
- **`eda.builtin` counts as installed**, as `ansible.builtin` does: each
  is part of the tool that runs its content, which alai doesn't look for.
  Its route steps say "with ansible-rulebook", the details' Installed line
  adds "if ansible-rulebook is in your Ansible environment, which alai
  doesn't check", as installed means Ansible sees it, not that the tool is
  in the Python environment (asked 2026-10-30), and outside a project the
  installed section is titled with the tools of the builtins it lists:
  "Installed with ansible-core and ansible-rulebook".
- **Action plugins need nothing of alai's**: the spec lists an action-only
  plugin as a `module`, and alai-core extracts it as one, so a user sees
  the name they'd write in a task.

## Not done yet

- **Full documentation** is to be a project of its own, alai-doc, kept in
  `../alai-doc` beside this repository (2026-10-25): a first version was
  built here as `alai doc`, then moved out, as most of what it needs is
  alai's core. Its README has the design and the plan: it calls alai as a
  subprocess, through `alai find --output-format json` and a new command
  printing where a collection's files are (installed, locked, cached, or
  downloaded into the cache), something like
  `alai collection path NAME --output-format json`. Keep `find`'s JSON
  stable enough for it.
- **`ansible.builtin` is the latest ansible-core release's**, not the
  installed ansible-core's, and every line is counted as installed. With an
  older ansible-core, a plugin added since is marked `✓` though it's missing
  (its `since` is its `version_added`, but there's no installed version to
  compare it with). Fixing it needs the installed ansible-core's version,
  which alai doesn't look for ([run.md](run.md#checking-ansible-core)), to
  mark it `↑`; the lines are read per release, so that release's are then
  one more cache entry.
