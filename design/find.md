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
  `collections_dir`, with its version from `MANIFEST.json`, found the way
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
- **Removed plugins are listed only when named exactly** (a whole name or a
  FQCN), as looking one up is how to find where it went;
  `--include-removed` lists those matching part of a name too. This holds
  in the Installed section as well: an installed collection's removed
  plugin is listed when named, marked removed, since the user may be using
  it.
- **Nothing found is a warning and exit 1**, as `uv pip show`, with up to
  three close names from `difflib` (`did you mean 'docker_container'?`).
  No configured index with a plugin index is an error (exit 2).

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
  place of its description (`added in 3.9.1, installed 3.8.0`,
  `removed in 2.0.0; → community.docker.docker_container`).
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
8. status, then collection, type and name.

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
  use fully qualified collection names".
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
installed, as only the index's latest facts were used. What that older
version's own routing said is unknown, as the index only has the latest.

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
into `ansible.builtin`, which the index lacked until it was synced.

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
- **Routing from installed collections.** Routing comes from the index,
  so from each collection's latest version; an installed older version
  may route differently. Reading `meta/runtime.yml` from disk for installed
  collections would fix it. A documented limitation for now (2026-10-21).
- **Installed collections that aren't on any index** (source checkouts,
  path, tarball and git sources), and a collection project's own
  collection, have no plugins listed. A documented limitation. The plan
  (2026-10-24, weighed with the user and left for later), which takes
  alai-plugin-extractor as a new dependency, or its code moved into alai:
  - **List each local collection's plugins** with the extractor: installed
    ones and the collection project's checkout, their names, types, short
    descriptions and routing. Listing costs about what reading the docs
    does, as parsing the Python is most of it (measured 2026-10-24:
    `community.general` 0.9 s, `fortinet.fortios` 1.5 s, `ansible.posix`
    0.05 s), against `find`'s 0.1-0.3 s.
  - **Cache just those fields**, per collection, keyed by the installed
    `MANIFEST.json`'s SHA-256: a cache of everything extracted, docs
    included, would cost 10-30 ms a collection to load on every `find`. A
    checkout has no `MANIFEST.json`, so key it by its plugin files' sizes
    and modification times, or working in a large one (`community.general`
    itself) costs about a second every time.
  - **A second, small source in `Catalog`**, in Python beside the native
    scan of `plugins.jsonl`, with the same search interface. A local
    collection's lines replace the index's for it, as the installed
    version is what runs, which also fixes routing from installed
    collections (above).
  - **Fit `find`'s rules**: ranking and markers read `since` and `until`,
    so a local line's `since` is the installed version. On Python 3.10 and
    3.11, which the extractor doesn't run on, local collections are
    skipped, with a dim line saying so.
  - The first `find` in a project with many installed collections reads
    each once, several seconds where `find` now takes a fraction of one,
    so it shows progress; after that, a few milliseconds each.
  About 200-300 lines, and as much again in tests and docs.
- **`ansible.builtin` is one ansible-core release's**, the one the index
  was generated from (the spec says one release, and `plugins.jsonl`
  doesn't say which), not the installed ansible-core's, and every line is
  counted as installed. With an older ansible-core, a plugin added since is
  marked `✓` though it's missing (its `since` is its `version_added`, but
  there's no installed version to compare it with); with a newer one,
  plugins added after the index's release aren't listed at all. Fixing it
  needs the installed ansible-core's version, which alai doesn't look for
  ([run.md](run.md#checking-ansible-core)), to mark the first `↑`; the second
  needs the index to carry builtins per release, or the release it used,
  which is for the spec.
