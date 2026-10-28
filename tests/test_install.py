# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import concurrent.futures
import errno
import json
import os
import re
import stat
from pathlib import Path

import pytest
from alai_core.semver import Version
from helpers import installed_at

from alai import _install, _native
from alai._config import LinkMode
from alai._install import (
    Installed,
    InstallError,
    Linker,
    install,
    installed,
    namespace_links,
    plan,
    remove,
    unlink_namespace,
)


def collection(path: Path, name: str = "ns.coll", version: str | None = "1.0.0", *, manifest: bool = True) -> Path:
    """A collection directory as the artifact cache holds one: ``MANIFEST.json``, a file, a script and a symlink."""
    namespace, short = name.split(".")
    (path / "plugins" / "modules").mkdir(parents=True)
    if manifest:
        info: dict[str, object] = {"namespace": namespace, "name": short}
        if version is not None:
            info["version"] = version
        _ = (path / "MANIFEST.json").write_text(json.dumps({"collection_info": info}))
    _ = (path / "plugins" / "modules" / "mod.py").write_text(f"# {name} {version}\n")
    (path / "plugins" / "modules" / "alias.py").symlink_to("mod.py")
    run = path / "run.sh"
    _ = run.write_text("#!/bin/sh\n")
    run.chmod(0o755)
    return path


def cached(tmp_path: Path, name: str = "ns.coll", version: str = "1.0.0") -> str:
    return str(collection(tmp_path / "cache" / f"{name}-{version}", name, version))


def leftovers(cdir: Path) -> list[str]:
    return sorted(p.name for p in (cdir / "ansible_collections").glob("*/.alai-*"))


@pytest.fixture
def cdir(tmp_path: Path) -> Path:
    return tmp_path / "collections"


class TestInstalled:
    def test_reads_manifests(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.a"), "ns.a", "1.0.0")
        _ = collection(installed_at(cdir, "ns.b"), "ns.b", None)
        _ = collection(installed_at(cdir, "ns.src"), "ns.src", manifest=False)
        (cdir / "ansible_collections" / "ns.a-1.0.0.info").mkdir()
        (cdir / "ansible_collections" / "ns" / ".alai-new-a-1234").mkdir()

        assert installed(str(cdir)) == {
            "ns.a": Installed("ns.a", Version("1.0.0"), str(installed_at(cdir, "ns.a"))),
            "ns.b": Installed("ns.b", None, str(installed_at(cdir, "ns.b"))),
            "ns.src": Installed("ns.src", None, str(installed_at(cdir, "ns.src"))),
        }

    def test_symlinks_count(self, tmp_path: Path, cdir: Path) -> None:
        checkout = collection(tmp_path / "checkout", "ns.dev", "1.0.0")
        installed_at(cdir, "ns.dev").parent.mkdir(parents=True)
        installed_at(cdir, "ns.dev").symlink_to(checkout)
        installed_at(cdir, "ns.broken").symlink_to(tmp_path / "gone")

        found = installed(str(cdir))

        assert found["ns.dev"].version == Version("1.0.0")
        assert found["ns.broken"].version is None

    def test_nothing_installed(self, cdir: Path) -> None:
        assert installed(str(cdir)) == {}


class TestPlan:
    def test_by_version(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.same"), "ns.same", "1.0.0")
        _ = collection(installed_at(cdir, "ns.other"), "ns.other", "1.0.0")
        _ = collection(installed_at(cdir, "ns.unknown"), "ns.unknown", None)
        _ = collection(installed_at(cdir, "ns.extra"), "ns.extra", "1.0.0")
        locked = {n: Version("1.0.0") for n in ("ns.same", "ns.new", "ns.unknown")} | {"ns.other": Version("2.0.0")}

        result = plan(str(cdir), locked, exact=True)

        assert result.install == ("ns.new", "ns.other", "ns.unknown")
        assert result.unchanged == ("ns.same",)
        assert [c.name for c in result.remove] == ["ns.extra"]

    def test_inexact_keeps_extras(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.extra"), "ns.extra")

        assert plan(str(cdir), {}, exact=False).remove == ()

    def test_edits_dont_count(self, cdir: Path) -> None:
        # Matched by version, as uv does: an edited file is left as it is.
        path = collection(installed_at(cdir, "ns.coll"), "ns.coll", "1.0.0")
        _ = (path / "plugins" / "modules" / "mod.py").write_text("edited\n")

        assert plan(str(cdir), {"ns.coll": Version("1.0.0")}, exact=True).unchanged == ("ns.coll",)

    def test_no_manifest_replaced(self, cdir: Path) -> None:
        # Named by its path, at no version, so it never matches the lock.
        _ = collection(installed_at(cdir, "ns.coll"), manifest=False)

        assert plan(str(cdir), {"ns.coll": Version("1.0.0")}, exact=True).install == ("ns.coll",)

    def test_no_manifest_removed(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.coll"), manifest=False)

        assert [c.name for c in plan(str(cdir), {}, exact=True).remove] == ["ns.coll"]

    def test_reinstall_all(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.a"), "ns.a", "1.0.0")
        _ = collection(installed_at(cdir, "ns.b"), "ns.b", "1.0.0")

        result = plan(str(cdir), {"ns.a": Version("1.0.0"), "ns.b": Version("1.0.0")}, exact=True, reinstall=True)

        assert result.install == ("ns.a", "ns.b")
        assert [c.name for c in result.replace] == ["ns.a", "ns.b"]
        assert result.unchanged == ()

    def test_reinstall_named(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.a"), "ns.a", "1.0.0")
        _ = collection(installed_at(cdir, "ns.b"), "ns.b", "1.0.0")
        locked = {"ns.a": Version("1.0.0"), "ns.b": Version("1.0.0")}

        # One the lock doesn't list is ignored, as uv ignores a --reinstall-package it doesn't install.
        result = plan(str(cdir), locked, exact=True, reinstall={"ns.b", "ns.other"})

        assert result.install == ("ns.b",)
        assert result.unchanged == ("ns.a",)


def describe(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(path.rglob("*")):
        relative = p.relative_to(path).as_posix()
        out[relative] = f"-> {p.readlink()}" if p.is_symlink() else f"{stat.S_IMODE(p.stat().st_mode):o}"
    return out


class TestInstall:
    @pytest.mark.parametrize("mode", [LinkMode.AUTO, LinkMode.HARDLINK, LinkMode.COPY])
    def test_new(self, tmp_path: Path, cdir: Path, mode: LinkMode) -> None:
        source = cached(tmp_path)

        install(str(cdir), "ns.coll", source, Linker(mode))

        target = installed_at(cdir, "ns.coll")
        assert describe(target) == describe(Path(source))
        assert (target / "plugins" / "modules" / "alias.py").readlink() == Path("mod.py")
        # auto clones or hardlinks depending on the filesystem, so only the explicit modes say which.
        if mode is not LinkMode.AUTO:
            same = (target / "run.sh").stat().st_ino == Path(source, "run.sh").stat().st_ino
            assert same is (mode is LinkMode.HARDLINK)
        assert leftovers(cdir) == []

    def test_symlink_mode(self, tmp_path: Path, cdir: Path) -> None:
        source = cached(tmp_path)

        install(str(cdir), "ns.coll", source, Linker(LinkMode.SYMLINK))

        assert (installed_at(cdir, "ns.coll") / "run.sh").readlink() == Path(source, "run.sh")

    def test_replaces(self, tmp_path: Path, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.coll"), version="1.0.0")
        _ = (installed_at(cdir, "ns.coll") / "stale.txt").write_text("from 1.0.0")
        info = cdir / "ansible_collections" / "ns.coll-1.0.0.info"
        info.mkdir()
        other_info = cdir / "ansible_collections" / "ns.collection-1.0.0.info"
        other_info.mkdir()

        install(str(cdir), "ns.coll", cached(tmp_path, version="2.0.0"), Linker(LinkMode.COPY))

        assert installed(str(cdir))["ns.coll"].version == Version("2.0.0")
        assert not (installed_at(cdir, "ns.coll") / "stale.txt").exists()
        assert not info.exists()
        assert other_info.exists()
        assert leftovers(cdir) == []

    def test_failure_keeps_what_was_there(self, tmp_path: Path, cdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ = collection(installed_at(cdir, "ns.coll"), version="1.0.0")

        monkeypatch.setattr(_install, "_link_tree", FakeLinker({"copy": errno.ENOSPC}))

        with pytest.raises(InstallError, match=r"can't install ns\.coll to .*: No space left on device"):
            install(str(cdir), "ns.coll", cached(tmp_path, version="2.0.0"), Linker(LinkMode.COPY))
        assert installed(str(cdir))["ns.coll"].version == Version("1.0.0")
        assert leftovers(cdir) == []

    def test_removes_leftovers_from_a_killed_run(self, tmp_path: Path, cdir: Path) -> None:
        stale = cdir / "ansible_collections" / "ns" / ".alai-new-coll-dead"
        stale.mkdir(parents=True)

        install(str(cdir), "ns.coll", cached(tmp_path), Linker(LinkMode.COPY))

        assert leftovers(cdir) == []


class FakeLinker:
    """Stands in for ``_install._link_tree``, failing in the modes given, as a filesystem without them would, after
    making part of the tree; otherwise the extension links it."""

    def __init__(self, failing: dict[str, int]) -> None:
        self.failing = failing
        self.calls: list[str] = []

    def __call__(self, source: str, dest: str, mode: str) -> None:
        self.calls.append(mode)
        if mode in self.failing:
            Path(dest).mkdir()
            _ = (Path(dest) / "partial").write_text("x")
            code = self.failing[mode]
            raise OSError(code, os.strerror(code), str(Path(source) / "MANIFEST.json"))
        _native.link_tree(source, dest, mode)


class TestLinker:
    def test_auto_falls_back(
        self, tmp_path: Path, cdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = FakeLinker({"clone": errno.EOPNOTSUPP, "hardlink": errno.EXDEV})
        monkeypatch.setattr(_install, "_link_tree", fake)

        install(str(cdir), "ns.coll", cached(tmp_path), Linker(LinkMode.AUTO))

        # Each failed once, its partial tree removed, and copy made the whole one; the warning is given once.
        assert fake.calls == ["clone", "hardlink", "copy"]
        assert describe(installed_at(cdir, "ns.coll")) == describe(Path(cached(tmp_path / "again")))
        err = capsys.readouterr().err
        assert err.count("warning:") == 1
        # The system's own wording: glibc says "Invalid cross-device link", macOS "Cross-device link".
        assert f"can't hardlink files from the cache ({os.strerror(errno.EXDEV)}); using copy instead" in err

    def test_explicit_mode_doesnt_fall_back(self, tmp_path: Path, cdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_install, "_link_tree", FakeLinker({"hardlink": errno.EXDEV}))

        # The cached file it failed on is named.
        message = rf"can't hardlink .*/MANIFEST\.json: {re.escape(os.strerror(errno.EXDEV))}$"
        with pytest.raises(InstallError, match=message):
            install(str(cdir), "ns.coll", cached(tmp_path), Linker(LinkMode.HARDLINK))
        assert leftovers(cdir) == []

    def test_no_space_isnt_fallen_back_from(self, tmp_path: Path, cdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLinker({"clone": errno.ENOSPC})
        monkeypatch.setattr(_install, "_link_tree", fake)

        with pytest.raises(InstallError, match="No space left on device"):
            install(str(cdir), "ns.coll", cached(tmp_path), Linker(LinkMode.AUTO))
        assert fake.calls == ["clone"]

    def test_falls_back_once_across_threads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Every thread finds clone unsupported, but auto moves on from it once, to hardlink, not past it.
        fake = FakeLinker({"clone": errno.EOPNOTSUPP})
        monkeypatch.setattr(_install, "_link_tree", fake)
        linker = Linker(LinkMode.AUTO)
        source = cached(tmp_path)

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda i: linker.tree(source, str(tmp_path / f"out{i}")), range(8)))

        assert "copy" not in fake.calls
        assert all((tmp_path / f"out{i}" / "MANIFEST.json").stat().st_nlink > 1 for i in range(8))

    @pytest.mark.parametrize("mode", [LinkMode.HARDLINK, LinkMode.COPY])
    def test_same_tree_as_the_cache(self, tmp_path: Path, mode: LinkMode) -> None:
        source = cached(tmp_path)
        (Path(source) / "bin").mkdir(mode=0o750)
        _ = (Path(source) / "bin" / "run.sh").write_text("#!/bin/sh\n")
        (Path(source) / "bin" / "run.sh").chmod(0o755)
        (Path(source) / "tools").symlink_to("bin")
        (Path(source) / "bin" / "link.txt").symlink_to("run.sh")

        Linker(mode).tree(source, str(tmp_path / "out"))

        # Directories and files keep their modes, and symlinks, the directory one included, stay links.
        assert describe(tmp_path / "out") == describe(Path(source))
        assert (tmp_path / "out" / "tools").is_symlink()


def test_clone(tmp_path: Path, cdir: Path) -> None:
    # clonefile on macOS, which APFS always supports; FICLONE on Linux, which only some filesystems do (btrfs, XFS),
    # and not the ext4 of CI runners or a tmpfs /tmp, so it's skipped there.
    probe = tmp_path / "probe"
    probe.mkdir()
    _ = (probe / "x").write_text("x")
    try:
        _native.link_tree(str(probe), str(tmp_path / "probe-clone"), "clone")
    except OSError as e:
        pytest.skip(f"{tmp_path} can't clone files: {e.strerror or e}")
    source = cached(tmp_path)

    install(str(cdir), "ns.coll", source, Linker(LinkMode.CLONE))

    target = installed_at(cdir, "ns.coll")
    assert describe(target) == describe(Path(source))
    assert (target / "run.sh").read_text() == Path(source, "run.sh").read_text()
    # A clone is a file of its own, unlike a hardlink.
    assert (target / "run.sh").stat().st_ino != Path(source, "run.sh").stat().st_ino


class TestNamespaceLinks:
    @pytest.fixture
    def shared(self, tmp_path: Path, cdir: Path) -> Path:
        """A namespace symlinked to a directory elsewhere holding ns.a and ns.b."""
        shared = tmp_path / "shared"
        for short in ("a", "b"):
            _ = collection(shared / short, f"ns.{short}")
        (cdir / "ansible_collections").mkdir(parents=True)
        (cdir / "ansible_collections" / "ns").symlink_to(shared)
        return shared

    def test_not_installed(self, cdir: Path, shared: Path) -> None:
        assert installed(str(cdir)) == {}
        assert namespace_links(str(cdir)) == {"ns": str(cdir / "ansible_collections" / "ns")}

    @pytest.mark.parametrize("exact", [True, False])
    def test_locked_namespace_unlinked(self, cdir: Path, shared: Path, exact: bool) -> None:
        # ns.a is behind the link at the locked version, but that copy is elsewhere: it's installed afresh.
        result = plan(str(cdir), {"ns.a": Version("1.0.0")}, exact=exact)

        assert result.install == ("ns.a",)
        assert result.unlink == (str(cdir / "ansible_collections" / "ns"),)
        assert result.remove == ()

    @pytest.mark.parametrize(("exact", "unlinked"), [(True, True), (False, False)])
    def test_unlocked_namespace(self, cdir: Path, shared: Path, exact: bool, unlinked: bool) -> None:
        result = plan(str(cdir), {"other.c": Version("1.0.0")}, exact=exact)

        assert bool(result.unlink) is unlinked

    def test_install_never_writes_through(self, tmp_path: Path, cdir: Path, shared: Path) -> None:
        before = describe(shared)

        install(str(cdir), "ns.a", cached(tmp_path, "ns.a", "2.0.0"), Linker(LinkMode.COPY))

        namespace = cdir / "ansible_collections" / "ns"
        assert not namespace.is_symlink()
        assert installed(str(cdir))["ns.a"].version == Version("2.0.0")
        assert describe(shared) == before

    def test_unlink_leaves_the_target(self, cdir: Path, shared: Path) -> None:
        unlink_namespace(str(cdir / "ansible_collections" / "ns"))

        assert not (cdir / "ansible_collections" / "ns").exists()
        assert (shared / "a" / "MANIFEST.json").is_file()
        assert (shared / "b" / "MANIFEST.json").is_file()


class TestRemove:
    def test_removes_with_info_and_empty_namespace(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.coll"))
        info = cdir / "ansible_collections" / "ns.coll-1.0.0.info"
        info.mkdir()

        remove(str(cdir), installed(str(cdir))["ns.coll"])

        assert not installed_at(cdir, "ns.coll").exists()
        assert not info.exists()
        assert not (cdir / "ansible_collections" / "ns").exists()

    def test_keeps_a_namespace_in_use(self, cdir: Path) -> None:
        _ = collection(installed_at(cdir, "ns.a"), "ns.a")
        _ = collection(installed_at(cdir, "ns.b"), "ns.b")

        remove(str(cdir), installed(str(cdir))["ns.a"])

        assert installed_at(cdir, "ns.b").is_dir()

    def test_symlink_removed_not_its_target(self, tmp_path: Path, cdir: Path) -> None:
        # An editable source will be a link to its checkout: removing or replacing it must leave the checkout.
        checkout = collection(tmp_path / "checkout", "ns.dev", "1.0.0")
        installed_at(cdir, "ns.dev").parent.mkdir(parents=True)
        installed_at(cdir, "ns.dev").symlink_to(checkout)

        remove(str(cdir), installed(str(cdir))["ns.dev"])

        assert not installed_at(cdir, "ns.dev").is_symlink()
        assert (checkout / "MANIFEST.json").is_file()

    def test_symlink_replaced_not_its_target(self, tmp_path: Path, cdir: Path) -> None:
        checkout = collection(tmp_path / "checkout", "ns.coll", "0.1.0")
        installed_at(cdir, "ns.coll").parent.mkdir(parents=True)
        installed_at(cdir, "ns.coll").symlink_to(checkout)

        install(str(cdir), "ns.coll", cached(tmp_path), Linker(LinkMode.COPY))

        assert not installed_at(cdir, "ns.coll").is_symlink()
        assert installed(str(cdir))["ns.coll"].version == Version("1.0.0")
        assert (checkout / "MANIFEST.json").is_file()

    def test_broken_symlink_removed(self, tmp_path: Path, cdir: Path) -> None:
        installed_at(cdir, "ns.coll").parent.mkdir(parents=True)
        installed_at(cdir, "ns.coll").symlink_to(tmp_path / "gone")

        remove(str(cdir), installed(str(cdir))["ns.coll"])

        assert not installed_at(cdir, "ns.coll").is_symlink()

    def test_hardlinked_cache_untouched(self, tmp_path: Path, cdir: Path) -> None:
        source = cached(tmp_path)
        install(str(cdir), "ns.coll", source, Linker(LinkMode.HARDLINK))

        remove(str(cdir), installed(str(cdir))["ns.coll"])

        assert Path(source, "run.sh").is_file()
