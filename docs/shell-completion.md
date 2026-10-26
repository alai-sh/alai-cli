# Shell completion

alai can provide shell completion options for various shells. Use
`alai generate-shell-completion <shell>` and your shell's eval command to have
it setup completion support:

| Shell | Add to | Line |
|---|---|---|
| bash | `~/.bashrc`, or `~/.bash_profile` on macOS | `eval "$(alai generate-shell-completion bash)"` |
| zsh | `~/.zshrc`, after `compinit` | `eval "$(alai generate-shell-completion zsh)"` |
| fish | | Run `alai generate-shell-completion fish > ~/.config/fish/completions/alai.fish` once |
| PowerShell | `$PROFILE` | `alai generate-shell-completion powershell | Out-String | Invoke-Expression` |
| tcsh | `~/.tcshrc` | ``eval "`alai generate-shell-completion tcsh`"`` |

After doing the above, restart the shell and completion should work using
whatever mechanism your shell exposes, for example with the tab key.

!!! note "bash on macOS"

    Terminal and iTerm2 start bash as a login shell, which reads
    `~/.bash_profile` and not `~/.bashrc`. Use `~/.bash_profile`, unless it
    already sources `~/.bashrc`. Linux terminals start non-login shells, which
    read `~/.bashrc`.

!!! note "zsh needs compinit"

    The script registers alai with zsh's completion system, so that has to be
    loaded first. Frameworks such as Oh My Zsh load it for you; otherwise put
    `autoload -Uz compinit && compinit` above the alai line. zsh is the
    default shell on macOS.

The script doesn't list alai's options. On each completion request it runs
`alai`, which answers from its own options, so the script never needs
regenerating when alai is upgraded.

Completion only works when `alai` is the command being run. When alai is
called through another command, such as `uv run alai ...`, the shell completes
`uv`'s arguments and not alai's.

## Other ways to set up completion

### bash-completion

[bash-completion](https://github.com/scop/bash-completion) loads a command's
completion script from a directory the first time you press Tab after that
command, rather than when the shell starts. Saving alai's script there means
nothing needs adding to `~/.bashrc`, and a new shell doesn't run alai until
you complete it:

```console
$ alai generate-shell-completion bash > ~/.local/share/bash-completion/completions/alai
```

This needs bash-completion version 2, which most Linux distributions install
with bash. macOS doesn't include it but can be installed through tools like
Homebrew through the `bash-completion@2` package.

### argcomplete's global completion

alai's completion is built on
[argcomplete](https://github.com/kislyuk/argcomplete). If you already use its
[global completion](https://github.com/kislyuk/argcomplete#global-completion)
(`activate-global-python-argcomplete`, in bash and zsh), alai is completed
with no setup.
