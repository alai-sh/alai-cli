# Editors

This page covers how to set up an editor to run and debug Ansible against a
project's collections.

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
itself, because they are typically based on user preferences, and detecting
which editor is being used and merging in configuration options would be
very difficult. Instead, `alai env --output-format dotenv` can be used to
write the environment variables Ansible needs to see the collections alai
manages.

## Visual Studio Code

When using VS Code, the Python debugger has to run in the same Python
environment that ansible-core is installed in. As alai is typically installed
as a uv tool, this is not the environment alai is in. Instead, you can
configure VS Code to launch the debug task using the `alai env` environment
file generated for that project.

### Running a playbook under the debugger

`.vscode/tasks.json` installs the project's collections and writes the
variables into `./.vscode/alai.env` before each launch:

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

`.vscode/launch.json` runs `ansible-playbook` with the `alai.env` values,
under the Python debugger:

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
- In a [collection project](projects.md#collection-project), give a
  playbook in the collection by its fully qualified name rather than its
  path, such as
  `"args": ["playbook", "my_ns.my_col.site"]`, so its short names for the
  collection's content resolve. See
  [In a collection project](running-ansible.md#in-a-collection-project).
- It runs with the Python interpreter selected for the workspace, which
  must be the one ansible-core is installed in, such as the project's
  `.venv`. Set `"python"` in the configuration to choose another.
- `alai env` doesn't install anything, so the task runs `alai sync` first.
  The file `.vscode/alai.env` is specific to that machine, so add it to
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

In a [collection project](projects.md#collection-project), Ansible loads
the collection through a link in its collections directory, as it does a
collection from an
[editable directory](collection-sources.md#an-editable-directory). While the
directory is different from your checkout, breakpoints set in your checkout
still hit, as the debugger matches them on the real path the link leads to.

The call stack shows the file's path in your checkout when you use
`alai run` or `alai env`, but `ansible-test` currently shows the path in the
collections directory that Ansible loaded it from. Module breakpoints
have further limitations described in
[Debugging with ansible-test](#debugging-with-ansible-test).

### Debugging with ansible-test

ansible-test has the ability to debug Python modules launched in the
ansible-test session through the `--dev-debug-on-demand` CLI argument. In
this mode, ansible-test sets up Ansible, and the modules it runs, to connect
back to the debugger automatically when they start, so you can debug the
code there.

To use `--dev-debug-on-demand`, the launch configuration is the same as any
other. Set `args` to `test ... --dev-debug-on-demand` to enter this mode, and
use the `envFile` and `preLaunchTask` defined above:

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

With ansible-core 2.21 and older, `debugpy` must be installed in the same
Python environment as ansible-core. Later versions automatically use the copy
bundled with VS Code.

The following table describes the different modes that ansible-test can use to
debug collection code and what does and does not work:

| `args` | Use it for | Controller code, such as action and filter plugins | Modules |
|---|---|---|---|
| `test shell` | Running Ansible commands by hand on this machine | Set in your checkout | Set in your checkout, when the command is run with `alai run` |
| `test integration` | Debugging an integration target as it runs | Set in your checkout | Doesn't work yet: run the module from `test shell` with `alai run` instead |
| `test shell`, `test integration` with `--docker` or `--remote` | The same, in a container or on another host | Set in the collections directory | Doesn't work yet |
| `test windows-integration`, `test network-integration` | Integration targets on Windows or network hosts | Set in your checkout | ansible-test doesn't connect these hosts to the debugger |

Commands with `--venv`, and `test units --local`, don't work in a
collection project; see
[Running Ansible](running-ansible.md#in-a-collection-project).

#### `ansible-test shell`

`ansible-test shell` opens a shell that can be used to launch subsequent
Ansible commands with a debugger attached. As `ansible-test` clears environment
variables like the ones `alai.env` sets, run any Ansible
commands in this shell through `alai run ...`.

#### `ansible-test integration`

Unlike `ansible-test shell`, this command runs the given integration test
target under the debugger, rather than providing a shell to run any
arbitrary command. For example,
`"args": ["test", "integration", "--dev-debug-on-demand", "my_target"]` will
run the `tests/integration/targets/my_target` target with the debugger
attached.

alai can't hook into this process, so module breakpoints don't hit, and the
call stack for controller-side plugins shows the path in the collections
directory rather than your checkout. To debug a module the
target uses, run it from [`ansible-test shell`](#ansible-test-shell) with
`alai run` instead.

### Troubleshooting ansible-test debugging

If breakpoints don't hit, check the following from inside the shell that
ansible-test set up:

| Check | Expected | If not |
|---|---|---|
| `command -v ansible` | A path in ansible-test's temporary directory, `/tmp/ansible-test-…-injector/ansible` | Something put another `ansible` first: usually the terminal's environment activation. Run `deactivate`, or see below |
| `echo "$ANSIBLE_TEST_DEBUGGER_CONFIG"` | JSON with `-m debugpy --connect localhost:<port>` | ansible-test didn't detect the debugger, so it wasn't started from the launch configuration. Add `"-v"` to the launch's `args`: it says `Enabling on-demand debugging.` when it does, and `Debugging disabled because no debugger was detected.` when it doesn't |
| `echo "$ANSIBLE_COLLECTIONS_PATH"` | The collections directory `alai env` prints | Something in the shell's startup files, such as `~/.bashrc`, set its own. Run the command with `alai run` |

To tell a process that isn't connected from a breakpoint that doesn't
match, put `import debugpy; debugpy.breakpoint()` in the code. It stops
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
`PATH` finds instead. VS Code has no way to turn off the environment
activation for a single launch configuration. Another option is to set the
Python Environments extension's
`python-envs.terminal.autoActivationType` to `shellStartup`, which activates
the environment as a terminal starts rather than injecting it as a command on
the launched terminal. Activating it at start means it doesn't override ansible-test's `ansible`.
It's a machine-wide setting and can't be set in `.vscode/settings.json` for
the workspace.

For more detail, add `"logToFile": true` to the launch configuration,
which writes debugpy's logs to the extension's directory, and export
`DEBUGPY_LOG_DIR=/tmp/debugpy-logs` in the shell before running a
command.

### Other tools

The Python extension can load the same `alai.env` file for its terminals and test
runs: set `python.envFile` to `${workspaceFolder}/.vscode/alai.env`.
