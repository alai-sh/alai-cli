# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Installing collections from the artifact cache into ``<collections_dir>/ansible_collections``.

Every ``ansible_collections/<namespace>/<name>`` directory is an installed collection, its version read from its
``MANIFEST.json``, as uv reads each package's installed metadata: a locked collection at its locked version is left as
it is, anything else under a locked name is replaced, and with ``exact`` anything the lock doesn't list is removed. The
design is in ``design/install.md``.
"""

from __future__ import annotations

import collections.abc as _c
import contextlib
import errno
import fcntl
import glob
import json
import os
import secrets
import shutil
import stat
import sys

from ._common import Error, Record
from ._config import LinkMode
from ._output import debug, trace, warn
from ._semver import Version

_TEMP = ".alai-"
"""The prefix of alai's temporary directories beside a namespace's collections."""
_FICLONE = 0x40049409
"""Linux's ``FICLONE`` ioctl, ``_IOW(0x94, 9, int)``, as x86 and ARM encode it, for Pythons before 3.12 that don't
have ``fcntl.FICLONE``. Architectures that encode it differently (powerpc, mips, sparc) fall back from cloning there."""


class InstallError(Error):
    """A collection couldn't be installed or removed."""


class Installed(Record):
    """A directory, or symlink, at ``ansible_collections/<namespace>/<name>``."""

    __slots__ = ("name", "path", "version")

    def __init__(self, name: str, version: Version | None, path: str) -> None:
        self.name = name
        self.version = version
        """None when there's no ``MANIFEST.json`` or it has no readable version, which never matches a lock."""
        self.path = path

    def __str__(self) -> str:
        return f"{self.name} {self.version if self.version is not None else '(unknown version)'}"


class Plan(Record):
    __slots__ = ("install", "remove", "replace", "unchanged", "unlink")

    def __init__(
        self,
        install: _c.Sequence[str],
        remove: _c.Sequence[Installed],
        unchanged: _c.Sequence[str],
        unlink: _c.Sequence[str] = (),
        replace: _c.Sequence[Installed] = (),
    ) -> None:
        self.install = tuple(install)
        """Locked collections to install, replacing any other version already there, by name."""
        self.replace = tuple(replace)
        """What's installed now under the names in ``install``, which installing them replaces."""
        self.remove = tuple(remove)
        """Installed collections the lock doesn't list."""
        self.unchanged = tuple(unchanged)
        """Locked collections already installed at their locked version."""
        self.unlink = tuple(unlink)
        """Namespace symlinks to unlink, without touching what they point at: those a locked collection needs as a
        real directory, and with ``exact`` every one."""


def namespaces_dir(collections_dir: str) -> str:
    return os.path.join(collections_dir, "ansible_collections")


def installed(collections_dir: str) -> dict[str, Installed]:
    """The installed collections by name: every directory or symlink at ``<namespace>/<name>``, named by its path as
    Ansible loads it, whether or not it has a ``MANIFEST.json``.

    A namespace that's a symlink isn't looked into: what's behind it is somewhere else, which alai never writes to or
    deletes from, so none of it counts as installed (see ``namespace_links``).
    """
    found: dict[str, Installed] = {}
    root = namespaces_dir(collections_dir)
    for namespace in _subdirs(root):
        if os.path.islink(os.path.join(root, namespace)):
            continue
        for short in _subdirs(os.path.join(root, namespace)):
            path = os.path.join(root, namespace, short)
            found[f"{namespace}.{short}"] = Installed(
                f"{namespace}.{short}", _manifest_version(os.path.join(path, "MANIFEST.json")), path
            )
    return found


def namespace_links(collections_dir: str) -> dict[str, str]:
    """The namespaces that are symlinks, broken or not, by name, with their paths."""
    root = namespaces_dir(collections_dir)
    return {n: os.path.join(root, n) for n in _subdirs(root) if os.path.islink(os.path.join(root, n))}


def plan(
    collections_dir: str,
    locked: _c.Mapping[str, Version],
    *,
    exact: bool,
    keep_unversioned: bool = False,
    reinstall: bool | _c.Set[str] = False,
) -> Plan:
    """What makes ``collections_dir`` hold the ``locked`` versions: matched by version alone, as uv does, so a
    collection edited in place is left as it is, and one with no readable version is replaced, unless
    ``keep_unversioned`` says to count it as the version wanted. ``reinstall``, every locked collection or the names
    given, replaces one at its locked version all the same."""
    present = installed(collections_dir)
    needed = {name.split(".")[0] for name in locked}
    unlink = [
        path for namespace, path in sorted(namespace_links(collections_dir).items()) if exact or namespace in needed
    ]
    install: list[str] = []
    unchanged: list[str] = []
    for name, version in sorted(locked.items()):
        have = present.get(name)
        again = reinstall is True or (reinstall is not False and name in reinstall)
        kept = have is not None and (
            (have.version == version and not again) or (keep_unversioned and have.version is None)
        )
        (unchanged if kept else install).append(name)
    remove = [c for name, c in sorted(present.items()) if name not in locked] if exact else []
    replace = [present[name] for name in install if name in present]
    return Plan(install, remove, unchanged, unlink, replace)


def install(collections_dir: str, name: str, source: str, linker: Linker) -> None:
    """Install the collection extracted at ``source`` as ``name``, replacing what's there.

    The new copy is made beside it and renamed into place, so the collection is never seen half made; whatever was
    there is renamed aside first and removed after, and so are ansible-galaxy's ``.info`` records of it.
    """
    namespace, short = name.split(".")
    parent = os.path.join(namespaces_dir(collections_dir), namespace)
    target = os.path.join(parent, short)
    try:
        if os.path.islink(parent):
            # Never written through: the namespace becomes a real directory here, and what it pointed at is left.
            unlink_namespace(parent)
        os.makedirs(parent, exist_ok=True)
        _remove_leftovers(parent, short)
        temp = os.path.join(parent, f"{_TEMP}new-{short}-{secrets.token_hex(4)}")
        try:
            linker.tree(source, temp)
            old = None
            if os.path.lexists(target):
                old = os.path.join(parent, f"{_TEMP}old-{short}-{secrets.token_hex(4)}")
                os.rename(target, old)
            os.rename(temp, target)
        except BaseException:
            _rmtree(temp)
            raise
        if old is not None:
            _rmtree(old)
        _remove_galaxy_info(collections_dir, name)
    except OSError as e:
        raise InstallError(f"can't install {name} to {target}: {_reason(e)}") from None
    trace(f"{name}: installed to {target} from {source}")


def unlink_namespace(path: str) -> None:
    """Remove a namespace symlink, never what it points at."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise InstallError(f"can't remove the namespace link {path}: {_reason(e)}") from None
    trace(f"unlinked the namespace {path}")


def remove(collections_dir: str, collection: Installed) -> None:
    """Remove an installed collection, its ansible-galaxy ``.info`` records, and its namespace directory if that's left
    empty."""
    try:
        _rmtree(collection.path, missing_ok=False)
        _remove_galaxy_info(collections_dir, collection.name)
        parent = os.path.dirname(collection.path)
        if not os.path.islink(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except OSError as e:
        raise InstallError(f"can't remove {collection.name} from {collection.path}: {_reason(e)}") from None
    trace(f"{collection.name}: removed {collection.path}")


class Linker:
    """Puts a cached collection's files into place in one ``link_mode``.

    ``auto`` tries a clone, then a hardlink, then a copy, and keeps to the first that works for the rest of the run,
    as uv does; falling back to a copy warns once, since it's slower and uses more disk. Any other mode fails rather
    than fall back.
    """

    def __init__(self, mode: LinkMode) -> None:
        self.mode = mode
        self._auto = [LinkMode.CLONE, LinkMode.HARDLINK, LinkMode.COPY] if mode is LinkMode.AUTO else [mode]

    def tree(self, source: str, dest: str) -> None:
        """Recreate the directory ``source`` as ``dest``: directories and symlinks as they are, files linked."""
        os.mkdir(dest, stat.S_IMODE(os.stat(source).st_mode))
        for directory, dirs, files in os.walk(source):
            relative = os.path.relpath(directory, source)
            target_dir = dest if relative == "." else os.path.join(dest, relative)
            for name in dirs + files:
                src = os.path.join(directory, name)
                dst = os.path.join(target_dir, name)
                if os.path.islink(src):
                    os.symlink(os.readlink(src), dst)
                elif name in dirs:
                    os.mkdir(dst, stat.S_IMODE(os.stat(src).st_mode))
                else:
                    self.file(src, dst)
            # os.walk doesn't descend into symlinked directories, which were recreated as links above.
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(directory, d))]

    def file(self, source: str, dest: str) -> None:
        while True:
            mode = self._auto[0]
            try:
                _LINKS[mode](source, dest)
                return
            except OSError as e:
                if len(self._auto) == 1 or e.errno in (errno.ENOENT, errno.ENOSPC, errno.EDQUOT):
                    if self.mode is LinkMode.AUTO:
                        raise
                    raise OSError(e.errno, f"can't {mode.value} {source}: {_reason(e)}") from None
                _ = self._auto.pop(0)
                fallback = self._auto[0]
                message = f"can't {mode.value} files from the cache ({_reason(e)}); using {fallback.value} instead"
                if fallback is LinkMode.COPY:
                    warn(f'{message}, which is slower and uses more disk; set link_mode = "copy" to silence this')
                else:
                    debug(message)
                _remove_partial(dest)


def _clone(source: str, dest: str) -> None:
    if sys.platform == "darwin":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.clonefile(os.fsencode(source), os.fsencode(dest), 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    elif sys.platform == "linux":
        with open(source, "rb") as src, open(dest, "xb") as dst:
            _ = fcntl.ioctl(dst.fileno(), _ficlone(), src.fileno())
        shutil.copymode(source, dest)
    else:
        raise OSError(errno.EOPNOTSUPP, "cloning isn't supported on this platform")


def _ficlone() -> int:
    """The ``FICLONE`` request: Python's own from 3.12, which is right for every architecture, else x86 and ARM's."""
    request: int = getattr(fcntl, "FICLONE", _FICLONE)
    return request


def _hardlink(source: str, dest: str) -> None:
    os.link(source, dest)


def _copy(source: str, dest: str) -> None:
    _ = shutil.copyfile(source, dest)
    shutil.copymode(source, dest)


def _symlink(source: str, dest: str) -> None:
    os.symlink(os.path.abspath(source), dest)


_LINKS: dict[LinkMode, _c.Callable[[str, str], None]] = {
    LinkMode.CLONE: _clone,
    LinkMode.HARDLINK: _hardlink,
    LinkMode.COPY: _copy,
    LinkMode.SYMLINK: _symlink,
}


def _manifest_version(path: str) -> Version | None:
    try:
        with open(path, "rb") as f:
            data = json.load(f)
        text = data["collection_info"]["version"]
        return Version(text) if isinstance(text, str) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _subdirs(path: str) -> list[str]:
    """The directories and symlinks in ``path`` that could be a namespace or collection: not hidden, and not
    ``*.info``. A broken symlink counts, so it's replaced or removed like anything else there."""
    try:
        names = os.listdir(path)
    except FileNotFoundError:
        return []
    return sorted(
        n
        for n in names
        if not n.startswith(".")
        and not n.endswith(".info")
        and (os.path.isdir(os.path.join(path, n)) or os.path.islink(os.path.join(path, n)))
    )


def _remove_leftovers(parent: str, short: str) -> None:
    """alai's temporary directories for this collection, left by a run that was killed."""
    for path in glob.glob(os.path.join(glob.escape(parent), f"{_TEMP}*-{glob.escape(short)}-*")):
        _rmtree(path)


def _remove_galaxy_info(collections_dir: str, name: str) -> None:
    """ansible-galaxy's ``<ns>.<name>-<version>.info`` records of a collection alai has replaced or removed, which
    would otherwise describe a copy that's gone."""
    pattern = os.path.join(glob.escape(namespaces_dir(collections_dir)), f"{glob.escape(name)}-*.info")
    for path in glob.glob(pattern):
        _rmtree(path)


def _remove_partial(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def _rmtree(path: str, *, missing_ok: bool = True) -> None:
    if os.path.islink(path):
        os.remove(path)
        return
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        if not missing_ok:
            raise


def _reason(error: OSError) -> str:
    return error.strerror or str(error)
