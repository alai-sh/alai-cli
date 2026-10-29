# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What several test modules need: writing a project's ``alai.toml`` and collection sources, reading what's installed,
and reading alai's output."""

from __future__ import annotations

import collections.abc as _c
import json
import os
import subprocess
import typing as _t
from pathlib import Path

from alai_core.toml import loads


def write_project(
    project: Path,
    collections: _c.Sequence[str] = (),
    *,
    groups: _c.Mapping[str, _c.Sequence[str]] | None = None,
    extra: str = "",
) -> None:
    """An ``alai.toml`` asking for ``collections``, with a ``[project.groups.<name>]`` for each of ``groups``, and
    ``extra`` written after them."""
    lines = ["[project]", f"collections = {json.dumps(list(collections))}"]
    for name, items in (groups or {}).items():
        lines += [f"[project.groups.{name}]", f"collections = {json.dumps(list(items))}"]
    _ = (project / "alai.toml").write_text("\n".join(lines) + "\n" + extra)


def write_sources(project: Path, sources: str, collections: str = '"ns.src"') -> None:
    """An ``alai.toml`` whose ``collections`` array holds ``collections`` as written, and whose ``[project.sources]``
    table holds ``sources``."""
    _ = (project / "alai.toml").write_text(
        f"[project]\ncollections = [{collections}]\n\n[project.sources]\n{sources}\n"
    )


def write_file(path: Path, content: str = "") -> Path:
    """``content`` at ``path``, making its directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(content)
    return path


def installed_at(cdir: Path, name: str) -> Path:
    """Where ``name`` is installed under the collections directory ``cdir``."""
    return cdir / "ansible_collections" / Path(*name.split("."))


def lock_table(project: Path) -> dict[str, _t.Any]:
    """``project``'s ``alai.lock``, as a test reads into it without checking each value's type."""
    return loads((project / "alai.lock").read_text())


def install_record(cdir: Path, name: str) -> dict[str, object] | None:
    """alai's record of where ``name`` was installed from, or None without one."""
    path = cdir / "ansible_collections" / ".alai" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def installed_versions(cdir: Path) -> dict[str, str]:
    """Each installed collection's version, by name, from its ``MANIFEST.json``; one without is left out."""
    out: dict[str, str] = {}
    for manifest in sorted((cdir / "ansible_collections").glob("*/*/MANIFEST.json")):
        info = json.loads(manifest.read_text())["collection_info"]
        out[f"{info['namespace']}.{info['name']}"] = info["version"]
    return out


def installed_names(cdir: Path) -> list[str]:
    """Every directory or link at ``<namespace>/<name>``, whatever it holds."""
    return sorted(f"{p.parent.name}.{p.name}" for p in (cdir / "ansible_collections").glob("*/*"))


def lines(err: str) -> list[str]:
    """alai's output with its timings dropped, which vary."""
    return [line.rsplit(" in ", 1)[0] if not line.startswith(" ") else line for line in err.splitlines()]


def edit(cdir: Path, *names: str) -> None:
    """Edit each collection's installed README.md. The file is replaced rather than written to, since a hardlinked
    install shares it with the cache, which an edit through it would change too."""
    for name in names:
        readme = installed_at(cdir, name) / "README.md"
        readme.unlink()
        _ = readme.write_text("edited\n")


def edited(cdir: Path) -> set[str]:
    """The installed collections whose README.md is still edited."""
    root = cdir / "ansible_collections"
    return {
        f"{p.parent.parent.name}.{p.parent.name}" for p in root.glob("*/*/README.md") if p.read_text() == "edited\n"
    }


def collection_source(
    root: Path,
    name: str = "ns.src",
    version: str = "1.0.0",
    dependencies: dict[str, str] | None = None,
    extra: str = "",
) -> Path:
    """A collection's source directory, with a plugin, a file build_ignore can leave out, and a .git directory."""
    namespace, short = name.split(".")
    root.mkdir(parents=True, exist_ok=True)
    lines = [f"namespace: {namespace}", f"name: {short}", f"version: {version}", "readme: README.md", "authors: [me]"]
    if dependencies:
        lines.append("dependencies:")
        lines += [f"  {key}: '{value}'" for key, value in dependencies.items()]
    _ = (root / "galaxy.yml").write_text("\n".join(lines) + "\n" + extra)
    _ = (root / "README.md").write_text("readme\n")
    (root / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
    _ = (root / "plugins" / "modules" / "hello.py").write_text("# hello\n")
    _ = (root / "notes.txt").write_text("notes\n")
    (root / ".git").mkdir(exist_ok=True)
    _ = (root / ".git" / "HEAD").write_text("ref\n")
    return root


class Repo:
    """A git repository on disk, committed to with a fixed author so tests don't depend on the user's git config."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        _ = self.git("init", "--quiet", "--initial-branch=main")

    @property
    def url(self) -> str:
        return self.path.as_uri()

    def git(self, *args: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        done = subprocess.run(  # noqa: S603 # the test's own git commands
            ["git", *args],  # noqa: S607
            cwd=self.path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def collection(
        self,
        directory: str = "",
        name: str = "ns.src",
        version: str = "1.0.0",
        dependencies: dict[str, str] | None = None,
        readme: str = "readme\n",
    ) -> None:
        namespace, short = name.split(".")
        root = self.path / directory if directory else self.path
        (root / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
        lines = [f"namespace: {namespace}", f"name: {short}", f"version: '{version}'", "readme: README.md"]
        lines.append("authors: [me]")
        if dependencies:
            lines.append("dependencies:")
            lines += [f"  {key}: '{value}'" for key, value in dependencies.items()]
        _ = (root / "galaxy.yml").write_text("\n".join(lines) + "\n")
        _ = (root / "README.md").write_text(readme)
        _ = (root / "plugins" / "modules" / "hello.py").write_text("# hello\n")

    def commit(self, message: str = "commit") -> str:
        _ = self.git("add", "--all")
        _ = self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")
