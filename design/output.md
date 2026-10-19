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
- **Two verbosity levels, each with a meaning**, as uv has, rather than
  Ansible's six loosely defined ones:

  | Flag | Shows |
  |---|---|
  | `-q` | errors only |
  | none | results, warnings and errors |
  | `-v` | `debug:` lines, what alai decided and why: which config files were read, which index a collection came from, a cache hit or miss |
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
- **Colour** with `--color auto|always|never`, plus `NO_COLOR` and
  `FORCE_COLOR`. `auto` colours a stream only when that stream is a terminal.
  A small ANSI helper in `_output`; not Python 3.14's `_colorize`, which costs
  ~12 ms to import (see `HelpFormatter` in `src/alai/_args.py`).
- **Progress** (spinners, download bars) on stderr, drawn only when stderr is
  a terminal, off with `-q` and with a `--no-progress` flag (and
  `ALAI_NO_PROGRESS`) for CI logs that are terminals.

### Progress on stderr and PowerShell

Writing progress to stderr is what uv, cargo, pip, curl and git do. Checked
with PowerShell 7.6 on Linux, running a child process from a terminal:

| PowerShell | Child sees | Captured in `$res` |
|---|---|---|
| `$res = alai …` | stderr is a terminal, stdout isn't | stdout only; stderr goes straight to the console |
| `$res = alai … 2>&1` | neither is a terminal | stdout lines as strings, stderr lines as `ErrorRecord` objects |
| `$res = alai … 2>$null` | stdout isn't a terminal | stdout only |

So `$res = alai …` shows the spinner and captures only the result, and once
stderr is redirected alai sees no terminal and draws nothing. Drawing only
when stderr is a terminal is what keeps `2>&1` output free of `\r` noise, and
keeps the stderr lines that do appear (warnings, errors) one record each.
Windows PowerShell 5.1 treats redirected stderr more harshly (it can stop a
script under `$ErrorActionPreference = 'Stop'`); not checked, and less likely
to matter since Ansible doesn't run on a Windows control node.

## Open questions

- **`-qq` for no output at all**, errors included, as uv has. Not needed
  until someone asks; the exit code already says whether it worked.
- **Machine-readable errors** in the JSON output. Only if a tool needs more
  than the exit code and the stderr text.
