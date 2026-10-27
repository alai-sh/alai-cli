# alai run and alai env

**Status: implemented** (2026-10-21). `alai run COMMAND` runs a command so
that Ansible sees the project's collections and nothing else; `alai env`
prints the same setup for a shell, a CI step or an editor to use.
Collection projects, which `alai run` serves too, are in
[collection-development.md](collection-development.md).
The code is `_commands/run.py`, `_commands/env.py` and `_environ.py`; the
user docs are [docs/running-ansible.md](../docs/running-ansible.md).

## Decisions

Taken with the user on 2026-10-21; [What uv run does](#what-uv-run-does)
fills in the details.

- **Bring your own environment.** alai doesn't manage Python or
  ansible-core, and doesn't detect or hand off to uv, Poetry or a
  virtualenv tool. Trying to combine with what each of those does would
  make alai worse at its own job. `alai run` runs the command in the
  environment it was started from, plus alai's variables, and should work
  there without setup as far as it can. Managing ansible-core
  ([environments.md](environments.md)) stays possible later but isn't
  planned.
- **alai doesn't share Ansible's environment.** The expected install is as
  a tool (`uv tool install alai`, pipx), in its own environment, though
  installing it next to Ansible must work too. So alai never imports
  ansible-core, and leaves [checking its version](#checking-ansible-core)
  to ansible-core.
- **uv projects nest.** A directory with both `pyproject.toml` (Python and
  ansible-core through uv) and `alai.toml` (collections) needs nothing
  special: `uv run alai run ansible-playbook …` or `alai run uv run
  ansible-playbook …`. Whether that pairing deserves documenting as a
  recommended setup is open.
- **Environment variables only.** Everything `alai run` does to the child
  must be expressible as environment variables, so `alai env` can print it
  and `eval "$(alai env)"`, direnv, mise, a CI step or an editor's env file
  get the same result. If a need turns up that isn't (a working directory
  ansible-test needs, say), that's a design problem to solve, not a reason
  for `alai run` to do more than `alai env` can say. No activation script.
  A variable that extends one already set, such as `PATH` with a managed
  Python environment's `bin` in front ([environments.md](environments.md)),
  is printed as its whole value, worked out from the environment `alai env`
  runs in, with alai's entry taken out first so evaluating it twice doesn't
  add it twice (decided with the user, 2026-10-25). Every output format
  then says the same, where `$PATH` would be expanded by shells but not by
  `$GITHUB_ENV`, and only by some editors' env files. For `eval` and direnv
  it's no different; a `dotenv` file is a snapshot of the `PATH` it was
  written with, which the docs must say once alai prints one.
- **`alai run` syncs first**, as `uv run` does, with an opt-out
  (`--no-sync`, and `--locked`/`--frozen` as elsewhere). `--no-sync` means
  what it means to uv on each command: on `add` and `remove`, lock but
  don't install (`uv add --no-sync` locked); on `run`, neither lock nor
  install (`uv run --no-sync` left a stale `uv.lock` alone; both uv
  0.13.5, 2026-10-23). One difference: `alai run --no-sync --locked` (or
  `--frozen`) is a usage error, where uv accepts the pair and `--locked`
  then checks nothing (`uv run --no-sync --locked` ran with a stale lock
  and exited 0), so a CI job that wrote them together would think it was
  checking the lock.
- **Editor setup is printed, never written.** alai doesn't edit
  `.vscode/settings.json`, `launch.json` or an env file: merging into
  existing files, or replacing them, is the user's call. It prints what to
  paste.
- **Debugging modules must stay possible.** Controller-side code
  (plugins, the `module_utils` they import) comes first, but nothing in the
  layout or the variables may rule out breakpoints in modules, which
  AnsiballZ packages and runs in another process
  ([collection-development.md](collection-development.md#debugging)).
- **Other tools aren't targets, but shouldn't break.** ansible-lint,
  molecule, the Ansible language server and VS Code extension, and
  ansible-navigator aren't designed for, but many users run them, so each
  design is checked against them where it's cheap to. ansible-lint installs
  collections itself and sets its own collections path, so it's the most
  likely to clash.

## What uv run does

Tried with uv 0.13.0 on 2026-10-21, in a scratch project:

- **Syncing.** `uv run` locks and syncs first, printing less than `uv
  sync`: only what it creates and `Installed 3 packages in 3ms`, no
  `Resolved` line or package list, and nothing at all when there's nothing
  to do. `-q` hides all of it. The sync is inexact: extra packages stay
  unless `--exact` is given.
- **The flags** mean what they mean for `uv sync`:
  - `--locked` exits 1 when the lock would change.
  - `--frozen` installs from the lock as it is.
  - `--no-sync` (`UV_NO_SYNC`) touches nothing and implies `--frozen`.
    With no `.venv` it still creates an empty one.
  - `--locked --frozen` is a usage error. `--no-sync --locked` is accepted
    silently.
- **The child's environment** differs from the parent's in four variables:
  - `PATH` with `<venv>/bin` prepended;
  - `VIRTUAL_ENV`;
  - `UV`, the path to uv;
  - `UV_RUN_RECURSION_DEPTH`, which counts nested runs.
- **The working directory is kept.** The project is found from parent
  directories. `--directory DIR` changes into DIR first. `--project DIR`
  only changes where the project is looked for.
- **Arguments.** Everything after the command is the command's, so no `--`
  is needed, and uv's own options come before the command.
- **The process.** uv stays the parent rather than exec'ing. It forwards
  SIGINT, SIGTERM and SIGHUP, and exits with the child's code, or 128+n
  after signal n.
- **Errors.** A command that isn't found exits 2 with `error: Failed to
  spawn: nosuchcmd`, then `  cause: No such file or directory (os error
  2)`. Bare `uv run` exits 2 and lists the commands available.
- **No shell setup.** uv has no `env`, `activate` or `shell` command.
  `uv venv` ends with `Activate with: source .venv/bin/activate`, choosing
  `activate.fish` when `SHELL` is fish. Poetry, PDM, Hatch, mise and direnv
  weren't installed to compare.

What alai takes from it:

- **The same flags, quiet syncing** and passing everything after the
  command through. The flags are `--locked`, `--frozen` and `--no-sync`,
  which can't be combined, and the global `--project`. `--directory`
  isn't done yet.
- **Inexact, with a warning** (decided with the user, 2026-10-21). As
  with `uv run`, the sync installs what's missing or wrong and leaves
  collections the lock doesn't list, as someone may be working on a
  collection before adding it. But Ansible will load them, so the lock no
  longer describes everything that runs
  ([README.md](README.md#principles)): `alai run` warns, naming each one,
  then runs. `--exact` removes them first, as `alai sync` does, and
  `--inexact` (added 2026-10-21, as `uv run` has it, hidden there since
  it's uv's default) keeps them without the warning; `-q` would hide every
  other warning too. Exact by
  default was considered and rejected, as it deletes work in progress
  without asking, and so was an error, as the case it's for then needs a
  flag every time.
- **Exec, not a parent**, unlike uv (decided with the user, 2026-10-21).
  alai doesn't run on Windows, which has no exec, so once the sync is
  done it replaces itself with the command (`os.execve`, after finding it
  on the child's `PATH` with `shutil.which`, so a missing command is
  alai's error rather than the exec's). The
  command then gets signals, the terminal and job control directly, and
  its exit status is the run's, with nothing of alai's to forward or clean
  up. The cost: alai can't do anything after the command ends, which
  nothing planned needs. Output must be flushed before the exec. uv likely
  stays a parent for cleanup too, not only Windows: `uv run --with` and
  `--isolated` build a temporary environment in its cache that has to go
  once the command ends (not confirmed from uv's source). A `--with` for
  alai would need the same, and so a parent.
- **Messages of alai's own.** They don't have to match uv's wording, only
  say clearly what went wrong. A command that isn't found is an error
  before the exec, naming the command, rather than a traceback. Bare
  `alai run` is a usage error; listing the commands available, as uv's
  does, isn't done.
- **Variables:** `ANSIBLE_COLLECTIONS_PATH` and
  `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False`. uv's `UV` and
  `UV_RUN_RECURSION_DEPTH` have no counterpart: uv counts nested runs as
  their parent, and with exec there's no parent. Collection projects add
  `ANSIBLE_TEST_CONTENT_ROOT` and `PYDEVD_RESOLVE_SYMLINKS=1` (see
  [collection-development.md](collection-development.md)).
- **A project is required.** `uv run` runs outside a project, but without
  one alai has no collections directory to point Ansible at, so both
  commands fail without an `alai.toml`, `--no-sync` included, with the hint
  every command has (see [configuration.md](configuration.md#decisions)).
  What `uv run` does outside a project is mostly `--python`'s, picking an
  interpreter to run with, and alai has no equivalent for Python or
  ansible-core yet, so there's nothing for it to do there (2026-10-27).
- **The sync reports briefly**, as `uv run`'s does: no `Resolved` or
  `Checked` line, only the phases that changed something, and each
  collection's line only with `-v`.
- **`alai env`** has no uv command to copy. It prints POSIX `export`
  lines, or fish's `set -gx` when `SHELL` is fish, as `uv venv` chooses
  its hint; `--output-format pwsh`, named after PowerShell's binary on Linux and macOS, prints `$env:` assignments (added 2026-10-22, as completions cover PowerShell too). It's only explicit (decided 2026-10-22): PowerShell on Unix leaves `SHELL` as the login shell; `PSModulePath`, which it sets, is inherited by every process under it, a bash started from pwsh included; and the parent process's name (`/proc/<ppid>/comm` on Linux, `ps` or libproc on macOS) is right only without anything between, such as `uv run` or `sudo`. Checking the name only when `PSModulePath` is set would avoid false positives, but still miss those cases, at the cost of per-platform code), and `--output-format dotenv` prints `KEY=VALUE` lines for editors'
  env files (VS Code's `python.envFile`) and `$GITHUB_ENV`. It never
  syncs, so direnv can run it on every `cd` without reaching an index,
  but it warns, from the lock and the directory alone, when the lock is
  missing or stale, a collection the default groups need isn't installed,
  or something the lock doesn't list is there.

## Checking ansible-core

**Decided (2026-10-21): alai doesn't check the ansible-core version; Ansible
does.** ansible-core checks each collection's `requires_ansible` as it loads
it (`_on_collection_load_handler` in `lib/ansible/plugins/loader.py`),
warning by default, and failing with
`ANSIBLE_COLLECTIONS_ON_ANSIBLE_VERSION_MISMATCH=error` or the
`collections_on_ansible_version_mismatch` setting in `ansible.cfg`. alai
leaves that setting to the user. So `alai run` and `alai env` never run or
look for ansible-core. That avoids finding the right `ansible` from outside
its environment, waiting for it and parsing its output, all for a check
Ansible makes anyway. What's lost is a warning before a long run starts,
and a check of collections the run never loads. Collections in
`ignore_requires_ansible` still get ansible-core's warning, which that
setting silences.

What was tried before deciding, on 2026-10-21 with ansible-core 2.21.5:

- **Where `ansible` comes from varies by install.** After `uv venv` and
  `uv pip install` it's a script whose shebang is the venv's `python`. With
  `--relocatable` it's a `/bin/sh` trampoline. `uv tool` and pipx link it
  into the tool's venv. `hacking/env-setup` links it to
  `lib/ansible/cli/adhoc.py`, with no dist-info.
- **`ansible --version`** took 190 ms, and was the only way that covered
  every install. A broken `ansible.cfg` printed an error and still exited
  0. A venv on a Python that ansible-core doesn't support failed with a
  traceback.

## Open questions

- **`alai env --output-format json`**, for scripts, if anyone needs it.
- **`ALAI_NO_SYNC`**, as uv has `UV_NO_SYNC`: not done, as it needs a
  setting only one command reads.
- **Requiring the ansible-test fixes.** Collection projects use the one tree
  in [collection-development.md](collection-development.md#a-collection-checked-out-anywhere)
  (decided 2026-10-21), so `units --local` and `--venv` need the
  ansible-core release with the
  [upstream fixes](collection-development.md#upstream-fixes). alai doesn't
  check for it, as it doesn't [check ansible-core](#checking-ansible-core)
  at all. ansible-test's own errors on older releases (`not found in
  collection path`, `FATAL: The current working directory must be within
  the source tree being tested.`) don't say why, so the docs name the
  release and those errors once the fixes are released.
- **`<playbook dir>/collections`**, which `ansible-playbook` searches
  whatever `ANSIBLE_COLLECTIONS_PATH` says (`cli/playbook.py` sets
  `AnsibleCollectionConfig.playbook_paths` unconditionally). Documented as
  the one way around the lock for now; nothing alai can set turns it off.

## Next

- **`alai env --locked`**, exiting 1 where `alai env` now only warns: no
  `alai.lock`, one that's out of date, or locked collections not installed
  (decided with the user, 2026-10-23). `alai env` keeps exiting 0 by
  default: its output is right whatever the lock says, and in `eval
  "$(alai env)"` the shell drops the status anyway, so a non-zero default
  would only break scripts that redirect it. The check is what `alai sync
  --check` ([install.md](install.md#not-done-yet)) answers for CI, so the
  two should share it.
- Propose the [upstream fixes](collection-development.md#upstream-fixes)
  to ansible-test.
- Check ansible-lint and the VS Code Ansible extension against
  `alai env`'s variables.
