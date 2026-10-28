# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Path sources: a collection taken from a directory holding its galaxy.yml, copied as ansible-galaxy installs one,
or linked to it when editable."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from alai_core.toml import loads
from helpers import collection_source, install_record, installed_at, lines, write_sources

from alai._cli import main
from alai._common import EXIT_ERROR
from alai._lock import DirectorySource, EditableSource, parse_lock, read_lock, render_lock


class TestLock:
    def test_directory(self, project: Path) -> None:
        _ = collection_source(project.parent / "src", dependencies={"a.a": ">=2.0.0"})
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["lock", "-q"]) == 0

        data = loads((project / "alai.lock").read_text())
        assert data["sources"] == {"ns.src": {"path": "../src"}}
        entries = {c["name"]: c for c in data["collection"]}
        assert sorted(entries) == ["a.a", "b.b", "ns.src"]
        assert entries["ns.src"] == {
            "name": "ns.src",
            "version": "1.0.0",
            "source": {"directory": "../src"},
            "collections": ["a.a>=2.0.0"],
            "dependencies": ["a.a"],
        }
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and render_lock(lock) == (project / "alai.lock").read_text()

    def test_editable(self, project: Path) -> None:
        _ = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src", editable = true }')

        assert main(["lock", "-q"]) == 0

        text = (project / "alai.lock").read_text()
        assert '"ns.src" = { path = "../src", editable = true }' in text
        assert 'source = { editable = "../src" }' in text
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and lock.collections[0].source == EditableSource("../src")

    def test_as_a_dependency(self, project: Path) -> None:
        # A source applies to whatever needs the collection, not only what the project lists.
        _ = collection_source(project.parent / "b", "b.b", "9.0.0")
        write_sources(project, '"b.b" = { path = "../b" }', '"a.a"')

        assert main(["lock", "-q"]) == 0

        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None
        b = lock.get("b.b")
        assert b is not None and b.version.text == "9.0.0" and b.source == DirectorySource("../b")

    def test_version_not_met(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }', '"ns.src>=2"')

        assert main(["lock"]) == EXIT_ERROR

        err = capsys.readouterr().err
        assert "only ns.src==1.0.0, from ../src, is available and the project depends on ns.src>=2.0.0" in err

    def test_galaxy_change_is_stale(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["lock", "-q"])
        _ = collection_source(src, dependencies={"c.c": "*"})

        assert main(["lock", "--check"]) == 1
        assert "Added c.c v1.0.0" in capsys.readouterr().err
        assert main(["lock"]) == 0
        _ = collection_source(src, version="1.1.0", dependencies={"c.c": "*"})
        assert main(["lock"]) == 0
        assert "Updated ns.src v1.0.0 -> v1.1.0" in capsys.readouterr().err

    @pytest.mark.parametrize("command", [["lock", "--check"], ["sync", "--locked"]])
    def test_check_lists_what_changed(
        self, project: Path, capsys: pytest.CaptureFixture[str], command: list[str]
    ) -> None:
        src = collection_source(project.parent / "src", dependencies={"a.a": ">=1.0.0", "c.c": "*"})
        (src / "meta").mkdir()
        _ = (src / "meta" / "runtime.yml").write_text("requires_ansible: '>=2.15'\n")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["lock", "-q"])
        _ = collection_source(src, version="1.1.0", dependencies={"a.a": ">=2.0.0", "d.d": "*"})
        _ = (src / "meta" / "runtime.yml").write_text("requires_ansible: '>=2.16'\n")

        assert main(command) == 1

        err = capsys.readouterr().err
        assert (
            "../src has changed since alai.lock was made:\n"
            + "  galaxy.yml version: changed 1.0.0 to 1.1.0\n"
            + "  galaxy.yml dependencies: changed a.a>=1.0.0 to a.a>=2.0.0\n"
            + "  galaxy.yml dependencies: removed c.c\n"
            + "  galaxy.yml dependencies: added d.d\n"
            + "  meta/runtime.yml requires_ansible: changed >=2.15 to >=2.16\n"
        ) in err

    def test_check_unchanged_says_nothing(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = collection_source(project.parent / "src", dependencies={"c.c": "*"})
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["lock", "-q"])

        assert main(["lock", "--check"]) == 0

        assert "has changed since" not in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("setup", "message"),
        [
            ("missing", "doesn't exist or isn't a directory (from [project.sources] for ns.src)"),
            ("no-galaxy", "has no galaxy.yml or MANIFEST.json, so it isn't a collection"),
            ("both", "has both a galaxy.yml and a MANIFEST.json"),
            ("other", "takes ns.src from ../src, but its galaxy.yml is for ns.other"),
            ("inside", "is inside the collections directory"),
        ],
    )
    def test_refused(self, project: Path, capsys: pytest.CaptureFixture[str], setup: str, message: str) -> None:
        path = "../src"
        if setup == "no-galaxy":
            (project.parent / "src").mkdir()
        elif setup == "both":
            _ = (collection_source(project.parent / "src") / "MANIFEST.json").write_text("{}")
        elif setup == "other":
            _ = collection_source(project.parent / "src", "ns.other")
        elif setup == "inside":
            _ = collection_source(project / "collections" / "ansible_collections" / "ns" / "src")
            path = "collections/ansible_collections/ns/src"
        write_sources(project, f'"ns.src" = {{ path = "{path}" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert message in capsys.readouterr().err

    def test_round_trip(self) -> None:
        table = {
            "version": 1,
            "revision": 1,
            "sources": {"ns.src": {"path": "../src", "editable": True}},
            "collection": [
                {
                    "name": "ns.src",
                    "version": "1.0.0",
                    "source": {"directory": "/abs/src"},
                    "requires_ansible": ">=2.16",
                    "collections": [],
                    "dependencies": [],
                }
            ],
        }

        text = render_lock(parse_lock(table, "alai.lock"))

        assert loads(text) == {**table, "project": {"collections": []}}


class TestRequiresAnsible:
    def runtime(self, src: Path, requires_ansible: str) -> None:
        (src / "meta").mkdir(exist_ok=True)
        _ = (src / "meta" / "runtime.yml").write_text(f"requires_ansible: '{requires_ansible}'\n")

    def test_newer_than_the_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.runtime(collection_source(project.parent / "src"), ">=2.19")
        _ = (project / "alai.toml").write_text(
            '[project]\nrequires_ansible = ">=2.17"\ncollections = ["ns.src"]\n\n'
            + '[project.sources]\n"ns.src" = { path = "../src" }\n'
        )

        assert main(["lock"]) == EXIT_ERROR

        assert (
            "Because ns.src==1.0.0 needs a newer ansible-core than the project's >=2.17 and the project depends on "
            "ns.src, we can conclude that the project's requirements are unsatisfiable."
        ) in capsys.readouterr().err

    def test_ignored(self, project: Path) -> None:
        self.runtime(collection_source(project.parent / "src"), ">=2.19")
        _ = (project / "alai.toml").write_text(
            '[project]\nrequires_ansible = ">=2.17"\ncollections = ["ns.src"]\n\n'
            + '[project.overrides]\nignore_requires_ansible = ["ns.src"]\n\n'
            + '[project.sources]\n"ns.src" = { path = "../src" }\n'
        )

        assert main(["lock", "-q"]) == 0

    def test_ends_inside_the_project(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.runtime(collection_source(project.parent / "src"), ">=2.15,<2.18")
        _ = (project / "alai.toml").write_text(
            '[project]\nrequires_ansible = ">=2.17"\ncollections = ["ns.src"]\n\n'
            + '[project.sources]\n"ns.src" = { path = "../src" }\n'
        )

        assert main(["lock"]) == 0

        assert "ns.src 1.0.0 supports ansible-core >=2.15,<2.18, which ends inside" in capsys.readouterr().err


class TestSync:
    def test_copies(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src", extra="build_ignore: ['*.txt']\n")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err) == [
            "Resolved 1 collection",
            "Installed 1 collection",
            f" + ns.src==1.0.0 (from {src})",
        ]
        target = installed_at(project / "collections", "ns.src")
        assert not target.is_symlink()
        assert sorted(str(p.relative_to(target)) for p in target.rglob("*")) == [
            "FILES.json",
            "MANIFEST.json",
            "README.md",
            "plugins",
            "plugins/modules",
            "plugins/modules/hello.py",
        ]
        manifest = json.loads((target / "MANIFEST.json").read_text())
        assert manifest["collection_info"]["version"] == "1.0.0"
        assert install_record(project / "collections", "ns.src") == {
            "source": {"directory": str(src)},
            "cache_keys": {
                "galaxy.yml": hashlib.sha256((src / "galaxy.yml").read_bytes()).hexdigest(),
                "MANIFEST.json": None,
                "meta/runtime.yml": None,
            },
        }

    def test_checked_until_galaxy_changes(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["sync", "-q"])
        _ = (src / "plugins" / "modules" / "hello.py").write_text("# edited\n")

        assert main(["sync"]) == 0
        assert lines(capsys.readouterr().err) == ["Resolved 1 collection", "Checked 1 collection"]

        # Only touching galaxy.yml isn't a change: its hash is the cache key, not its modification time.
        os.utime(src / "galaxy.yml", ns=(0, 1))
        assert main(["sync"]) == 0
        assert lines(capsys.readouterr().err) == ["Resolved 1 collection", "Checked 1 collection"]

        # Changing it is, even when what alai.lock records stays the same.
        with (src / "galaxy.yml").open("a") as f:
            _ = f.write("# a comment\n")
        assert main(["sync"]) == 0
        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from {src})"
        assert (
            installed_at(project / "collections", "ns.src") / "plugins" / "modules" / "hello.py"
        ).read_text() == "# edited\n"

    def test_runtime_change_copies_again(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["sync", "-q"])
        (src / "meta").mkdir()
        _ = (src / "meta" / "runtime.yml").write_text("requires_ansible: '>=2.15'\n")

        assert main(["sync"]) == 0

        # The lock is out of date, so it's locked again, and the copy has the new meta/runtime.yml.
        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from {src})"
        assert (installed_at(project / "collections", "ns.src") / "meta" / "runtime.yml").exists()
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and lock.collections[0].requires_ansible == ">=2.15"

    def test_reinstall_copies_again(self, project: Path) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["sync", "-q"])
        _ = (src / "notes.txt").write_text("new\n")

        assert main(["sync", "-q", "--reinstall-collection", "ns.src"]) == 0

        assert (installed_at(project / "collections", "ns.src") / "notes.txt").read_text() == "new\n"

    def test_version_change_relocks(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["sync", "-q"])
        _ = collection_source(src, version="2.0.0")

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err)[-2:] == [" - ns.src==1.0.0", f" + ns.src==2.0.0 (from {src})"]

    def test_index_copy_replaced(self, project: Path) -> None:
        # The same name and version from an index has no record, so it isn't the directory's copy.
        _ = collection_source(project.parent / "c", "c.c")
        write_sources(project, '"c.c" = { index = "alai" }', '"c.c"')
        _ = main(["sync", "-q"])
        assert install_record(project / "collections", "c.c") is None
        write_sources(project, '"c.c" = { path = "../c" }', '"c.c"')

        assert main(["sync", "-q"]) == 0
        assert install_record(project / "collections", "c.c") is not None
        assert (installed_at(project / "collections", "c.c") / "notes.txt").exists()

        write_sources(project, '"c.c" = { index = "alai" }', '"c.c"')
        assert main(["sync", "-q"]) == 0
        assert install_record(project / "collections", "c.c") is None
        assert not (installed_at(project / "collections", "c.c") / "notes.txt").exists()

    def test_editable(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src", dependencies={"c.c": "*"})
        write_sources(project, '"ns.src" = { path = "../src", editable = true }')

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err) == [
            "Resolved 2 collections",
            "Linked 1 collection",
            f" + ns.src==1.0.0 (from {src})",
            "Prepared 1 collection",
            "Installed 1 collection",
            " + c.c==1.0.0",
        ]
        assert installed_at(project / "collections", "ns.src").readlink() == src
        assert main(["sync"]) == 0
        assert lines(capsys.readouterr().err) == ["Resolved 2 collections", "Checked 2 collections"]

    def test_editable_to_copy_and_back(self, project: Path) -> None:
        _ = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src", editable = true }')
        _ = main(["sync", "-q"])

        write_sources(project, '"ns.src" = { path = "../src" }')
        assert main(["sync", "-q"]) == 0
        assert not installed_at(project / "collections", "ns.src").is_symlink()
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

        write_sources(project, '"ns.src" = { path = "../src", editable = true }')
        assert main(["sync", "-q"]) == 0
        assert installed_at(project / "collections", "ns.src").is_symlink()
        assert install_record(project / "collections", "ns.src") is None

    def test_exact_removes_and_keeps(self, project: Path) -> None:
        _ = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src", editable = true }')
        _ = main(["sync", "-q"])
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["sync", "-q"]) == 0

        assert not os.path.lexists(installed_at(project / "collections", "ns.src"))
        assert (project.parent / "src" / "galaxy.yml").exists()

    def test_frozen_galaxy_changed(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')
        _ = main(["lock", "-q"])
        _ = collection_source(src, version="2.0.0")

        assert main(["sync", "--frozen"]) == EXIT_ERROR

        assert "is for ns.src 2.0.0 now, not ns.src 1.0.0 as locked; run alai lock" in capsys.readouterr().err


class TestBuild:
    def test_files_json(self, project: Path) -> None:
        src = collection_source(project.parent / "src")
        (src / "plugins" / "modules" / "run.sh").write_text("#!/bin/sh\n")
        (src / "plugins" / "modules" / "run.sh").chmod(0o755)
        (src / "link.md").symlink_to("README.md")
        (src / "__pycache__").mkdir()
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync", "-q"]) == 0

        target = installed_at(project / "collections", "ns.src")
        files = json.loads((target / "FILES.json").read_text())
        assert [(f["name"], f["ftype"]) for f in files["files"]] == [
            (".", "dir"),
            ("README.md", "file"),
            ("link.md", "file"),
            ("notes.txt", "file"),
            ("plugins", "dir"),
            ("plugins/modules", "dir"),
            ("plugins/modules/hello.py", "file"),
            ("plugins/modules/run.sh", "file"),
        ]
        assert (target / "link.md").readlink() == Path("README.md")
        assert (target / "plugins" / "modules" / "run.sh").stat().st_mode & 0o100
        assert not (target / "notes.txt").stat().st_mode & 0o100

    def test_manifest_directives(self, project: Path) -> None:
        _ = collection_source(project.parent / "src", extra="manifest:\n  directives:\n    - exclude notes.txt\n")
        (project.parent / "src" / "extra.cfg").write_text("x\n")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync", "-q"]) == 0

        names = [
            f["name"]
            for f in json.loads((installed_at(project / "collections", "ns.src") / "FILES.json").read_text())["files"]
        ]
        # "." twice, as ansible-galaxy lists it with manifest directives.
        assert names == [".", ".", "README.md", "plugins", "plugins/modules", "plugins/modules/hello.py"]

    def test_missing_build_metadata(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = project.parent / "src"
        src.mkdir()
        _ = (src / "galaxy.yml").write_text("namespace: ns\nname: src\nversion: 1.0.0\n")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync"]) == EXIT_ERROR

        assert "is missing readme, authors, which ansible-galaxy needs" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            ("build_ignore: ['x']\nmanifest: {}\n", '"build_ignore" and "manifest" are mutually exclusive'),
            ("manifest: {bad: 1}\n", '"manifest" may only have "directives" and "omit_default_directives"'),
            ("manifest: {directives: x}\n", '"manifest.directives" must be a list'),
            ("manifest: {omit_default_directives: 1}\n", '"manifest.omit_default_directives" must be a boolean'),
            ("manifest: {omit_default_directives: true}\n", "which would install nothing"),
            ("manifest: {directives: ['frobnicate x']}\n", "invalid manifest directive 'frobnicate x'"),
        ],
    )
    def test_refused(self, project: Path, capsys: pytest.CaptureFixture[str], extra: str, message: str) -> None:
        _ = collection_source(project.parent / "src", extra=extra)
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync"]) == EXIT_ERROR

        assert message in capsys.readouterr().err
        assert not os.path.lexists(installed_at(project / "collections", "ns.src"))

    def test_outside_directory_link_skipped(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        (project.parent / "elsewhere").mkdir()
        (src / "out").symlink_to(project.parent / "elsewhere")
        (src / "in").symlink_to("plugins")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync"]) == 0

        assert "a symlink to a directory outside the collection" in capsys.readouterr().err
        assert not os.path.lexists(installed_at(project / "collections", "ns.src") / "out")
        assert (installed_at(project / "collections", "ns.src") / "in").readlink() == Path("plugins")

    def test_symlinks_as_collection_build_makes_them(self, project: Path) -> None:
        src = collection_source(project.parent / "src")
        outside = project.parent / "outside"
        outside.mkdir()
        _ = (outside / "tool.sh").write_text("#!/bin/sh\n")
        (outside / "tool.sh").chmod(0o755)
        (src / "abs_in.md").symlink_to(src / "README.md")
        (src / "dirlink").symlink_to("plugins")
        (src / "rel_out.sh").symlink_to("../outside/tool.sh")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync", "-q"]) == 0

        target = installed_at(project / "collections", "ns.src")
        # Inside the collection: a relative link. Outside: the file itself, so the copy needs nothing outside it.
        assert (target / "abs_in.md").readlink() == Path("README.md")
        assert (target / "dirlink").readlink() == Path("plugins")
        assert not (target / "rel_out.sh").is_symlink()
        assert (target / "rel_out.sh").read_text() == "#!/bin/sh\n"
        assert (target / "rel_out.sh").stat().st_mode & 0o100

    def test_broken_symlink(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src")
        (src / "gone.md").symlink_to("nothing.md")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["sync"]) == EXIT_ERROR

        assert "gone.md is a symlink to" in capsys.readouterr().err


class TestReasons:
    def test_lock_verbose(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "c", "c.c")
        write_sources(project, '"c.c" = { index = "alai" }', '"c.c"')
        _ = main(["lock", "-q"])
        write_sources(project, '"c.c" = { path = "../c" }', '"c.c"')

        assert main(["lock", "-v"]) == 0
        err = capsys.readouterr().err
        assert "Updated c.c v1.0.0, now from ../c" in err
        assert "c.c now comes from ../c" in err

        _ = collection_source(src, "c.c", "1.1.0")
        assert main(["lock", "-v"]) == 0
        err = capsys.readouterr().err
        assert "Updated c.c v1.0.0 -> v1.1.0" in err
        assert "c.c's galaxy.yml now says 1.1.0" in err


class TestBuiltDirectory:
    """A directory holding a built or installed collection, with a MANIFEST.json and FILES.json rather than a
    galaxy.yml: copied as it is, as an artifact is extracted."""

    def built(self, project: Path, dependencies: dict[str, str] | None = None) -> Path:
        from alai_core.semver import Version

        from alai._build import build

        src = collection_source(project.parent / "src-source", dependencies=dependencies)
        built = project.parent / "built"
        build(str(src), str(built), "ns.src", Version("1.0.0"), umask=0o022)
        return built

    def test_copied_as_it_is(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        built = self.built(project, {"c.c": "*"})
        _ = (built / "extra.txt").write_text("not built\n")
        write_sources(project, '"ns.src" = { path = "../built" }')

        assert main(["sync"]) == 0

        assert lines(capsys.readouterr().err)[-2:] == [" + c.c==1.0.0", f" + ns.src==1.0.0 (from {built})"]
        target = installed_at(project / "collections", "ns.src")
        for name in ("MANIFEST.json", "FILES.json"):
            assert (target / name).read_bytes() == (built / name).read_bytes()
        assert (target / "plugins" / "modules" / "hello.py").exists()
        # Only what FILES.json lists: the build didn't have it.
        assert not (target / "extra.txt").exists()
        cache_keys = _record_keys(project)
        assert cache_keys["MANIFEST.json"] is not None and cache_keys["galaxy.yml"] is None

    def test_locked_from_manifest(self, project: Path) -> None:
        _ = self.built(project, {"a.a": ">=2.0.0"})
        write_sources(project, '"ns.src" = { path = "../built" }')

        assert main(["lock", "-q"]) == 0

        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None
        entry = lock.get("ns.src")
        assert entry is not None and entry.requirements == ("a.a>=2.0.0",)
        assert entry.source == DirectorySource("../built")

    def test_check_names_manifest(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        built = self.built(project, {"c.c": "*"})
        write_sources(project, '"ns.src" = { path = "../built" }')
        _ = main(["lock", "-q"])
        manifest = json.loads((built / "MANIFEST.json").read_text())
        manifest["collection_info"]["dependencies"] = {"d.d": "*"}
        _ = (built / "MANIFEST.json").write_text(json.dumps(manifest))

        assert main(["lock", "--check"]) == 1

        err = capsys.readouterr().err
        assert "  MANIFEST.json dependencies: removed c.c\n  MANIFEST.json dependencies: added d.d\n" in err

    @pytest.mark.parametrize(
        ("damage", "message"),
        [
            ("edit", "plugins/modules/hello.py doesn't match its checksum in FILES.json"),
            ("files", "FILES.json doesn't match the checksum"),
            ("no-files", "has a MANIFEST.json but no FILES.json"),
            ("missing", "isn't there, though the collection lists it"),
        ],
    )
    def test_refused(self, project: Path, capsys: pytest.CaptureFixture[str], damage: str, message: str) -> None:
        built = self.built(project)
        if damage == "edit":
            _ = (built / "plugins" / "modules" / "hello.py").write_text("# changed\n")
        elif damage == "files":
            _ = (built / "FILES.json").write_text((built / "FILES.json").read_text() + "\n")
        elif damage == "no-files":
            (built / "FILES.json").unlink()
        else:
            (built / "plugins" / "modules" / "hello.py").unlink()
        write_sources(project, '"ns.src" = { path = "../built" }')

        assert main(["sync"]) == EXIT_ERROR

        assert message in capsys.readouterr().err
        assert not os.path.lexists(installed_at(project / "collections", "ns.src"))

    def test_editable(self, project: Path) -> None:
        built = self.built(project)
        write_sources(project, '"ns.src" = { path = "../built", editable = true }')

        assert main(["sync", "-q"]) == 0

        assert installed_at(project / "collections", "ns.src").readlink() == built


def _record_keys(project: Path) -> dict[str, object]:
    found = install_record(project / "collections", "ns.src")
    assert found is not None
    keys = found["cache_keys"]
    assert isinstance(keys, dict)
    return keys  # pyright: ignore[reportUnknownVariableType]


class TestAddRemove:
    def test_add_directory(self, project: Path) -> None:
        _ = collection_source(project.parent / "src")
        _ = (project / "alai.toml").write_text('[project]\ncollections = ["c.c"]\n')
        (project / "sub").mkdir()
        os.chdir(project / "sub")

        assert main(["add", "-q", "../../src"]) == 0

        assert (project / "alai.toml").read_text() == (
            '[project]\ncollections = [\n    "c.c",\n    "ns.src",\n]\n\n'
            + '[project.sources]\n"ns.src" = { path = "../src" }\n'
        )
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

    def test_add_editable_replaces(self, project: Path) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }')

        assert main(["add", "-q", "--editable", "../src"]) == 0

        assert '"ns.src" = { path = "../src", editable = true }' in (project / "alai.toml").read_text()
        assert installed_at(project / "collections", "ns.src").readlink() == src

    def test_add_editable_is_per_directory(self, project: Path) -> None:
        # -e DIR takes one directory, as pip install -e does; what else is given is added as it would be without it.
        src = collection_source(project.parent / "src")
        _ = collection_source(project.parent / "other", "ns.other")
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", "-q", "--editable", "../src", "../other", "d.d"]) == 0

        assert (project / "alai.toml").read_text() == (
            '[project]\ncollections = [\n    "d.d>=1.0.0",\n    "ns.other",\n    "ns.src",\n]\n\n'
            + '[project.sources]\n"ns.other" = { path = "../other" }\n"ns.src" = { path = "../src", editable = true }\n'
        )
        assert installed_at(project / "collections", "ns.src").readlink() == src
        assert not installed_at(project / "collections", "ns.other").is_symlink()

    def test_add_needs_something(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        with pytest.raises(SystemExit):
            _ = main(["add"])

        assert "REQUIREMENT or --editable" in capsys.readouterr().err

    def test_remove_drops_source(self, project: Path) -> None:
        _ = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src" }\n"c.c" = { index = "alai" }', '"ns.src", "c.c"')
        _ = main(["sync", "-q"])

        assert main(["remove", "-q", "ns.src", "c.c"]) == 0

        assert (project / "alai.toml").read_text() == (
            '[project]\ncollections = []\n\n[project.sources]\n"c.c" = { index = "alai" }\n'
        )
        assert not os.path.lexists(installed_at(project / "collections", "ns.src"))


class TestCollectionInstall:
    def test_directory_copied_every_time(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = collection_source(project.parent / "src", dependencies={"c.c": "*"})
        _ = (project / "alai.toml").write_text("")

        assert main(["collection", "install", "../src"]) == 0
        assert lines(capsys.readouterr().err)[-2:] == [" + c.c==1.0.0", f" + ns.src==1.0.0 (from {src})"]

        assert main(["collection", "install", "../src"]) == 0
        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from {src})"

    def test_editable(self, project: Path) -> None:
        src = collection_source(project.parent / "src")
        _ = (project / "alai.toml").write_text("")

        assert main(["collection", "install", "-q", "-e", "../src"]) == 0

        assert installed_at(project / "collections", "ns.src").readlink() == src

    def test_needs_something(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            _ = main(["collection", "install"])

        assert "REQUIREMENT or --editable" in capsys.readouterr().err


class TestRunAndEnv:
    def test_debugger_mapping(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = collection_source(project.parent / "src")
        write_sources(project, '"ns.src" = { path = "../src", editable = true }')
        _ = main(["sync", "-q"])
        monkeypatch.setenv("_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG", "{}")
        from alai._config import load_settings
        from alai._environ import ansible_environment
        from alai._project import load_project

        settings = load_settings()
        _, manifest = load_project(settings, "")
        environ = ansible_environment(settings, manifest)

        assert environ["PYDEVD_RESOLVE_SYMLINKS"] == "1"
        assert "ANSIBLE_TEST_CONTENT_ROOT" not in environ
        mapping = json.loads(environ["_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG"])["source_mapping"]
        assert mapping == {f"{src}/": f"{installed_at(project / 'collections', 'ns.src')}/"}
