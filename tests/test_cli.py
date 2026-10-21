# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from alai import _config, _output
from alai._args import terminal_width
from alai._cli import EXIT_ERROR, main
from alai._commands.shell_completion import SHELLS


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run from an empty directory with no user, system or ALAI_* settings."""
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    xdg_dirs = tmp_path / "xdg"
    xdg_dirs.mkdir()
    for name in [n for n in os.environ if n.startswith(("ALAI_", "XDG_"))]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(xdg_dirs))
    monkeypatch.chdir(home / "work")
    return home / "work"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"alai {version('alai')}\n"


@pytest.mark.parametrize("argv", [[], ["config"]])
def test_no_command_prints_help(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    assert main(argv) == EXIT_ERROR
    assert "usage: alai" in capsys.readouterr().err


def lines(out: str) -> list[str]:
    """The output's lines with runs of spaces collapsed, so a test doesn't depend on column widths."""
    return [" ".join(line.split()) for line in out.splitlines()]


def test_config_show_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "show"]) == 0

    # The home directory is shown as ~, and isolated is ~/work.
    assert capsys.readouterr().out == (
        "Project\n"
        "  root  ~/work (no marker found)\n"
        "\n"
        "Config files, highest precedence first\n"
        "  project  none\n"
        "  user     none\n"
        "  system   none\n"
        "\n"
        "Settings\n"
        "  project.collections_dir  ~/work/collections  default\n"
        "  cache_dir                ~/.cache/alai       default\n"
        "  link_mode                auto                default\n"
        "  system_certs             true                default\n"
        "  offline                  false               default\n"
        "\n"
        "Indexes, in resolution order\n"
        "  alai  https://index.alai.sh/  built-in, default index\n"
    )


@pytest.mark.parametrize(("argv", "expected"), [([], "true"), (["--no-system-certs"], "false")])
def test_config_show_boolean(capsys: pytest.CaptureFixture[str], argv: list[str], expected: str) -> None:
    assert main(["config", "show", "system_certs", *argv]) == 0
    assert capsys.readouterr().out == f"{expected}\n"


def test_boolean_flag_pair_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["config", "show", "--help"])
    assert "--system-certs, --no-system-certs" in capsys.readouterr().out


def test_config_show_project(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (isolated / "alai.toml").write_text('link_mode = "copy"\n[[index]]\nname = "internal"\nurl = "https://internal/"\n')
    (isolated / "sub").mkdir()

    assert main(["config", "show", "--project", "sub"]) == 0

    out = lines(capsys.readouterr().out)
    assert "root ~/work (found by alai.toml)" in out
    assert "project ~/work/alai.toml" in out
    assert "link_mode copy ~/work/alai.toml" in out
    assert out.index("internal https://internal/ ~/work/alai.toml") < out.index(
        "alai https://index.alai.sh/ built-in, default index"
    )


def test_config_show_verbose(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (isolated / "alai.toml").write_text('link_mode = "copy"\n')
    monkeypatch.setenv("ALAI_LINK_MODE", "hardlink")

    assert main(["-v", "config", "show"]) == 0

    out = lines(capsys.readouterr().out)
    assert "user none, looked for ~/.config/alai/alai.toml" in out
    # The value replaced by a higher level follows the one used.
    i = out.index("link_mode hardlink ALAI_LINK_MODE")
    assert out[i + 1] == "copy ~/work/alai.toml, overridden"


def test_config_show_config_file(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (isolated / "ci.toml").write_text('link_mode = "copy"\n')

    assert main(["config", "show", "--config-file", "ci.toml"]) == 0

    out = lines(capsys.readouterr().out)
    assert "--config-file ~/work/ci.toml" in out
    assert "link_mode copy ~/work/ci.toml" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["--no-config", "config", "show"],
        ["config", "--no-config", "show"],
        ["config", "show", "--no-config"],
    ],
)
def test_global_args_anywhere(isolated: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    (isolated / "alai.toml").write_text('link_mode = "copy"\n')

    assert main(argv) == 0
    assert "none --no-config given" in lines(capsys.readouterr().out)


def test_global_arg_before_subcommand_kept(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--cache-dir", "/c", "config", "show"]) == 0
    assert "cache_dir /c --cache-dir" in lines(capsys.readouterr().out)


def test_settings_args(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["config", "show", "--link-mode", "hardlink", "--index", "a=https://a/", "--index", "b=https://b/"]
    argv += ["--default-index", "https://mirror/", "--collections-dir", "out"]

    assert main(argv) == 0

    out = lines(capsys.readouterr().out)
    assert "link_mode hardlink --link-mode" in out
    assert "project.collections_dir ~/work/out --collections-dir" in out
    assert out[-3:] == [
        "a https://a/ --index",
        "b https://b/ --index",
        "alai https://mirror/ --default-index, default index",
    ]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("link_mode", "copy\n"),
        # The full path, not shortened to ~.
        ("project.collections_dir", "{isolated}/collections\n"),
        ("index", "internal=https://internal/\nalai=https://index.alai.sh/\n"),
    ],
)
def test_config_show_key(isolated: Path, capsys: pytest.CaptureFixture[str], key: str, expected: str) -> None:
    (isolated / "alai.toml").write_text('link_mode = "copy"\n[[index]]\nname = "internal"\nurl = "https://internal/"\n')

    assert main(["config", "show", key]) == 0
    assert capsys.readouterr().out == expected.format(isolated=isolated)


def test_config_show_key_source(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ALAI_LINK_MODE", "copy")

    assert main(["-v", "config", "show", "link_mode"]) == 0

    out, err = capsys.readouterr()
    assert out == "copy\n"
    assert "debug: link_mode from ALAI_LINK_MODE\n" in err


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("link_mod", "unknown setting 'link_mod', did you mean 'link_mode'?"),
        ("collections_dir", "unknown setting 'collections_dir', did you mean 'project.collections_dir'?"),
        ("default_index", "unknown setting 'default_index'"),
        ("tool.my-ci", "'tool.my-ci' is in [tool], which alai never reads"),
    ],
)
def test_config_show_bad_key(capsys: pytest.CaptureFixture[str], key: str, message: str) -> None:
    assert main(["config", "show", key]) == EXIT_ERROR
    assert capsys.readouterr().err.startswith(f"error: {message}")


def test_config_error(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (isolated / "alai.toml").write_text('nope = "x"\n')

    assert main(["config", "show"]) == EXIT_ERROR
    assert capsys.readouterr().err == f"error: {isolated / 'alai.toml'}: unknown setting 'nope'\n"


def test_verbose_logs_debug(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (isolated / "alai.toml").write_text('[[index]]\nname = "alai"\nurl = "https://pinned/"\ndefault = true\n')

    assert main(["-v", "config", "show"]) == 0
    assert "debug: Ignoring index 'alai' from built-in" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["-vvv", "config", "show"], "-v/--verbose can be given at most twice (-vv)"),
        (["-vv", "config", "show", "-v"], "-v/--verbose can be given at most twice (-vv)"),
        (["-q", "config", "show", "-v"], "-q/--quiet and -v/--verbose can't be used together"),
    ],
)
def test_verbosity_errors(capsys: pytest.CaptureFixture[str], argv: list[str], message: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert exc.value.code == EXIT_ERROR
    assert f"error: {message}\n" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "verbosity"),
    [
        (["config", "show"], 0),
        (["-q", "config", "show"], -1),
        (["config", "show", "-q"], -1),
        (["-v", "config", "show"], 1),
        (["-vv", "config", "show"], 2),
        (["--verbose", "--verbose", "config", "show"], 2),
        # Counted across every level, not replaced by the subcommand's.
        (["-v", "config", "show", "-v"], 2),
        (["config", "-v", "show", "-v"], 2),
    ],
)
def test_verbosity(monkeypatch: pytest.MonkeyPatch, argv: list[str], verbosity: int) -> None:
    monkeypatch.setattr(_output, "verbosity", 0)

    assert main(argv) == 0
    assert _output.verbosity == verbosity


@pytest.mark.parametrize(
    ("verbosity", "expected"),
    [
        (-1, ""),
        (0, "warning: w\n"),
        (1, "warning: w\ndebug: d\n"),
        (2, "warning: w\ndebug: d\ntrace: t\n"),
    ],
)
def test_output_levels(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], verbosity: int, expected: str
) -> None:
    monkeypatch.setattr(_output, "verbosity", verbosity)

    _output.warn("w")
    _output.debug("d")
    _output.trace("t")

    assert capsys.readouterr().err == expected


@pytest.mark.parametrize(("columns", "expected"), [("120", 120), ("0", 80), ("wide", 80)])
def test_help_width(monkeypatch: pytest.MonkeyPatch, columns: str, expected: int) -> None:
    # Not a terminal under pytest, so anything but a valid COLUMNS falls back to 80.
    monkeypatch.setenv("COLUMNS", columns)

    assert terminal_width() == expected


# What alai may import on top of argparse, besides its own modules: argparse's gettext lookups load locale, and 3.10
# to 3.12 load collections.abc and errno for us. Anything else on the startup path costs every command.
_STARTUP_IMPORTS = {"__future__", "_locale", "collections.abc", "errno", "locale"}

_PROBE = """
import json, sys
import argparse
{setup}
before = set(sys.modules)
from alai._cli import main
try:
    main({argv!r})
except SystemExit:
    pass
with open({out!r}, "w") as fd:
    json.dump(sorted(set(sys.modules) - before), fd)
"""


@pytest.mark.parametrize(
    ("argv", "setup"),
    [
        # --version is meant to load importlib.metadata, so the probe loads it first and checks what's left.
        (["--version"], "import importlib.metadata; importlib.metadata.version('alai')"),
        (["config", "show"], ""),
    ],
)
def test_startup_imports(isolated: Path, tmp_path: Path, argv: list[str], setup: str) -> None:
    out = tmp_path / "modules.json"
    code = _PROBE.format(setup=setup, argv=argv, out=str(out))

    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, env=os.environ.copy())  # noqa: S603

    loaded = {m for m in json.loads(out.read_text()) if m != "alai" and not m.startswith("alai.")}
    assert loaded - _STARTUP_IMPORTS == set()


def complete(tmp_path: Path, line: str) -> list[str]:
    """The completions argcomplete gives for the command line typed so far.

    Run in a subprocess, as the shell does: argcomplete takes over file descriptors 8 and 9 and exits when done.
    """
    out = tmp_path / "completions"
    env = os.environ | {
        "_ARGCOMPLETE": "1",
        "_ARGCOMPLETE_STDOUT_FILENAME": str(out),
        "COMP_LINE": line,
        "COMP_POINT": str(len(line)),
    }

    subprocess.run([sys.executable, "-m", "alai"], check=True, capture_output=True, env=env)

    return out.read_text().split("\013")


def test_complete_subcommand(tmp_path: Path) -> None:
    assert complete(tmp_path, "alai con") == ["config "]


def test_complete_option(tmp_path: Path) -> None:
    assert complete(tmp_path, "alai config show --link-m") == ["--link-mode "]


def test_complete_choices(tmp_path: Path) -> None:
    assert complete(tmp_path, "alai config show --link-mode h") == ["hardlink "]


def test_complete_suggestions(tmp_path: Path) -> None:
    assert complete(tmp_path, "alai find -t fi") == ["filter "]
    # With several, the shell adds no space after any of them.
    assert sorted(complete(tmp_path, "alai find --type c")) == ["cache", "callback", "cliconf", "connection"]


def test_suggestions_not_enforced(tmp_path: Path) -> None:
    from alai._cli import _build_parser

    args = _build_parser().parse_args(["find", "-t", "a_type_from_the_future", "x"])

    assert args.types == ["a_type_from_the_future"]


def test_complete_directories(isolated: Path, tmp_path: Path) -> None:
    (isolated / "playbooks").mkdir()
    (isolated / "plays.yml").touch()

    assert complete(tmp_path, "alai config show --project pla") == ["playbooks/"]


@pytest.mark.parametrize("shell", SHELLS)
def test_generate_shell_completion(capsys: pytest.CaptureFixture[str], shell: str) -> None:
    assert main(["generate-shell-completion", shell]) == 0
    assert "alai" in capsys.readouterr().out


def test_config_show_without_home(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(_config, "_home", lambda _environ: None)

    assert main(["config", "show"]) == 0
    assert "cache_dir unknown: cannot find the home directory for the cache, set ALAI_CACHE_DIR default" in lines(
        capsys.readouterr().out
    )

    assert main(["config", "show", "cache_dir"]) == EXIT_ERROR
    assert capsys.readouterr().err == "error: cannot find the home directory for the cache, set ALAI_CACHE_DIR\n"
