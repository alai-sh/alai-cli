# Shell completion

alai can complete its commands, options and option values when you press
Tab. `alai generate-shell-completion <shell>` prints the script that turns it
on; add it to your shell's startup file once.

| Shell | Add to | Line |
|---|---|---|
| bash | `~/.bashrc`, or `~/.bash_profile` on macOS | `eval "$(alai generate-shell-completion bash)"` |
| zsh | `~/.zshrc`, after `compinit` | `eval "$(alai generate-shell-completion zsh)"` |
| fish | | Run `alai generate-shell-completion fish > ~/.config/fish/completions/alai.fish` once |
| PowerShell | `$PROFILE` | `alai generate-shell-completion powershell | Out-String | Invoke-Expression` |
| tcsh | `~/.tcshrc` | ``eval "`alai generate-shell-completion tcsh`"`` |

Then start a new shell.

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

The script doesn't list alai's options. On each Tab it runs `alai`, which
answers from its own options, so the script never needs regenerating when
alai is upgraded. That also means bash users can save it once instead of
running it in every new shell:

```console
$ alai generate-shell-completion bash > ~/.local/share/bash-completion/completions/alai
```

bash-completion then loads it the first time you press Tab after `alai`.
This needs the bash-completion package (version 2), which most Linux
distributions install with bash. macOS doesn't include it; Homebrew has it as
`bash-completion@2`.

Completion works for the `alai` command, as installed by
`uv tool install alai` or in an activated virtual environment. It doesn't
work through `uv run alai …`, since the shell completes for `uv` there.

If you already use argcomplete's global completion
(`activate-global-python-argcomplete`, bash and zsh), alai is completed with
no setup. In bash that needs bash 4.2 or later, so not the bash 3.2 that macOS
includes.
