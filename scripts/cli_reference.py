# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Write the CLI reference, ``docs/cli.md``, from alai's argument parser and ``scripts/cli_recordings.toml``.

Each command gets a section with its description, its recordings, its usage and a table of its options, so the page
says what ``alai <command> --help`` does. The global options, which every command takes, are listed once. Run from the
repository root, with alai installed in the environment; ``--check`` exits 1 if the page isn't up to date instead:

    python scripts/cli_reference.py [--check]
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

import tomllib

from alai._cli import _build_parser

PAGE = pathlib.Path("docs/cli.md")
RECORDINGS = pathlib.Path("scripts/cli_recordings.toml")
DOCS = pathlib.Path("docs")
GLOBAL = "global options"

HEADER = """\
<!-- Written by scripts/cli_reference.py from alai's argument parser: edit the help there, not here. -->

# CLI reference

Every alai command, with what it does and the options it takes, as
`alai <command> --help` shows them. The recordings were made against a
local index when these docs were built, so their output is alai's own,
though the collections and timings are only examples.
"""


def main(check: bool) -> int:
    scenarios = tomllib.loads(RECORDINGS.read_text(encoding="utf-8"))
    by_name: dict[str, dict[str, str]] = {r["name"]: r for r in scenarios["recording"]}
    # Per command, the recordings shown under it, each with the description to show there, if any.
    reference: dict[str, list[dict[str, str]]] = scenarios.get("reference", {})
    problems = [
        f"[reference] '{command}' names no recording called '{entry['recording']}'"
        for command, entries in reference.items()
        for entry in entries
        if entry["recording"] not in by_name
    ]
    # Every recording is shown somewhere: in the reference, or linked from a page, so none is recorded for nothing.
    # The reference itself is left out: it's written from [reference], so it would count a stale entry as used.
    pages = [page for page in DOCS.rglob("*.md") if page != PAGE]
    linked = {m for page in pages for m in re.findall(r"casts/([\w-]+)\.cast", page.read_text(encoding="utf-8"))}
    shown = {entry["recording"] for entries in reference.values() for entry in entries}
    problems += [
        f"recording '{name}' isn't in [reference] or linked from any page"
        for name in by_name
        if name not in shown | linked
    ]
    recordings = {
        command: [
            {**by_name[entry["recording"]], "description": entry.get("description", "")}
            for entry in entries
            if entry["recording"] in by_name
        ]
        for command, entries in reference.items()
    }

    parser = _build_parser()
    commands = [(["alai"], parser), *_commands(parser, ["alai"])]
    for _, command in commands:
        # From 3.14 argparse colours help on a terminal; the page is the same wherever it's written.
        command.color = False
    sections = [HEADER, _command(["alai"], parser, []), _global_options(parser)]
    sections += [_command(names, command, recordings.get(" ".join(names[1:]), [])) for names, command in commands[1:]]
    known = {" ".join(names[1:]) for names, _ in commands}
    problems += [f"[reference] '{command}' isn't a command" for command in reference if command not in known]
    if problems:
        _ = sys.stderr.write("".join(f"{RECORDINGS}: {problem}\n" for problem in problems))
        return 1
    page = "\n".join(sections)

    current = PAGE.read_text(encoding="utf-8") if PAGE.is_file() else ""
    if not check:
        _ = PAGE.write_text(page, encoding="utf-8")
        return 0
    if current == page:
        return 0
    diff = difflib.unified_diff(current.splitlines(True), page.splitlines(True), str(PAGE), "generated")
    _ = sys.stderr.write("".join(diff))
    _ = sys.stderr.write(f"\n{PAGE} isn't up to date: run python {sys.argv[0]}\n")
    return 1


def _commands(parser: argparse.ArgumentParser, names: list[str]) -> list[tuple[list[str], argparse.ArgumentParser]]:
    """Every command under ``parser``, depth first, in the order they're registered."""
    found: list[tuple[list[str], argparse.ArgumentParser]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, command in action.choices.items():  # pyright: ignore[reportUnknownVariableType]
                found.append(([*names, name], command))
                found += _commands(command, [*names, name])  # pyright: ignore[reportUnknownArgumentType]
    return found


def _global_options(parser: argparse.ArgumentParser) -> str:
    group = next(g for g in parser._action_groups if g.title == GLOBAL)
    return "## Global options\n\nEvery command takes these, before or after its name.\n\n" + _table(
        parser, group._group_actions
    )


def _command(names: list[str], parser: argparse.ArgumentParser, recordings: list[dict[str, str]]) -> str:
    lines = [f"## {' '.join(names)}", "", _prose(parser.description or ""), ""]
    for recording in recordings:
        lines += [f"[{recording['title']}](casts/{recording['name']}.cast){{ .alai-cast }}", ""]
        # cast.js puts the paragraphs describing it between its title and the player; without JavaScript they follow
        # the link.
        for paragraph in re.split(r"\n\s*\n", recording.get("description", "").strip()):
            if paragraph:
                lines += [" ".join(paragraph.split()), "{: .alai-cast-description }", ""]

    formatter = parser._get_formatter()
    positionals = [a for a in parser._actions if not a.option_strings and a.help != argparse.SUPPRESS]
    usage = [*names, "[OPTIONS]"]
    for action in positionals:
        if isinstance(action, argparse._SubParsersAction):
            usage.append("<COMMAND>")
        else:
            usage.append(formatter._format_args(action, formatter._get_default_metavar_for_positional(action)))
    lines += ["```console", f"$ {' '.join(usage)}", "```", ""]

    for action in positionals:
        if isinstance(action, argparse._SubParsersAction):
            lines += ["| Command | Description |", "|---|---|"]
            for sub in action._choices_actions:
                anchor = "-".join([*names, sub.dest])
                lines.append(f"| [`{sub.dest}`](#{anchor}) | {_prose(sub.help or '')} |")
            lines.append("")
    arguments = [a for a in positionals if not isinstance(a, argparse._SubParsersAction)]
    if arguments:
        lines += ["### Arguments", "", _table(parser, arguments, "Argument")]

    for group in parser._action_groups:
        actions = [a for a in group._group_actions if a.option_strings and a.help != argparse.SUPPRESS]
        actions = [a for a in actions if not isinstance(a, argparse._HelpAction)]
        if group.title == GLOBAL or not actions:
            continue
        lines += [f"### {(group.title or '').capitalize()}", "", _table(parser, actions)]
    if len(names) > 1:
        lines.append("Also takes the [global options](#global-options).\n")
    return "\n".join(lines)


def _table(parser: argparse.ArgumentParser, actions: list[argparse.Action], heading: str = "Option") -> str:
    formatter = parser._get_formatter()
    rows = [f"| {heading} | Description |", "|---|---|"]
    for action in actions:
        invocation = formatter._format_action_invocation(action)
        if action.option_strings:
            # "-P, --upgrade-collection NAME" as `-P`, `--upgrade-collection NAME`
            invocation = ", ".join(f"`{part}`" for part in invocation.split(", "))
        else:
            invocation = f"`{invocation}`"
        description = _prose(formatter._expand_help(action))
        # argparse's usage shows these as [--locked | --frozen]; the page has no full usage, so the table says it.
        others = [
            f"`{max(other.option_strings, key=len)}`"
            for group in parser._mutually_exclusive_groups
            if action in group._group_actions
            for other in group._group_actions
            if other is not action
        ]
        if others:
            description += f". Can't be used with {' or '.join(others)}"
        rows.append(f"| {invocation} | {description} |")
    return "\n".join(rows) + "\n"


def _prose(text: str) -> str:
    """Help text as a sentence for the page: a capital letter, and options and variables as code."""
    text = re.sub(r"(?<![\w`-])(--?[a-zA-Z][\w-]*(?: [A-Z]+\b| [\w.-]+$)?|[A-Z][A-Z0-9]*_[A-Z0-9_]+)", r"`\1`", text)
    text = text.replace("|", "\\|")
    return text[:1].upper() + text[1:]


if __name__ == "__main__":
    sys.exit(main(check=sys.argv[1:] == ["--check"]))
