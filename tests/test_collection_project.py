# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""A collection project: its galaxy.yml as the manifest, and the checkout linked into a
tree in the cache."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from alai import _config
from alai._cli import EXIT_ERROR, main
from alai._common import ConfigError
from alai._config import Overrides, find_project, load_settings
from alai._galaxy import read_source_collection
from alai._lock import EditableSource, LockError, parse_lock, read_lock, render_lock

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def galaxy(
    root: Path, dependencies: dict[str, str] | None = None, *, name: str = "col", version: str = "1.0.0"
) -> None:
    lines = ["namespace: ns", f"name: {name}", f"version: {version}", "readme: README.md", "authors: [me]"]
    if dependencies:
        lines.append("dependencies:")
        lines += [f"  {key}: '{value}'" for key, value in dependencies.items()]
    _ = (root / "galaxy.yml").write_text("\n".join(lines) + "\n")


def runtime(root: Path, requires_ansible: str) -> None:
    (root / "meta").mkdir(exist_ok=True)
    _ = (root / "meta" / "runtime.yml").write_text(f"requires_ansible: '{requires_ansible}'\n")


def tree(root: Path) -> Path:
    """Where the checkout's tree is by default: the cache's envs directory, keyed by the checkout's path."""
    key = hashlib.sha256(os.fsencode(os.path.realpath(root))).hexdigest()[:16]
    return root.parent / "cache" / "envs" / key


def installed(cdir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted((cdir / "ansible_collections").glob("*/*")):
        name = f"{path.parent.name}.{path.name}"
        out[name] = f"-> {path.readlink()}" if path.is_symlink() else "dir"
    return out


def lines(err: str) -> list[str]:
    return [line.rsplit(" in ", 1)[0] if not line.startswith(" ") else line for line in err.splitlines()]


class TestDiscovery:
    def test_galaxy_yml_alone(self, tmp_path: Path) -> None:
        galaxy(tmp_path)
        (tmp_path / "plugins").mkdir()

        project = find_project(str(tmp_path / "plugins"))

        assert (project.root, project.config_file, project.marker) == (str(tmp_path), None, "galaxy.yml")
        assert project.collection_file == str(tmp_path / "galaxy.yml")

    def test_beside_alai_toml(self, tmp_path: Path) -> None:
        galaxy(tmp_path)
        _ = (tmp_path / "alai.toml").write_text("")

        project = find_project(str(tmp_path))

        assert (project.marker, project.collection_file) == ("alai.toml", str(tmp_path / "galaxy.yml"))

    def test_alai_toml_above_wins(self, tmp_path: Path) -> None:
        _ = (tmp_path / "alai.toml").write_text("")
        checkout = tmp_path / "collections/ansible_collections/ns/col"
        checkout.mkdir(parents=True)
        galaxy(checkout)

        project = find_project(str(checkout))

        assert (project.root, project.collection_file) == (str(tmp_path), None)

    def test_before_a_nearer_ansible_cfg(self, tmp_path: Path) -> None:
        galaxy(tmp_path)
        target = tmp_path / "tests/integration/targets/one"
        target.mkdir(parents=True)
        _ = (target / "ansible.cfg").write_text("")

        assert find_project(str(target)).root == str(tmp_path)


class TestTree:
    def test_in_the_cache(self, project: Path) -> None:
        galaxy(project)

        assert load_settings().collections_dir == str(tree(project))

    def test_set_outside(self, project: Path, tmp_path: Path) -> None:
        galaxy(project)

        settings = load_settings(Overrides(collections_dir=str(tmp_path / "elsewhere")))

        assert settings.collections_dir == str(tmp_path / "elsewhere")

    @pytest.mark.parametrize(("where", "what"), [("collections", "inside the collection"), (".", "inside")])
    def test_inside_the_checkout(self, project: Path, where: str, what: str) -> None:
        galaxy(project)

        with pytest.raises(ConfigError, match=f"collections_dir .* \\(from --collections-dir\\) {what}"):
            _ = load_settings(Overrides(collections_dir=str(project / where)))

    def test_holding_the_checkout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        checkout = tmp_path / "dev/ansible_collections/ns/col"
        checkout.mkdir(parents=True)
        galaxy(checkout)
        monkeypatch.chdir(checkout)
        monkeypatch.setenv("ALAI_COLLECTIONS_DIR", str(tmp_path / "dev"))

        with pytest.raises(ConfigError, match="\\(from ALAI_COLLECTIONS_DIR\\) holds the collection"):
            _ = load_settings()

    def test_playbook_projects_unchecked(self, project: Path) -> None:
        settings = load_settings(Overrides(collections_dir=str(project / "collections")))

        assert settings.collections_dir == str(project / "collections")

    def test_no_cache_dir(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        galaxy(project)
        monkeypatch.delenv("ALAI_CACHE_DIR")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(_config, "_home", lambda _environ: None)

        with pytest.raises(ConfigError, match="set ALAI_CACHE_DIR or ALAI_COLLECTIONS_DIR"):
            _ = load_settings()


class TestGalaxy:
    def test_reads_it(self, tmp_path: Path) -> None:
        galaxy(tmp_path, {"a.a": ">=1.0.0", "b.b": "*", "c.c": "1.0.0", "d.d": ">=1.0,<2.0.0"})
        runtime(tmp_path, ">=2.15")

        root = read_source_collection(str(tmp_path / "galaxy.yml"))

        assert (root.name, root.version.text, root.path) == ("ns.col", "1.0.0", str(tmp_path))
        # In alai.toml's syntax, from ansible-galaxy's: * dropped, a bare version made ==.
        assert root.dependencies == ("a.a>=1.0.0", "b.b", "c.c==1.0.0", "d.d>=1.0.0,<2.0.0")
        assert root.requires_ansible == ">=2.15"

    def test_no_runtime_or_dependencies(self, tmp_path: Path) -> None:
        galaxy(tmp_path)

        root = read_source_collection(str(tmp_path / "galaxy.yml"))

        assert (root.dependencies, root.requires_ansible) == ((), None)

    @pytest.mark.parametrize(
        ("text", "error"),
        [
            ("name: col\nversion: 1.0.0\n", "'namespace' and 'name' are required strings"),
            ("namespace: ns\nname: Col\nversion: 1.0.0\n", "collection names are lowercase"),
            ("namespace: ns\nname: col\n", "'version' is required"),
            ("namespace: ns\nname: col\nversion: one\n", "'version': "),
            ("namespace: ns\nname: col\nversion: 1.0.0\ndependencies: [a.a]\n", "'dependencies' must be a mapping"),
            ("namespace: ns\nname: col\nversion: 1.0.0\ndependencies: {a.a: 1}\n", "dependencies.a.a: expected"),
            ("namespace: ns\nname: col\nversion: 1.0.0\ndependencies: {a.a: '^1'}\n", "dependencies.a.a: invalid"),
            ("namespace: ns\nname: col\nversion: 1.0.0\ndependencies: {ns.col: '*'}\n", "can't depend on itself"),
            ("namespace: [ns\n", "isn't valid YAML"),
            ("- ns\n", "expected a mapping at the top level"),
        ],
    )
    def test_invalid(self, tmp_path: Path, text: str, error: str) -> None:
        _ = (tmp_path / "galaxy.yml").write_text(text)

        with pytest.raises(ConfigError, match=error.replace("(", "\\(").replace("^", "\\^")):
            _ = read_source_collection(str(tmp_path / "galaxy.yml"))

    def test_invalid_requires_ansible(self, tmp_path: Path) -> None:
        galaxy(tmp_path)
        runtime(tmp_path, "2.15+")

        with pytest.raises(ConfigError, match=r"runtime\.yml: requires_ansible: .*expected PEP 440"):
            _ = read_source_collection(str(tmp_path / "galaxy.yml"))

    def test_requires_ansible_not_a_string(self, tmp_path: Path) -> None:
        galaxy(tmp_path)
        (tmp_path / "meta").mkdir()
        _ = (tmp_path / "meta/runtime.yml").write_text("requires_ansible: 2.15\n")

        with pytest.raises(ConfigError, match="'requires_ansible' must be a string"):
            _ = read_source_collection(str(tmp_path / "galaxy.yml"))

    def test_empty(self, tmp_path: Path) -> None:
        _ = (tmp_path / "galaxy.yml").write_text("")

        with pytest.raises(ConfigError, match="'namespace' and 'name' are required strings"):
            _ = read_source_collection(str(tmp_path / "galaxy.yml"))

    def test_unreadable(self, tmp_path: Path) -> None:
        galaxy(tmp_path)
        (tmp_path / "meta/runtime.yml").mkdir(parents=True)

        with pytest.raises(ConfigError, match=r"can't read .*runtime\.yml"):
            _ = read_source_collection(str(tmp_path / "galaxy.yml"))


class TestSync:
    def test_links_and_installs(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project, {"a.a": ">=1.0.0", "c.c": "*"})
        runtime(project, ">=2.15")

        assert main(["sync"]) == 0

        assert installed(tree(project)) == {
            "a.a": "dir",
            "b.b": "dir",
            "c.c": "dir",
            "ns.col": f"-> {project}",
        }
        assert lines(capsys.readouterr().err) == [
            "Resolved 3 collections",
            "Linked 1 collection",
            f" + ns.col==1.0.0 (from {project})",
            "Prepared 3 collections",
            "Installed 3 collections",
            " + a.a==2.0.0",
            " + b.b==1.1.0",
            " + c.c==1.0.0",
        ]
        assert not (project / "collections").exists()

    def test_lock(self, project: Path) -> None:
        galaxy(project, {"a.a": ">=1.0.0", "c.c": "*"})
        runtime(project, ">=2.15")

        assert main(["lock", "-q"]) == 0

        text = (project / "alai.lock").read_text()
        data = tomllib.loads(text)
        assert data["project"] == {"requires_ansible": ">=2.15", "requirements": ["a.a>=1.0.0", "c.c"]}
        assert [c["name"] for c in data["collection"]] == ["a.a", "b.b", "c.c", "ns.col"]
        assert data["collection"][-1] == {
            "name": "ns.col",
            "version": "1.0.0",
            "source": {"editable": "."},
            "requires_ansible": ">=2.15",
            "dependencies": ["a.a", "c.c"],
        }
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and render_lock(lock) == text

    def test_nothing_to_do(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project, {"c.c": "*"})
        _ = main(["sync", "-q"])

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err) == ["Resolved 1 collection", "Checked 2 collections"]

    def test_exact_keeps_the_link(self, project: Path) -> None:
        galaxy(project, {"c.c": "*"})
        _ = main(["sync", "-q"])
        (tree(project) / "ansible_collections/my/work").mkdir(parents=True)

        assert main(["sync", "-q"]) == 0

        assert installed(tree(project)) == {"c.c": "dir", "ns.col": f"-> {project}"}

    def test_relinks(self, project: Path) -> None:
        galaxy(project)
        _ = main(["sync", "-q"])
        link = tree(project) / "ansible_collections/ns/col"
        link.unlink()
        link.mkdir()

        assert main(["sync", "-q"]) == 0

        assert installed(tree(project)) == {"ns.col": f"-> {project}"}

    def test_galaxy_change_relocks(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project, {"c.c": "*"})
        _ = main(["sync", "-q"])
        galaxy(project, {"c.c": "*"}, version="1.1.0")

        assert main(["lock"]) == 0

        assert "Updated ns.col v1.0.0 -> v1.1.0" in capsys.readouterr().err

    def test_version_change_relocks_on_sync(self, project: Path) -> None:
        galaxy(project)
        _ = main(["sync", "-q"])
        galaxy(project, version="2.0.0")

        assert main(["sync", "-q"]) == 0

        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and lock.root is not None and lock.root.version.text == "2.0.0"

    def test_frozen_lock_without_galaxy(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project)
        _ = main(["lock", "-q"])
        (project / "galaxy.yml").unlink()
        _ = (project / "alai.toml").write_text("")

        assert main(["sync", "--frozen"]) == EXIT_ERROR

        assert "alai.lock locks ns.col as the project's own, but there's no galaxy.yml" in capsys.readouterr().err

    def test_replaces_a_namespace_link(self, project: Path, tmp_path: Path) -> None:
        galaxy(project)
        (tmp_path / "elsewhere/other").mkdir(parents=True)
        (tree(project) / "ansible_collections").mkdir(parents=True)
        (tree(project) / "ansible_collections/ns").symlink_to(tmp_path / "elsewhere")

        assert main(["sync", "-q"]) == 0

        assert installed(tree(project)) == {"ns.col": f"-> {project}"}
        assert (tmp_path / "elsewhere/other").is_dir()

    def test_dependency_change_relocks(self, project: Path) -> None:
        galaxy(project, {"c.c": "*"})
        _ = main(["sync", "-q"])
        galaxy(project, {"c.c": "*", "d.d": "*"})

        assert main(["sync", "-q"]) == 0

        assert set(installed(tree(project))) == {"c.c", "d.d", "ns.col"}

    def test_locked_sees_galaxy_change(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project)
        _ = main(["lock", "-q"])
        runtime(project, ">=2.15")

        assert main(["sync", "--locked"]) == 1

        assert "alai.lock is out of date, and --locked was given" in capsys.readouterr().err

    def test_groups_from_alai_toml(self, project: Path) -> None:
        galaxy(project, {"c.c": "*"})
        _ = (project / "alai.toml").write_text('[project.groups.dev]\ncollections = ["d.d"]\n')

        assert main(["sync", "-q"]) == 0

        assert set(installed(tree(project))) == {"c.c", "d.d", "ns.col"}

    def test_only_group_leaves_it_out(self, project: Path) -> None:
        galaxy(project, {"c.c": "*"})
        _ = (project / "alai.toml").write_text('[project.groups.test]\ncollections = ["d.d"]\n')

        assert main(["sync", "-q", "--only-group", "test"]) == 0

        assert set(installed(tree(project))) == {"d.d"}

    @pytest.mark.parametrize(
        ("toml", "key"),
        [
            ('[project]\ncollections = ["d.d"]\n', "collections"),
            ('[project]\nrequires_ansible = ">=2.15"\n', "requires_ansible"),
        ],
    )
    def test_project_keys_refused(self, project: Path, capsys: pytest.CaptureFixture[str], toml: str, key: str) -> None:
        galaxy(project)
        _ = (project / "alai.toml").write_text(toml)

        assert main(["sync"]) == EXIT_ERROR

        assert f"'project.{key}' can't be set beside a collection's galaxy.yml" in capsys.readouterr().err

    def test_a_dependency_on_itself(self, project: Path) -> None:
        # a.a 2.0.0 depends on b.b>=1.0.0, which is the project's own collection here: the checkout satisfies it.
        _ = (project / "galaxy.yml").write_text("namespace: b\nname: b\nversion: 1.0.0\ndependencies: {a.a: '>=2'}\n")

        assert main(["sync", "-q"]) == 0

        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and [c.name for c in lock.collections] == ["a.a"]
        assert lock.root is not None and lock.root.name == "b.b"
        assert installed(tree(project)) == {"a.a": "dir", "b.b": f"-> {project}"}

    def test_a_dependency_on_itself_backtracks(self, project: Path) -> None:
        # The checkout is too old for a.a 2.0.0, so a.a 1.0.0, which doesn't need it, is chosen.
        _ = (project / "galaxy.yml").write_text("namespace: b\nname: b\nversion: 0.5.0\ndependencies: {a.a: '*'}\n")

        assert main(["lock", "-q"]) == 0

        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and [(c.name, c.version.text) for c in lock.collections] == [("a.a", "1.0.0")]

    def test_a_dependency_on_itself_unmet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "galaxy.yml").write_text("namespace: b\nname: b\nversion: 0.5.0\ndependencies: {a.a: '>=2'}\n")

        assert main(["lock"]) == EXIT_ERROR

        err = capsys.readouterr().err
        assert "b.b 0.5.0, the project's own collection, doesn't meet:\n  b.b>=1.0.0 (from a.a 2.0.0)" in err

    def test_add_refused_without_a_group(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project)

        assert main(["add", "c.c"]) == EXIT_ERROR

        assert "whose collections are galaxy.yml's dependencies" in capsys.readouterr().err

    def test_add_and_remove_a_group(self, project: Path) -> None:
        galaxy(project, {"c.c": "*"})
        _ = (project / "alai.toml").write_text("")

        assert main(["add", "-q", "--group", "test", "d.d"]) == 0

        assert (project / "alai.toml").read_text() == '[project.groups.test]\ncollections = ["d.d>=1.0.0"]\n'
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and lock.root is not None and lock.root.dependencies == ("c.c",)
        assert installed(tree(project)) == {"c.c": "dir", "d.d": "dir", "ns.col": f"-> {project}"}

        assert main(["remove", "-q", "--group", "test", "d.d"]) == 0

        assert installed(tree(project)) == {"c.c": "dir", "ns.col": f"-> {project}"}


class TestRunAndEnv:
    def test_env(self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("SHELL", "/bin/sh")
        galaxy(project)
        _ = main(["sync", "-q"])

        assert main(["env"]) == 0

        out, err = capsys.readouterr()
        cdir = tree(project)
        assert out.splitlines() == [
            f"export ANSIBLE_COLLECTIONS_PATH={cdir}",
            "export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False",
            f"export ANSIBLE_TEST_CONTENT_ROOT={cdir}/ansible_collections/ns/col",
            "export PYDEVD_RESOLVE_SYMLINKS=1",
        ]
        assert err == ""

    def test_env_without_the_link(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        galaxy(project)
        _ = main(["sync", "-q"])
        (tree(project) / "ansible_collections/ns/col").unlink()

        assert main(["env", "-q"]) == 0
        _ = main(["env"])

        assert capsys.readouterr().err == "warning: 1 collection from alai.lock not installed; run alai sync\n"

    def test_run(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        galaxy(project)
        ran: list[dict[str, str]] = []

        def execve(path: str, argv: list[str], env: dict[str, str]) -> None:
            ran.append(env)

        monkeypatch.setattr(os, "execve", execve)

        _ = main(["run", "-q", "true"])

        assert ran[0]["ANSIBLE_TEST_CONTENT_ROOT"] == f"{tree(project)}/ansible_collections/ns/col"
        assert ran[0]["PYDEVD_RESOLVE_SYMLINKS"] == "1"
        assert installed(tree(project)) == {"ns.col": f"-> {project}"}


class TestModuleDebugging:
    """alai maps the checkout into ansible-core's module debugger settings, which ansible-test sets, so breakpoints in
    modules hit through the tree's link."""

    def environment(self, project: Path, current: dict[str, str]) -> dict[str, str]:
        from alai._environ import ansible_environment
        from alai._project import load_project

        settings = load_settings()
        _, manifest = load_project(settings, "test")
        return ansible_environment(settings, manifest, current)

    @pytest.mark.parametrize("name", ["_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG", "_ANSIBLE_ANSIBALLZ_PYDEVD_CONFIG"])
    def test_adds_the_checkout(self, project: Path, name: str) -> None:
        galaxy(project)
        config = {"host": "localhost", "port": 1234, "source_mapping": {}}

        environ = self.environment(project, {name: json.dumps(config)})

        link = f"{tree(project)}/ansible_collections/ns/col"
        assert json.loads(environ[name]) == {**config, "source_mapping": {f"{project}/": f"{link}/"}}

    def test_without_a_mapping(self, project: Path) -> None:
        galaxy(project)

        environ = self.environment(project, {"_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG": '{"port": 1}'})

        assert set(json.loads(environ["_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG"])["source_mapping"]) == {f"{project}/"}

    @pytest.mark.parametrize(
        "value", ["not json", "[]", '{"source_mapping": []}', json.dumps({"source_mapping": {"PROJECT/": "x"}})]
    )
    def test_left_alone(self, project: Path, value: str) -> None:
        galaxy(project)

        environ = self.environment(
            project, {"_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG": value.replace("PROJECT", str(project))}
        )

        assert "_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG" not in environ

    def test_not_set(self, project: Path) -> None:
        galaxy(project)

        assert "_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG" not in self.environment(project, {})

    def test_playbook_project(self, project: Path) -> None:
        _ = (project / "alai.toml").write_text("")

        environ = self.environment(project, {"_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG": "{}"})

        assert "_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG" not in environ

    def test_run_passes_it_on(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        galaxy(project)
        monkeypatch.setenv("_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG", "{}")
        ran: list[dict[str, str]] = []
        monkeypatch.setattr(os, "execve", lambda path, argv, env: ran.append(env))

        _ = main(["run", "-q", "true"])

        assert f"{project}/" in json.loads(ran[0]["_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG"])["source_mapping"]


class TestLockFormat:
    def test_other_editable_paths_are_path_sources(self) -> None:
        table = {
            "version": 1,
            "collection": [{"name": "ns.col", "version": "1.0.0", "source": {"editable": "../col"}}],
        }

        lock = parse_lock(table, "alai.lock")

        assert lock.root is None
        assert lock.collections[0].source == EditableSource("../col")

    def test_only_one_root(self) -> None:
        root = {"version": "1.0.0", "source": {"editable": "."}}
        table = {"version": 1, "collection": [{"name": "ns.one", **root}, {"name": "ns.two", **root}]}

        with pytest.raises(LockError, match="only one collection can be the project's own"):
            _ = parse_lock(table, "alai.lock")

    def test_root_dependencies_checked(self) -> None:
        table = {
            "version": 1,
            "collection": [
                {"name": "ns.col", "version": "1.0.0", "source": {"editable": "."}, "dependencies": ["x.y"]}
            ],
        }

        with pytest.raises(LockError, match=r"'ns\.col' depends on 'x\.y', which isn't locked"):
            _ = parse_lock(table, "alai.lock")
