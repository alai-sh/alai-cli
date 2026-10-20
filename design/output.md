# Output design

How alai talks to the user and to scripts. Implemented so far: the stdout and
stderr split, `-v`/`-vv`/`-q` (`src/alai/_output.py`, `src/alai/_args.py`).
The rest is planned.

## Decisions

- **stdout carries the result, stderr everything else.** A command's output
  (the settings from `config show`, later a list or a tree) goes to stdout.
  Warnings, debug and trace lines, progress and errors go to stderr. So
  `alai … | jq` or `> file` never captures anything but the result, and no
  verbosity, colour or progress setting can change it.
- **Status lines go to stderr too**, shown unless `-q`, as uv's are: what a
  command did, as opposed to data a script would read. `alai lock` prints
  `Resolved 3 collections in 412ms`, then one line per change in uv's
  wording: `Added a.a v2.0.0`, `Updated a.a v1.0.0 -> v2.0.0`,
  `Removed c.c v1.0.0`, and `Updated a.a v1.0.0, now from <url>` when only
  the index changed. Checked against uv 0.13, which does the same on
  stderr and silences it with `-q`. Like uv, a first lock lists no
  changes, since everything would be `Added`. Unlike uv, `lock --check`
  prints the change lines before failing, and `alai.lock is up to date`
  when it passes, which an interactive run is better for; scripts use the
  exit code. Nothing goes to stdout, since there's no result to pipe;
  `_output.status()` writes them.
- **`alai sync` prints uv's install lines**: `Prepared`, `Uninstalled`,
  `Installed` or `Checked N collections in …`, then ` - ns.name==1.0.0` and
  ` + ns.name==2.0.0` per change, as `uv sync` does
  ([install.md](install.md#the-command)).
- **Colour as uv colours**, from its escape codes (uv 0.13, 2026-10-20):
  `error` bold red and `warning` bold yellow, each with a bold `:`, the
  warning's message bold; `Resolved` and `Checked` lines dim with the count
  bold; `Prepared`, `Installed` and `Uninstalled` plain with the count bold
  and `in …` dim; ` + ` green and ` - ` red, the name bold and `==version`
  dim; lock changes' verb bold green (`Removed` red). `--color
  auto|always|never`, then `NO_COLOR` (off) over `FORCE_COLOR` (on), then
  whether stderr is a terminal and `TERM` isn't `dumb`. A few lines of ANSI
  in `_output` (`styled`), not Python 3.14's `_colorize` (~12 ms to
  import). Tests run with `--color never` (`tests/conftest.py`), so an
  environment's `FORCE_COLOR` can't change them.
- **Progress as uv draws it** (`_progress`): a spinner with the phase and
  what it's on (`⠹ Resolving collections... (41) sivel.acd`), a count with
  a line per item in flight while preparing (only `downloading` and
  `extracting`; a downloaded collection waiting its turn to extract isn't
  shown, and extracting comes first, as it's one at a time), and a bar while
  installing (`███░░ [40/97] community.general==10.7.3`), each cleared for
  its summary line. Recorded from uv 0.13 and cargo 1.90 on a
  pseudo-terminal: uv redraws a block in place and keeps only summaries;
  cargo keeps a line per crate above one status line, which would put 97
  lines in the scrollback for `sivel.acd`. A ticker thread redraws at
  10 Hz, the first frame one tick in, so a phase quicker than that draws
  nothing; lines are cut a column short of the terminal's width, as a
  wrapped line throws off moving back up. Messages written meanwhile
  (warnings, `-v`) clear the block, print, and redraw it. Drawn only on a
  terminal that isn't `TERM=dumb`, never with `-q`, off with
  `--no-progress` or `ALAI_NO_PROGRESS`. No Windows console handling:
  alai doesn't run on Windows, as Ansible's control node doesn't. Not rich: one block of lines is cursor
  movement and clearing, a couple of hundred lines here, against rich's
  ~50 ms import and its dependencies.
- **`-v` says why each change happened**, indented under it with no
  `debug:` prefix (`_output.detail()`), so the action and its reason read
  as one item and are easy to tell apart; uv doesn't give reasons. Asked for after a pin on one collection inside a
  bundle that pins everything exactly (`sivel.acd`, the Ansible package as
  a collection) moved the bundle and ~90 collections back with no
  explanation. The reasons are worked out by the resolver
  ([resolver.md](resolver.md)).
- **Paths under the home directory are shown with `~`** in `config show`
  and in errors naming the project's directory (`no project found from
  ~/ansible`), through `_output.home_path()`, so output reads the same in
  the docs' recordings, which run in a temporary home, as on a user's
  terminal. uv's equivalent error names no path at all; alai's does, as
  `--project` can start the search somewhere other than the current
  directory.
- **Two verbosity levels, each with a meaning**, as uv has, rather than
  Ansible's six loosely defined ones:

  | Flag | Shows |
  |---|---|
  | `-q` | errors only |
  | none | results, warnings and errors |
  | `-v` | why each change happened, indented under it, and `debug:` lines, what alai decided and why: which config files were read, which index a collection came from, a cache hit or miss |
  | `-vv` | `trace:` lines as well, each step: every HTTP request and file operation, with timings |

  More than two is an error, not silently the same as `-vv`, so a later
  level could be added without changing what anyone's `-vvv` already does.
  If trace output needs filtering by area, that's an environment variable
  (`ALAI_LOG`, like uv's `RUST_LOG`), not more `v`s. `-q` and `-v` together
  are an error.
- **`-v` counts across levels.** argparse copies a subcommand's parsed
  options over the parent's, so a single `verbose` count would make
  `alai -vv config show -v` verbosity 1. Each parser counts under its own
  dest and `verbose_count()` adds them up.
- **No logging module, and no rich.** `logging` costs ~14 ms to import on
  every command. rich costs ~20 ms for `rich.console` alone and ~50 ms with
  `rich.table` and `rich.progress` (Python 3.15, warm cache; ~80 ms cold).
  alai's tables are aligned columns, which `config show` already does in a
  few lines, and a spinner or progress line is a short loop writing `\r` to
  a terminal. Revisit rich only if the output needs something genuinely hard,
  such as several live progress bars at once, and then import it only in the
  command that draws them.

## Planned

- **JSON per command**, as `--output-format json` (uv's and cargo's style),
  only on commands that produce data: `config show`, and later listing
  installed collections, a dependency tree, outdated checks. The JSON is a
  contract: it carries a version field or has a written schema, and its field
  names don't follow changes to the human-readable text. Warnings, progress
  and errors stay on stderr as text, and the exit code stays the signal for
  failure.
- **`-qq` for no output at all**, errors included, as uv has. Not needed
  until someone asks; the exit code already says whether it worked.
- **Machine-readable errors** in the JSON output. Only if a tool needs more
  than the exit code and the stderr text.
