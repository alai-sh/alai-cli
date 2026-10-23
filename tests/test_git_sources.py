# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Git sources: a collection built from a commit of a git repository, its rev locked to the commit, and a source's
version in place of a placeholder in galaxy.yml."""

from __future__ import annotations

import collections.abc as _c
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from helpers import Repo, install_record, installed_at, lines, write_sources

from alai import _git
from alai._cli import main
from alai._common import EXIT_ERROR, ConfigError
from alai._git import _hint  # pyright: ignore[reportPrivateUsage]
from alai._lock import GitCommitSource, read_lock, render_lock
from alai._manifest import GitSource, parse_git
from alai._native import toml_loads
from alai._semver import Version


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


def manifest_version(project: Path, name: str = "ns.src") -> str:
    data = json.loads((installed_at(project / "collections", name) / "MANIFEST.json").read_text())
    return str(data["collection_info"]["version"])


def entry(project: Path, name: str = "ns.src") -> dict[str, object]:
    data = toml_loads((project / "alai.lock").read_text())
    return next(c for c in data["collection"] if c["name"] == name)


class TestLock:
    def test_tag(self, project: Path, repo: Repo) -> None:
        repo.collection(dependencies={"a.a": ">=2.0.0"})
        _ = (repo.path / "meta").mkdir()
        _ = (repo.path / "meta" / "runtime.yml").write_text("requires_ansible: '>=2.16'\n")
        commit = repo.commit()
        _ = repo.git("tag", "--annotate", "v1.0.0", "-m", "v1.0.0")
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", rev = "v1.0.0" }}')

        assert main(["lock", "-q"]) == 0

        data = toml_loads((project / "alai.lock").read_text())
        assert data["sources"] == {"ns.src": {"git": repo.url, "rev": "v1.0.0"}}
        # An annotated tag is locked as the commit it points at, not the tag object.
        assert entry(project) == {
            "name": "ns.src",
            "version": "1.0.0",
            "source": {"git": repo.url, "rev": "v1.0.0", "commit": commit},
            "requires_ansible": ">=2.16",
            "requirements": ["a.a>=2.0.0"],
            "dependencies": ["a.a"],
        }
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and render_lock(lock) == (project / "alai.lock").read_text()
        assert lock.get("ns.src") is not None and lock.get("ns.src").source == GitCommitSource(  # pyright: ignore[reportOptionalMemberAccess]
            repo.url, commit, rev="v1.0.0"
        )

    def test_default_branch(self, project: Path, repo: Repo) -> None:
        repo.collection()
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')

        assert main(["lock", "-q"]) == 0

        assert entry(project)["source"] == {"git": repo.url, "commit": commit}

    def test_branch_kept_until_upgraded(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        first = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", rev = "main" }}')
        _ = main(["lock", "-q"])
        repo.collection(version="1.1.0")
        second = repo.commit()

        # As uv keeps a git dependency's commit: the branch moving doesn't change the lock until it's upgraded.
        assert main(["lock"]) == 0
        assert entry(project)["source"] == {"git": repo.url, "rev": "main", "commit": first}
        _ = capsys.readouterr()

        assert main(["lock", "-P", "ns.src"]) == 0

        assert entry(project)["source"] == {"git": repo.url, "rev": "main", "commit": second}
        assert "Updated ns.src v1.0.0 -> v1.1.0" in capsys.readouterr().err

    def test_same_version_new_commit(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')
        _ = main(["lock", "-q"])
        repo.collection(readme="changed\n")
        second = repo.commit()

        assert main(["lock", "-U", "-v"]) == 0

        err = capsys.readouterr().err
        assert f"Updated ns.src v1.0.0, now from git+{repo.url}@{second}" in err
        assert f"git+{repo.url} is now at commit {second[:7]}" in err

    def test_full_commit(self, project: Path, repo: Repo) -> None:
        repo.collection()
        first = repo.commit()
        repo.collection(version="2.0.0")
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", rev = "{first}" }}')

        assert main(["sync", "-q"]) == 0

        assert entry(project)["version"] == "1.0.0"
        assert manifest_version(project) == "1.0.0"

    def test_abbreviated_commit(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", rev = "{commit[:7]}" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert f"has no branch or tag '{commit[:7]}'; give a commit by its full hash" in capsys.readouterr().err

    def test_no_such_rev(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", rev = "nope" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert f"{repo.url} has no branch or tag 'nope'" in capsys.readouterr().err

    def test_wrong_collection(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection(name="ns.other")
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert "takes ns.src from git+file://" in capsys.readouterr().err

    def test_unreachable(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        write_sources(project, f'"ns.src" = {{ git = "{(tmp_path / "missing").as_uri()}" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert "git ls-remote failed:" in capsys.readouterr().err

    def test_no_git(
        self, project: Path, repo: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')
        monkeypatch.setenv("PATH", str(project))

        assert main(["lock"]) == EXIT_ERROR

        assert "git isn't installed, or isn't on PATH, and git sources need it" in capsys.readouterr().err


class TestSubdirectory:
    def test_lock_and_sync(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection("chocolatey", name="ns.src")
        repo.collection("other", name="ns.other")
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", subdirectory = "chocolatey" }}')

        assert main(["sync"]) == 0

        assert entry(project)["source"] == {"git": repo.url, "subdirectory": "chocolatey", "commit": commit}
        assert lines(capsys.readouterr().err)[-1] == (
            f" + ns.src==1.0.0 (from git+{repo.url}@{commit}#subdirectory=chocolatey)"
        )
        assert (installed_at(project / "collections", "ns.src") / "plugins" / "modules" / "hello.py").exists()
        assert install_record(project / "collections", "ns.src") == {
            "source": {"git": repo.url, "commit": commit, "subdirectory": "chocolatey"}
        }

    def test_not_a_collection(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection("chocolatey")
        repo.collection("other", name="ns.other")
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')

        assert main(["lock"]) == EXIT_ERROR

        err = capsys.readouterr().err
        assert (
            "has no galaxy.yml or MANIFEST.json, so it isn't a collection; collections are in chocolatey, other" in err
        )
        assert "give one as its subdirectory" in err

    def test_missing(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection("chocolatey")
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", subdirectory = "nope" }}')

        assert main(["lock"]) == EXIT_ERROR

        assert f"{repo.url}@{commit[:7]} has no directory nope; collections are in chocolatey" in (
            capsys.readouterr().err
        )


class TestSync:
    def test_sync(self, project: Path, repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')

        assert main(["sync"]) == 0

        # Fetched into the cache when it was locked, saying so as uv does, so there's nothing to prepare.
        assert lines(capsys.readouterr().err) == [
            f"Updating ns.src from {repo.url} (HEAD)",
            f"Updated ns.src from {repo.url} ({commit})",
            "Resolved 1 collection",
            "Installed 1 collection",
            f" + ns.src==1.0.0 (from git+{repo.url}@{commit})",
        ]
        assert manifest_version(project) == "1.0.0"
        assert (installed_at(project / "collections", "ns.src") / "README.md").read_text() == "readme\n"
        assert install_record(project / "collections", "ns.src") == {"source": {"git": repo.url, "commit": commit}}
        # Only the files are cached, without .git.
        cached = list((tmp_path / "cache" / "git-v1").glob(f"*/{commit}"))
        assert len(cached) == 1 and (cached[0] / "galaxy.yml").exists() and not (cached[0] / ".git").exists()

        assert main(["sync"]) == 0
        assert lines(capsys.readouterr().err) == ["Resolved 1 collection", "Checked 1 collection"]

    def test_frozen_fetches(
        self, project: Path, repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo.collection()
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')
        _ = main(["lock", "-q"])
        shutil.rmtree(tmp_path / "cache")

        assert main(["sync", "--frozen"]) == 0

        assert lines(capsys.readouterr().err)[:4] == [
            f"Updating ns.src from {repo.url} ({commit[:7]})",
            f"Updated ns.src from {repo.url} ({commit})",
            "Prepared 1 collection",
            "Installed 1 collection",
        ]
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

    def test_one_fetch_for_a_commit(
        self, project: Path, repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo.collection("a", name="ns.src")
        repo.collection("b", name="ns.other")
        commit = repo.commit()
        write_sources(
            project,
            f'"ns.src" = {{ git = "{repo.url}", subdirectory = "a" }}\n'
            + f'"ns.other" = {{ git = "{repo.url}", subdirectory = "b" }}',
            '"ns.src", "ns.other"',
        )
        _ = main(["lock", "-q"])
        shutil.rmtree(tmp_path / "cache")

        assert main(["sync", "--frozen"]) == 0

        # Both collections are from one commit, fetched once and named for both.
        assert lines(capsys.readouterr().err)[:2] == [
            f"Updating ns.other, ns.src from {repo.url} ({commit[:7]})",
            f"Updated ns.other, ns.src from {repo.url} ({commit})",
        ]
        assert (installed_at(project / "collections", "ns.other") / "MANIFEST.json").exists()

    def test_offline(self, project: Path, repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')
        _ = main(["sync", "-q"])
        shutil.rmtree(installed_at(project / "collections", "ns.src"))

        # Locked and cached, it needs nothing from the repository.
        assert main(["sync", "-q", "--offline"]) == 0
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

        shutil.rmtree(tmp_path / "cache")
        shutil.rmtree(installed_at(project / "collections", "ns.src"))
        assert main(["sync", "--offline"]) == EXIT_ERROR
        assert f"{repo.url}@{commit[:7]} isn't in the cache, and --offline doesn't fetch it" in capsys.readouterr().err

    def test_new_commit_reinstalls(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection()
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')
        _ = main(["sync", "-q"])
        repo.collection(readme="changed\n")
        second = repo.commit()

        assert main(["sync", "-U"]) == 0

        # The same version from another commit replaces it: the record holds the commit.
        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from git+{repo.url}@{second})"
        assert (installed_at(project / "collections", "ns.src") / "README.md").read_text() == "changed\n"

    def test_server_without_fetch_by_hash(self, project: Path, repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
        repo.collection()
        first = repo.commit()
        repo.collection(version="2.0.0")
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", rev = "{first}" }}')
        # git's first protocol, with a server that only serves the tips of branches and tags by their hash.
        for i, (key, value) in enumerate((("protocol.version", "0"), ("uploadpack.allowAnySHA1InWant", "false"))):
            monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", key)
            monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", value)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "2")

        assert main(["sync", "-q"]) == 0

        assert manifest_version(project) == "1.0.0"

    def test_submodule(self, project: Path, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        inner = Repo(tmp_path / "inner")
        _ = (inner.path / "shared.py").write_text("# shared\n")
        _ = inner.commit()
        repo.collection()
        _ = repo.git(
            "-c", "protocol.file.allow=always", "submodule", "--quiet", "add", inner.url, "plugins/module_utils"
        )
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}" }}')
        # git only fetches a file:// submodule when told to, since CVE-2022-39253.
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")

        assert main(["sync", "-q"]) == 0

        assert (
            installed_at(project / "collections", "ns.src") / "plugins" / "module_utils" / "shared.py"
        ).read_text() == "# shared\n"


class TestSourceVersion:
    def test_placeholder_needs_one(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection("chocolatey", version="{{ REPLACE_VERSION }}")
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", subdirectory = "chocolatey" }}')

        assert main(["lock"]) == EXIT_ERROR

        err = capsys.readouterr().err
        assert (
            f"{repo.url}@{commit[:7]}/chocolatey/galaxy.yml: 'version': invalid version '{{{{ REPLACE_VERSION }}}}'"
            in err
        )
        assert (
            "if it's a placeholder, give the version in [project.sources] for ns.src, such as version = '1.0.0'" in err
        )

    def test_git(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection("chocolatey", version="{{ REPLACE_VERSION }}")
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", subdirectory = "chocolatey", version = "1.5.0" }}')

        assert main(["sync"]) == 0

        assert entry(project)["version"] == "1.5.0"
        assert manifest_version(project) == "1.5.0"
        assert "warning" not in capsys.readouterr().err
        lock = read_lock(str(project / "alai.lock"))
        assert lock is not None and lock.collections[0].source_version
        assert render_lock(lock) == (project / "alai.lock").read_text()

        # A new version in alai.toml locks again, from the cached commit.
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", subdirectory = "chocolatey", version = "1.5.1" }}')
        assert main(["sync", "-q"]) == 0
        assert manifest_version(project) == "1.5.1"

    def test_path(self, project: Path, tmp_path: Path) -> None:
        repo = Repo(tmp_path / "checkout")
        repo.collection(version="")
        write_sources(project, '"ns.src" = { path = "../checkout", version = "2.0.0" }')

        assert main(["sync", "-q"]) == 0

        assert manifest_version(project) == "2.0.0"
        # --frozen builds it from the lock alone, which knows the version is the source's.
        shutil.rmtree(installed_at(project / "collections", "ns.src"))
        assert main(["sync", "-q", "--frozen"]) == 0
        assert manifest_version(project) == "2.0.0"

    def test_editable(self, project: Path, tmp_path: Path) -> None:
        repo = Repo(tmp_path / "checkout")
        repo.collection(version="{{ VERSION }}")
        write_sources(project, '"ns.src" = { path = "../checkout", editable = true, version = "2.0.0" }')

        assert main(["sync", "-q"]) == 0

        assert entry(project)["version"] == "2.0.0"
        assert installed_at(project / "collections", "ns.src").is_symlink()

    def test_warns_when_galaxy_has_one(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo = Repo(tmp_path / "checkout")
        repo.collection(version="1.0.0")
        write_sources(project, '"ns.src" = { path = "../checkout", version = "2.0.0" }')

        assert main(["lock"]) == 0

        # The source's wins, as an override does.
        assert "galaxy.yml has version 1.0.0, but [project.sources] for ns.src gives 2.0.0, which is used" in (
            capsys.readouterr().err
        )
        assert entry(project)["version"] == "2.0.0"

        write_sources(project, '"ns.src" = { path = "../checkout", version = "1.0.0" }')
        assert main(["lock"]) == 0
        assert "has version 1.0.0 too, so [project.sources] for ns.src's 'version' isn't needed" in (
            capsys.readouterr().err
        )

    def test_not_for_a_tarball(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project.parent / "ns-src-1.0.0.tar.gz").write_bytes(b"\x1f\x8b")
        write_sources(project, '"ns.src" = { path = "../ns-src-1.0.0.tar.gz", version = "2.0.0" }')

        assert main(["lock"]) == EXIT_ERROR

        assert "is a tarball, whose MANIFEST.json gives its version, so its source can't give another" in (
            capsys.readouterr().err
        )

    def test_not_for_a_built_directory(self, project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        built = tmp_path / "built"
        built.mkdir()
        info = {"namespace": "ns", "name": "src", "version": "1.0.0", "dependencies": {}}
        _ = (built / "MANIFEST.json").write_text(json.dumps({"collection_info": info}))
        write_sources(project, '"ns.src" = { path = "../built", version = "2.0.0" }')

        assert main(["lock"]) == EXIT_ERROR

        assert "is a built collection, whose MANIFEST.json gives its version" in capsys.readouterr().err

    def test_not_for_a_built_collection_in_git(
        self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        info = {"namespace": "ns", "name": "src", "version": "1.0.0", "dependencies": {}}
        _ = (repo.path / "MANIFEST.json").write_text(json.dumps({"collection_info": info}))
        commit = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "{repo.url}", version = "2.0.0" }}')

        assert main(["lock"]) == EXIT_ERROR

        # Named by the repository and commit, not where the commit's files are in the cache.
        assert (
            f"{repo.url}@{commit[:7]} is a built collection, whose MANIFEST.json gives its version, so "
            + ("[project.sources] for ns.src can't give another: remove 'version' from it")
            in capsys.readouterr().err
        )


class TestAddRemove:
    def test_add(self, project: Path, repo: Repo) -> None:
        repo.collection("chocolatey")
        commit = repo.commit()
        _ = repo.git("tag", "v1")
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", "-q", f"git+{repo.url}@v1#subdirectory=chocolatey"]) == 0

        # As uv writes it: the repository, the directory in it, then the revision.
        assert (project / "alai.toml").read_text() == (
            '[project]\ncollections = [\n    "ns.src",\n]\n\n[project.sources]\n'
            + f'"ns.src" = {{ git = "{repo.url}", subdirectory = "chocolatey", rev = "v1" }}\n'
        )
        assert entry(project)["source"] == {
            "git": repo.url,
            "rev": "v1",
            "subdirectory": "chocolatey",
            "commit": commit,
        }
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

        assert main(["remove", "-q", "ns.src"]) == 0

        assert (project / "alai.toml").read_text() == "[project]\ncollections = []\n\n[project.sources]\n"
        assert not installed_at(project / "collections", "ns.src").exists()

    def test_add_placeholder(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection(version="{{ REPLACE_VERSION }}")
        _ = repo.commit()
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", f"git+{repo.url}"]) == EXIT_ERROR

        assert "give the version after the URL's #, such as #version=1.0.0" in capsys.readouterr().err

        assert main(["add", "-q", f"git+{repo.url}#version=1.5.0"]) == 0

        assert f'"ns.src" = {{ git = "{repo.url}", version = "1.5.0" }}' in (project / "alai.toml").read_text()
        assert manifest_version(project) == "1.5.0"

    def test_add_every_key(self, project: Path, repo: Repo) -> None:
        repo.collection("chocolatey", version="{{ REPLACE_VERSION }}")
        _ = repo.commit()
        _ = repo.git("tag", "1.5.0")
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", "-q", f"git+{repo.url}@1.5.0#subdirectory=chocolatey&version=1.5.0"]) == 0

        assert (
            (project / "alai.toml")
            .read_text()
            .endswith(
                f'"ns.src" = {{ git = "{repo.url}", subdirectory = "chocolatey", rev = "1.5.0", version = "1.5.0" }}\n'
            )
        )

    def test_remove_by_source(
        self, project: Path, repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo.collection("a")
        _ = repo.commit()
        checkout = tmp_path / "checkout"
        Repo(checkout).collection(name="ns.dir")
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")
        _ = main(["add", "-q", f"git+{repo.url}@main#subdirectory=a", "../checkout"])
        _ = capsys.readouterr()

        # Named by its source, with any revision, it's found, and its name given to use instead.
        for given, name in ((f"git+{repo.url}@v9#subdirectory=a", "ns.src"), (str(checkout), "ns.dir")):
            assert main(["remove", given]) == EXIT_ERROR
            assert f"[project.sources] takes {name} from {given}, so run alai remove {name}" in capsys.readouterr().err

        assert main(["remove", f"git+{repo.url}"]) == EXIT_ERROR
        assert f"[project.sources] takes nothing from git+{repo.url}" in capsys.readouterr().err
        assert main(["remove", "not-a-name"]) == EXIT_ERROR
        assert "invalid collection name 'not-a-name'" in capsys.readouterr().err

    def test_add_editable(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (project / "alai.toml").write_text("[project]\ncollections = []\n")

        assert main(["add", "--editable", f"git+{repo.url}"]) == EXIT_ERROR

        assert "is a git repository, which can't be editable" in capsys.readouterr().err


class TestCollectionInstall:
    def test_install(self, project: Path, repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
        repo.collection(dependencies={"c.c": "*"})
        commit = repo.commit()
        (project / "collections" / "ansible_collections").mkdir(parents=True)

        assert main(["collection", "install", f"git+{repo.url}@main"]) == 0
        err = lines(capsys.readouterr().err)
        # Its name isn't known until it's fetched.
        assert err[0] == f"Updating {repo.url} (main)"
        assert err[-2:] == [" + c.c==1.0.0", f" + ns.src==1.0.0 (from git+{repo.url}@{commit})"]

        # The same commit is left as it is; another is installed over it.
        assert main(["collection", "install", f"git+{repo.url}@main"]) == 0
        assert lines(capsys.readouterr().err)[-1] == "Checked 2 collections"
        repo.collection(dependencies={"c.c": "*"}, readme="changed\n")
        second = repo.commit()
        assert main(["collection", "install", f"git+{repo.url}@main"]) == 0
        assert lines(capsys.readouterr().err)[-1] == f" ~ ns.src==1.0.0 (from git+{repo.url}@{second})"

    def test_uninstall_by_source(
        self, project: Path, repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo.collection()
        _ = repo.commit()
        checkout = tmp_path / "checkout"
        Repo(checkout).collection(name="ns.dir")
        (project / "collections" / "ansible_collections").mkdir(parents=True)
        _ = main(["collection", "install", "-q", f"git+{repo.url}", "-e", str(checkout)])
        _ = capsys.readouterr()

        # Found by what alai recorded installing it, or the directory an editable one links to.
        for given, name in ((f"git+{repo.url}@main", "ns.src"), (str(checkout), "ns.dir")):
            assert main(["collection", "uninstall", given]) == EXIT_ERROR
            assert f"{name} came from {given}, so run alai collection uninstall {name}" in capsys.readouterr().err

        assert main(["collection", "uninstall", "https://example.com/x.tar.gz"]) == EXIT_ERROR
        assert "nothing installed is from https://example.com/x.tar.gz" in capsys.readouterr().err


class TestParse:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("git+https://github.com/org/repo", GitSource("https://github.com/org/repo")),
            ("git+https://github.com/org/repo@v1.0", GitSource("https://github.com/org/repo", rev="v1.0")),
            (
                "git+https://github.com/org/repo.git@main#subdirectory=coll/x",
                GitSource("https://github.com/org/repo.git", rev="main", subdirectory="coll/x"),
            ),
            # The @ in the user isn't a revision.
            ("git+ssh://git@github.com/org/repo", GitSource("ssh://git@github.com/org/repo")),
            ("git+ssh://git@github.com/org/repo@v2", GitSource("ssh://git@github.com/org/repo", rev="v2")),
            ("git+file:///srv/repo#subdirectory=./c/", GitSource("file:///srv/repo", subdirectory="c")),
            (
                "git+https://h/r@v1#version=1.5.0&subdirectory=c",
                GitSource("https://h/r", rev="v1", subdirectory="c", version=Version("1.5.0")),
            ),
        ],
    )
    def test_valid(self, text: str, expected: GitSource) -> None:
        assert parse_git(text) == expected
        assert str(parse_git(str(expected))) == str(expected)

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            (
                "git+https://github.com/org/repo#egg=x",
                "after the # a git URL takes subdirectory=<dir> and version=<version>, joined with &, not 'egg=x'",
            ),
            ("git+https://h/r#version=1.0", "version: invalid version '1.0'"),
            ("git+https://h/r#version=1.0.0&version=2.0.0", "'version' is given twice"),
            ("git+https://h/r#subdirectory=", "not 'subdirectory='"),
            (
                "git+git@github.com:org/repo.git@v1",
                "is git's scp-like form, which isn't a URL: write it as git\\+ssh://git@github.com/org/repo.git@v1, "
                + "or git\\+ssh://git@github.com/~/org/repo.git@v1",
            ),
            ("git+../repo", "'../repo' isn't a URL, .* a repository on disk is a file:// URL"),
            ("git+https://github.com/org/repo@", "nothing follows the '@'"),
            ("git+repo", "'repo' isn't a URL"),
            ("git+https:/github.com/org/repo", "'https:/github.com/org/repo' isn't a URL"),
            ("git+https://github.com/org/repo#subdirectory=../x", "must be a relative path inside the repository"),
        ],
    )
    def test_invalid(self, text: str, match: str) -> None:
        with pytest.raises(ConfigError, match=match):
            _ = parse_git(text)


class TestSharing:
    @pytest.fixture
    def ssh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _c.Iterator[Path]:
        """An ``ssh`` on PATH that logs its arguments and runs git's command on this machine, as the server would, and a
        fresh connection sharer; the log's path. Its ``-G`` prints ``controlmaster`` as ``FAKE_CONTROLMASTER`` says,
        ``false`` unless set."""
        log = tmp_path / "ssh.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        script = bin_dir / "ssh"
        _ = script.write_text(
            "#!/bin/sh\n"
            + 'if [ "$1" = -G ]; then echo "controlmaster ${FAKE_CONTROLMASTER:-false}"; exit 0; fi\n'
            + f'echo "$@" >> {log}\nfor last; do :; done\nexec sh -c "$last"\n'
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
        monkeypatch.delenv("GIT_SSH", raising=False)
        sharing = _git._Sharing()  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(_git, "_SHARING", sharing)
        yield log
        sharing.close()

    def test_shared(self, project: Path, repo: Repo, ssh: Path) -> None:
        repo.collection()
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "ssh://git@localhost{repo.path}", rev = "main" }}')

        assert main(["sync", "-q"]) == 0

        # Resolving the branch, then fetching its commit: two commands, through one shared connection.
        calls = ssh.read_text().splitlines()
        assert len(calls) == 2
        assert all("-o ControlMaster=auto" in call and "-o ControlPersist=60" in call for call in calls)
        paths = {next(a for a in call.split() if a.startswith("ControlPath=")) for call in calls}
        assert len(paths) == 1 and paths.pop().startswith("ControlPath=/tmp/alai-ssh-")
        assert (installed_at(project / "collections", "ns.src") / "MANIFEST.json").exists()

    def test_users_ssh_command(self, project: Path, repo: Repo, ssh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo.collection()
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "ssh://git@localhost{repo.path}" }}')
        monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o User=me")

        assert main(["sync", "-q"]) == 0

        # Theirs is used as it is.
        calls = ssh.read_text().splitlines()
        assert calls and all("-o User=me" in c and "ControlMaster" not in c for c in calls)

    def test_users_own_sharing(self, project: Path, repo: Repo, ssh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo.collection()
        _ = repo.commit()
        write_sources(project, f'"ns.src" = {{ git = "ssh://git@localhost{repo.path}" }}')
        # ~/.ssh/config shares connections to the host already, maybe through one that's open and approved.
        monkeypatch.setenv("FAKE_CONTROLMASTER", "auto")

        assert main(["sync", "-q"]) == 0

        calls = ssh.read_text().splitlines()
        assert calls and all("ControlMaster" not in c for c in calls)

    def test_only_ssh_urls(self) -> None:
        is_ssh = _git._is_ssh  # pyright: ignore[reportPrivateUsage]
        assert not is_ssh("https://github.com/o/r")
        assert not is_ssh("file:///srv/r")
        assert is_ssh("ssh://git@github.com/o/r")

    def test_first_connection_alone(self) -> None:
        sharing = _git._Sharing()  # pyright: ignore[reportPrivateUsage]
        order: list[str] = []
        started = threading.Event()

        def first() -> None:
            with sharing.turn("git@github.com:22"):
                _ = started.set()
                time.sleep(0.2)
                order.append("first")

        thread = threading.Thread(target=first)
        thread.start()
        _ = started.wait()
        # A second command to the host waits for the first connection to be made; one to another host doesn't.
        with sharing.turn("git@gitlab.com:22"):
            order.append("other host")
        with sharing.turn("git@github.com:22"):
            order.append("second")
        thread.join()

        assert order == ["other host", "first", "second"]


class TestHint:
    def test_host_key(self) -> None:
        hint = _hint("Host key verification failed.\nfatal: Could not read from remote", "ssh://git@github.com/o/r")
        assert hint is not None and "ssh doesn't trust github.com's key yet" in hint
        assert "running ssh -T git@github.com once" in hint

    def test_port(self) -> None:
        hint = _hint("Host key verification failed.", "ssh://git@gitlab.example.com:2222/o/r.git")
        assert hint is not None and "ssh -T git@gitlab.example.com once" in hint

    def test_publickey(self) -> None:
        hint = _hint("git@github.com: Permission denied (publickey).", "ssh://git@github.com/o/r")
        assert hint is not None and "load yours into an agent with ssh-add" in hint

    def test_credentials(self) -> None:
        said = "fatal: could not read Username for 'https://example.com': terminal prompts disabled"
        hint = _hint(said, "https://example.com/o/r")
        assert hint is not None and "set up a git credential helper" in hint

    def test_other(self) -> None:
        assert _hint("fatal: repository not found", "https://example.com/o/r") is None
