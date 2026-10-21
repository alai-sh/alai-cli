# Editors

This page covers setting up editors to run and debug Ansible against a
project's collections, one section per editor. So far it covers Visual
Studio Code.

!!! note "Expect some rough edges"

    Running and debugging Ansible from an editor involves the editor,
    Python's debugger, ansible-core, ansible-test and alai, so it's a
    complex area. What's here should work, but don't be surprised if
    something doesn't. Please report any problems in the
    [issue tracker](https://github.com/alai-sh/alai-cli/issues), so the
    docs can cover them and alai can fix what it can.

When an editor runs or debugs Ansible, it needs to set the same
environment variables as [`alai run`](running-ansible.md), so that Ansible
only loads the project's collections. alai doesn't change editor settings
itself, as it would have to merge them into files you may already have.
Instead, `alai env --output-format dotenv` writes the variables as
`KEY=VALUE` lines, which most editors can load into what they run.

## Visual Studio Code

The debugger launches Python code in the Python environment ansible-core
is installed in, and alai, installed as a uv tool, isn't in that
environment. So the launch runs Ansible's own Python, with alai's
variables loaded from a file a task writes first.

### Running a playbook under the debugger

`.vscode/tasks.json` installs the project's collections and writes the
variables before each run:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "alai env",
      "type": "shell",
      "command": "alai sync && alai env --output-format dotenv > ${workspaceFolder}/.vscode/alai.env",
      "presentation": { "reveal": "silent" }
    }
  ]
}
```

`.vscode/launch.json` runs `ansible-playbook` with them, under the Python
debugger:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "ansible-playbook site.yml",
      "type": "debugpy",
      "request": "launch",
      "module": "ansible",
      "args": ["playbook", "site.yml", "-i", "inventory.ini"],
      "cwd": "${workspaceFolder}",
      "envFile": "${workspaceFolder}/.vscode/alai.env",
      "preLaunchTask": "alai env",
      "console": "integratedTerminal"
    }
  ]
}
```

- `"module": "ansible"` runs ansible-core's `python -m ansible`, whose
  first argument names the command: `playbook` for `ansible-playbook`,
  `adhoc` for `ansible`, `doc` for `ansible-doc`, `inventory` for
  `ansible-inventory`, and so on.
- In a [collection project](projects.md#collection-projects), give a
  playbook in the collection by its fully qualified name rather than its
  path, such as
  `"args": ["playbook", "my_ns.my_col.site"]`, so its short names for the
  collection's content resolve; see
  [In a collection project](running-ansible.md#in-a-collection-project).
- It runs with the Python interpreter selected for the workspace, which
  must be the one ansible-core is installed in, such as the project's
  `.venv`. Set `"python"` in the configuration to choose another.
- `alai env` doesn't install anything, so the task runs `alai sync` first.
  The file holds paths true of one machine: add `.vscode/alai.env` to
  `.gitignore`.
- To step into ansible-core's controller code, add `"justMyCode": false`.
  Modules aren't debugged by this launch; see
  [What breakpoints hit](#what-breakpoints-hit).

### Attaching to `alai run`

To start Ansible with `alai run` instead, have it run Python under
debugpy, waiting for the editor:

```console
$ alai run python -m debugpy --listen 5678 --wait-for-client -m ansible playbook site.yml -i inventory.ini
```

then attach to it with a configuration such as:

```json
{
  "name": "Attach to alai run",
  "type": "debugpy",
  "request": "attach",
  "connect": { "host": "localhost", "port": 5678 }
}
```

`python` there is whichever is first on `PATH`, so activate the
environment ansible-core is in first, and install debugpy into it.

### What breakpoints hit

Breakpoints in code that runs in the `ansible-playbook` process hit, such
as filter, lookup, action, callback and inventory plugins, and the
`module_utils` they import. Breakpoints in modules don't, as Ansible runs
each module in a process of its own.

To debug modules, use
[ansible-test](#debugging-with-ansible-test), which has
modules connect back to the debugger.

In a [collection project](projects.md#collection-projects), Ansible loads
the collection through a link in its collections directory, as it does a
collection from an
[editable directory](collection-sources.md#an-editable-directory).
Breakpoints set in your checkout still hit.

The call stack shows the file's path in your checkout when you use
`alai run` or `alai env`. Due to current limitations, when using
`ansible-test`, it shows the path in the collections directory instead.
Module breakpoints have a further limit there, described in
[Debugging with ansible-test](#debugging-with-ansible-test).

### Debugging with ansible-test

Run under the debugger with `--dev-debug-on-demand`, ansible-test has the
Ansible commands it starts, and the modules they run, connect back to the
debugger. The launch is the same for each command, with its own `args`:

```json
{
  "name": "ansible-test shell",
  "type": "debugpy",
  "request": "launch",
  "module": "ansible",
  "args": ["test", "shell", "--dev-debug-on-demand"],
  "envFile": "${workspaceFolder}/.vscode/alai.env",
  "preLaunchTask": "alai env",
  "console": "integratedTerminal"
}
```

With ansible-core 2.21 and older, install `debugpy` into the environment
ansible-core is in. Later versions use the copy bundled with VS Code.

In a collection project, where a breakpoint has to be set depends on the
command:

| `args` | Use it for | Controller code, such as action and filter plugins | Modules |
|---|---|---|---|
| `test shell` | Running Ansible commands by hand on this machine | Set in your checkout | Set in your checkout, when the command is run with `alai run` |
| `test integration` | Debugging an integration target as it runs | Set in your checkout | Doesn't work yet: run the module from `test shell` with `alai run` instead |
| `test shell`, `test integration` with `--docker` or `--remote` | The same, in a container or on another host | Set in the collections directory | Doesn't work yet |
| `test windows-integration`, `test network-integration` | Integration targets on Windows or network hosts | Set in your checkout | Not debugged: ansible-test doesn't connect these hosts to the debugger |

Commands with `--venv`, and `test units --local`, don't work in a
collection project; see
[Running Ansible](running-ansible.md#in-a-collection-project).

!!! warning "Module breakpoints and delegated commands"

    ansible-test tells the debugger where the collection's files are by
    their paths in the collections directory, not in your checkout. Where
    ansible-test runs the modules itself, alai has no part in it and can't
    correct those paths, so module breakpoints don't hit: in
    `ansible-test integration`, and in anything run with `--docker` or
    `--remote`. This needs a change to ansible-test. Until then, debug a
    module from [`ansible-test shell`](#ansible-test-shell). It opens an
    interactive shell in the terminal, set up as for the collection's
    tests, where you can run any command. Run the module there with
    `alai run`, such as
    `alai run ansible localhost -m my_ns.my_col.my_module -a name=value`.

    With `--docker` or `--remote`, the same applies to the controller's
    code, which runs on the other host too: open the file from the
    collections directory, the path `alai env` prints as
    `ANSIBLE_TEST_CONTENT_ROOT`, and set the breakpoint there. It's the
    same file as in your checkout.

#### `ansible-test shell`

`ansible-test shell` opens a shell for running Ansible commands by hand,
with the collection and its dependencies set up as for its tests:

- Only the commands ansible-test puts first on the shell's `PATH` are
  debugged: `ansible`, `ansible-playbook` and the other Ansible commands.
  `alai run ansible-playbook …` finds them too.
- The shell starts with only a few of the variables it was launched with,
  and ansible-test sets its own Ansible settings. Run commands with
  `alai run` to get the collections directory back; an `ANSIBLE_*`
  variable exported in `~/.bashrc` would otherwise replace ansible-test's.
- Run the command with `alai run` to debug the collection's modules with
  breakpoints set in your checkout: alai adds your checkout to the
  settings ansible-test gives modules.
- The Python extension activates the selected environment in terminals
  VS Code opens by typing `source .venv/bin/activate` into them. That can
  land in ansible-test's shell, putting the environment's own `ansible`
  ahead of ansible-test's, so nothing connects to the debugger; see
  [Troubleshooting](#troubleshooting-ansible-test-debugging).

#### `ansible-test integration`

Run the target to debug, `"args": ["test", "integration", "--dev-debug-on-demand", "my_target"]`.
ansible-test runs the target's playbooks itself, so there's nothing for
`alai run` to do, and module breakpoints don't hit. To debug a module the
target uses, run it from [`ansible-test shell`](#ansible-test-shell) with
`alai run` instead.

### Troubleshooting ansible-test debugging

If breakpoints don't hit, check from inside the shell that ansible-test
set it up for debugging:

| Check | Expected | If not |
|---|---|---|
| `command -v ansible` | A path in ansible-test's temporary directory, `/tmp/ansible-test-…-injector/ansible` | Something put another `ansible` first: usually the terminal's environment activation. Run `deactivate`, or see below |
| `echo "$ANSIBLE_TEST_DEBUGGER_CONFIG"` | JSON with `-m debugpy --connect localhost:<port>` | ansible-test didn't detect the debugger. Add `"-v"` to the launch's `args`: it says `Enabling on-demand debugging.` when it does |
| `echo "$ANSIBLE_COLLECTIONS_PATH"` | The collections directory `alai env` prints | Something in the shell's startup files, such as `~/.bashrc`, set its own. Run the command with `alai run` |

To tell a process that isn't connected from a breakpoint that doesn't
match, put `import debugpy; debugpy.breakpoint()` in the code: it stops
only when the process is connected to the debugger.

To stop the terminal activation, turn it off for the workspace in
`.vscode/settings.json`:

```json
{
  "python.terminal.activateEnvironment": false
}
```

The selected environment is still used to run and debug Python, but
terminals VS Code opens no longer activate it, so `ansible`, `python` or
`pytest` typed into one, and `alai run ansible-playbook …`, use whatever
`PATH` finds instead; activate it there by hand when you need it. VS Code
has no way to turn activation off for one launch. To keep it in other
terminals, the Python Environments extension's
`python-envs.terminal.autoActivationType` can be set to `shellStartup`,
which activates the environment as a terminal starts rather than by
typing a command, so it should be done before ansible-test sets up its
shell. It's a machine-wide setting, set under **Remote** or **User**
settings, not the workspace.

For more detail, add `"logToFile": true` to the launch configuration,
which writes debugpy's logs to the extension's directory, and export
`DEBUGPY_LOG_DIR=/tmp/debugpy-logs` in the shell before running a
command, which gives each process ansible-test starts a log of its own:
one missing means that process was never started under debugpy.

### Other tools

The Python extension can load the same file for its terminals and test
runs: set `python.envFile` to `${workspaceFolder}/.vscode/alai.env`.
