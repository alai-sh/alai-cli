# Documentation design

## Decisions

- **`docs/` describes only what's implemented**, so users never read about a
  feature that doesn't exist yet. Plans and rationale live here in `design/`,
  which isn't published. The one exception is `docs/roadmap.md`, a short,
  user-facing summary of what's planned, under a warning that none of it
  works yet (asked for 2026-10-21). It lists the roadmap in
  [README.md](README.md) and the larger "Not done yet" items in no
  particular order, and an item moves out of it into the rest of the docs
  when it's built.
- **The landing page installs from GitHub until alai is on PyPI** (asked
  for 2026-10-21): `uv tool install` of the `main` branch's archive,
  `https://github.com/alai-sh/alai-cli/archive/refs/heads/main.tar.gz`,
  under a note that it's temporary. Tried with uv 0.13.0 on 2026-10-21:
  it installed and ran, but `uv tool upgrade alai` said "Nothing to
  upgrade" for a URL source, so the page says to update with `--reinstall`.
  At the first PyPI release, replace both with `uv tool install alai` and
  `uv tool upgrade alai`, and drop the note, here and in `README.md`.
- **`README.md` is a short landing page** (asked for 2026-10-21): what alai
  is, why, installing, a first project with real output, common tasks, and
  links into the docs. It's also the PyPI description, so its links are
  absolute, and the preview warning is a plain blockquote rather than
  GitHub's `> [!WARNING]`, which PyPI doesn't render. Its output is static,
  from a run against `index.alai.sh`, not a recording; its speed figures are
  the landing page's.
- **A page per concept that grows, starting with sources** (asked for
  2026-10-22): `docs/collection-sources.md` describes each kind of
  `[project.sources]` entry, why you'd use it, where it falls short, and a
  recording of adding or installing from it, so git and tarball sources
  each become a section there. `alai-toml.md` keeps the reference table,
  and `managing-collections.md` only how `alai add` and `alai remove`
  handle a directory.
- **An acknowledgements page** (asked for 2026-10-21), last in the nav:
  the projects alai is modelled on (uv, ModuleFast, Cargo's registry index,
  PwshSpectreConsole for the recorded docs) and the ones it's built with.
  Add to it when a dependency or a borrowed idea is.
- **Zensical over Sphinx.** There's no public Python API to autodoc, and
  Zensical needs little maintenance.
- **Plain Markdown plus admonitions**, so the docs could move to another
  generator without a rewrite. The recordings are links with a class
  (`attr_list`), so even they read as Markdown. So are the coloured markers
  in [finding-plugins.md](../docs/finding-plugins.md#what-the-markers-mean)
  (asked for 2026-10-21, to match what `alai find` prints): code spans
  with a class from `docs/assets/ansi.css`, `` `✓`{ .ansi-green } ``, in
  the recordings' palette, the same trade as the recordings: a generator
  without `attr_list` would show `{ .ansi-green }` as text.
- **Deployed to GitHub Pages on every push to `main`** that could change the
  docs (`.github/workflows/docs.yml`), not tied to a release. This is fine
  while alai is pre-release. At the first real release, decide whether docs
  follow `main`, follow releases, or are versioned. The workflow runs on a
  change to anything but what can't affect the docs (`design/`, editor and
  repo files), since the CLI reference and recordings come from `src/`,
  `scripts/` and the index helpers in `tests/`; the rest of `tests/` isn't
  excluded, as one more helper would be easy to miss. A list of what to
  include would miss whatever they come to use, and a check that doesn't run
  fails silently; one that runs needlessly costs half a minute.

## The CLI reference

`docs/cli.md` has a section per command: its description, recordings of it
running, its usage, and a table of its arguments and options by argparse
group. The global options, which every command takes, are listed once.

- **Generated from the parser, as uv does.** uv's `docs/reference/cli.md` is
  written from its clap definitions by `cargo dev generate-cli-reference`,
  committed, and CI fails with a diff when it's out of date (read in uv's
  `crates/uv-dev/src/generate_cli_reference.rs`, 2026-10-20).
  `scripts/cli_reference.py` does the same from `_cli._build_parser()`:
  `--check` in the `docs` tox env, and without it to rewrite the page. The
  help text in the command modules is the one source; the page is never
  edited by hand. It's committed, unlike the recordings, so a help change
  shows up in review as a change to the page.
- **One page, as uv has**, not a page per command. Its table of contents
  is the list of commands. Options have no anchors of their own, since
  Markdown tables can't carry them; links go to a command's section.
- **Help text becomes sentences**: the first letter capitalised, and
  options and environment variables put in code. The usage line is
  `alai <command> [OPTIONS]` plus the positional arguments as `--help` writes
  them, not argparse's full list of every option, which repeats the tables
  and runs to 11 lines for `sync`. What only that list showed, options that
  can't be used together (`[--locked | --frozen]`), is added to each one's
  description from argparse's mutually exclusive groups.

## Terminal recordings

Each recording shows commands being typed and their real output, colour and
progress included, as [asciinema](https://asciinema.org/) v2 files played by
asciinema-player. The idea comes from PwshSpectreConsole's docs, which run
each example from the help and record it with Spectre.Console's recorder.

- **Scenarios in `scripts/cli_recordings.toml`**, apart from where they're
  shown: each `[[recording]]` has a name, a title, where it starts, files to
  start with, built collections' tarballs to start with (made with the
  tests' `build_artifact`, as no `ansible-galaxy` is installed), hidden
  setup commands, and the commands typed. One
  `[[reference."<command>"]]` table per recording the CLI reference shows
  under a command, in order, names it and gives an optional description for
  that place. So one recording can be shown under several commands, each
  with its own words, or only on a guide page, linked as
  `[Title](casts/<name>.cast){ .alai-cast }` with the guide's own text
  around it (2026-10-21; each recording named its one command and carried
  its description before). The title stays with the recording, as the
  player shows it wherever it's used. `cli_reference.py` fails on a
  `[reference]` entry naming a command or recording that doesn't exist, and
  on a recording used nowhere, neither in `[reference]` nor linked from a
  page other than the generated one. A description's paragraphs follow the
  recording's link, each marked `{: .alai-cast-description }`, and
  `cast.js` moves them between the title and the player; without
  JavaScript they read as paragraphs under the link. Paragraphs only, as
  `attr_list` can't mark a list.
- **Recorded on every docs build**, by `scripts/cli_recordings.py` in the
  `docs` tox env, into `docs/casts/` (ignored by git). A recording can't go
  stale, and there's nothing to refresh by hand. In VS Code, `docs: serve`
  runs the `docs: generate` task first, which writes the reference and the
  recordings. They run at once, each with its own home and cache, so the
  cost is about three seconds per build, the longest recording, and timings
  that differ between builds, which they would on a reader's machine too.
  Considered: committing the recordings and checking them in CI with the
  timings normalised, which shows changes in review but is one more thing to
  keep in step.
- **Against a local index**, not index.alai.sh: the test index and artifact
  builders (`tests/index_data.py`, `tests/index_server.py`) serve the
  collections listed in the TOML, with versions taken from Galaxy so the
  output reads as it would for real. The build works offline and doesn't
  move as Galaxy does. The server waits 0.15 s per index request and 0.4 s
  per download, so the progress shows as it would over a network; without
  it everything finishes before the first frame is drawn.
- **A recorder of our own**, about 200 lines on the standard library: each
  command runs in a pseudo-terminal, with an empty home,
  config and cache, so alai colours and draws progress as it would for a
  user and nothing from the machine building the docs shows. `config show`
  shortens the home directory to `~`, so the temporary paths don't show
  there. The prompt shows the working directory as a shell's would
  (`~/project $`), so a recording can show where alai runs from: each
  starts in `~/project` or the `directory` it names, and a `cd` among its
  commands is followed by the recorder, since each command runs in a shell
  of its own. A command the shell can't find (exit 127, such as `tree`
  missing on the machine building the docs) fails the build rather than
  being recorded; any other failure is recorded, as some recordings show
  errors on purpose. The typing is drawn at a steady 40 ms a character; the
  output keeps the time it really took. The file's height is cut to the rows the recording
  reaches, following newlines, lines wider than the terminal wrapping onto
  the next row (escape sequences and OSC 8 links taking no room), and the
  cursor moving up as progress redraws; counting newlines alone made the
  player too short when a long path wrapped. Recordings run in an 80 by 48 terminal, and one that reaches
  past row 48 fails the build, as its first lines would scroll away
  before the end (raised from 40 when `find-search`'s first command
  scrolled off, 2026-10-21). The width was 100 until 2026-10-21: the
  player fits each recording to the page's width, so 100 columns made the
  text too small to read comfortably. A recording can set `columns`; the
  `alai find` tables keep 100, as at 80 they cut descriptions and redirect
  targets short, so their text is smaller than the rest. Considered: the asciinema CLI (another tool to install, and
  recording a whole shell session makes typing and setup harder to
  control) and vhs (needs ttyd and ffmpeg, and makes GIFs or videos, which
  can't be selected or themed).
- **asciinema-player 3.17.0, vendored** in
  `docs/assets/asciinema-player-3.17.0/` (the bundle's minified script, its
  CSS and the Apache-2.0 licence, from the npm package), so the site loads
  nothing from a CDN. Update it by replacing the directory and the two
  paths in `zensical.toml`.
- **`docs/assets/cast.js` turns each link into a player**, written in a
  page as `[Title](casts/name.cast){ .alai-cast }`. Without JavaScript, the
  link to the file stays. A player starts once half of it is scrolled into
  view, and plays once, as PwshSpectreConsole's do; with reduced motion
  asked for, it doesn't start, and shows how the recording ends. The
  terminal uses the page's code font and colours, with ANSI colours from
  GitHub's light and dark terminal palettes (`docs/assets/cast.css`), which
  switch with the page's scheme. `check_doc_links.py` checks a recording's
  link points at a file that exists.

## Not done yet

- **More recordings in the guides.** `managing-collections.md` and
  `installing-by-hand.md` link the reference's; a guide can have its own by
  leaving it out of `[reference]`. The output examples left as `console` blocks (in
  `managing-collections.md`'s locking section, and `configuration.md`) need
  scenarios that change a lock over time or set up several config files.
- **`generate-shell-completion` has no recording**: it prints a script.
- **`config show` prints the local index's address**,
  `http://127.0.0.1:<port>/`, from `ALAI_DEFAULT_INDEX`. Accurate, though not
  what a user would see.
