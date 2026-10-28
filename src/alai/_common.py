# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""What several modules share: the error type the CLI reports, and what reading ``alai.toml`` needs in both ``_config``
and ``_manifest``."""

from __future__ import annotations

import collections.abc as _c
import os
import re

# typing.TYPE_CHECKING without importing typing, which this module, loaded by every command, otherwise doesn't need.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from alai_core.toml import Document

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
"""Index and group names: lowercase, so each is spelled one way in files and on the command line."""
SHA256_RE = re.compile(r"[0-9a-f]{64}")
"""A SHA-256 as the index, the lock and the cache write one: lowercase hex."""


class Error(Exception):
    """A failure to report to the user as ``error: <message>``, without a traceback."""


EXIT_ERROR = 2
"""An ``Error`` was reported, or the command line was wrong, as argparse exits."""
EXIT_STALE = 1
"""``--check`` or ``--locked`` found the lock missing, or resolving would change it."""
EXIT_NOT_FOUND = 1
"""Nothing matched, as ``uv pip show`` exits 1 for a package it can't find."""


MAX_THREADS = 4
"""The most extracting, and the most installing, at once, or one per CPU if there are fewer: both write to the disk,
and more writers only contend there. On a 16-thread machine with btrfs, sivel.acd cold took 6.09 s with 16 of each,
5.15 s with 4 and 5.05 s with 2, and 4 was the best warm and for the largest collections; on a 6-CPU VM with XFS, 2 and
3 beat 6. Only on tmpfs did more win (1.45 s with 16 against 1.65 s with 4). The details are in
design/performance.md."""


def worker_threads() -> int:
    """How many artifacts extract, and how many collections install, at once: see ``MAX_THREADS``."""
    return min(os.cpu_count() or 1, MAX_THREADS)


class ConfigError(Error):
    """A config file, environment variable or flag holds an invalid value."""


class Record:
    """A value class with equality and repr from its ``__slots__``.

    Used instead of dataclasses, which cost ~3.5 ms to import and apply on every command.
    """

    __slots__: tuple[str, ...] = ()

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and all(getattr(self, n) == getattr(other, n) for n in self.__slots__)

    def __repr__(self) -> str:
        fields = ", ".join(f"{n}={getattr(self, n)!r}" for n in self.__slots__)
        return f"{type(self).__name__}({fields})"


def as_table(value: object) -> dict[str, object] | None:
    # TOML tables and JSON objects always have string keys.
    return value if isinstance(value, dict) else None  # pyright: ignore[reportUnknownVariableType]


def as_list(value: object) -> list[object] | None:
    # TOML and JSON arrays come back as list[Any]; this names them as list[object] for the type checker.
    return value if isinstance(value, list) else None  # pyright: ignore[reportUnknownVariableType]


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def string_list(value: object) -> list[str] | None:
    """``value`` when it's a list of strings, else None, for the caller to say what it must be."""
    items = as_list(value)
    if items is None or not all(isinstance(v, str) for v in items):
        return None
    return items  # pyright: ignore[reportReturnType]


def json_object(data: bytes) -> dict[str, object]:
    """``data`` as a JSON object; ValueError, ``not valid JSON: ...`` or ``expected a JSON object``, if it isn't."""
    import json

    try:
        obj = json.loads(data)
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}") from None
    found = as_table(obj)
    if found is None:
        raise ValueError("expected a JSON object")
    return found


def sha256_file(path: str) -> str:
    """The SHA-256 of the file at ``path``, read in chunks; OSError if it can't be read."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_url_to_path(url: str) -> str:
    """The path a ``file://`` URL names; ValueError if it names another host, which only Windows can reach."""
    import urllib.parse
    import urllib.request

    parts = urllib.parse.urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        raise ValueError("a file URL can't name another host")
    return urllib.request.url2pathname(parts.path)


def inside(path: str, parent: str) -> bool:
    """Whether ``path`` is ``parent`` or under it. Both are absolute and resolved alike (``os.path.realpath``) by
    the caller, so a symlink doesn't hide one in the other."""
    return os.path.commonpath((path, parent)) == parent


def read_toml(path: str) -> dict[str, object]:
    """The TOML file at ``path``: a config file, ``alai.toml`` or ``alai.lock``."""
    # Imported here so a command run without any config files never loads the extension for it.
    from alai_core.toml import TomlError, read

    try:
        return read(path)
    except (TomlError, OSError) as e:
        raise _toml_error(path, e) from None


def read_toml_document(path: str) -> Document:
    """The TOML file at ``path`` to edit, ``alai.toml`` for ``alai add``, read as ``read_toml`` reads one."""
    from alai_core.toml import Document, TomlError

    try:
        return Document.from_path(path)
    except (TomlError, OSError) as e:
        raise _toml_error(path, e) from None


def home_path(path: str) -> str:
    """``path`` with a leading home directory shortened to ``~``, for display."""
    home = os.path.expanduser("~")
    if home != "~" and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


def _toml_error(path: str, e: Exception) -> ConfigError:
    """A file that can't be read, or isn't UTF-8 or valid TOML, as an error naming it."""
    if isinstance(e, OSError):
        return ConfigError(f"can't read {home_path(path)}: {e.strerror or e}")
    return ConfigError(f"{home_path(path)}: invalid TOML: {e}")


TRUE_WORDS = frozenset(("1", "true", "yes", "on"))
FALSE_WORDS = frozenset(("0", "false", "no", "off"))
"""The words a boolean environment variable takes, in any case."""


def parse_bool(value: str, where: str) -> bool:
    """``value`` as one of the boolean words, in any case; ConfigError naming ``where`` if it's none of them."""
    lowered = value.lower()
    if lowered in TRUE_WORDS:
        return True
    if lowered in FALSE_WORDS:
        return False
    raise ConfigError(f"{where}: expected a boolean (1, true, yes, on, 0, false, no, off), got '{value}'")


def env_bool(environ: _c.Mapping[str, str], name: str) -> bool:
    """The boolean environment variable ``name``: False when unset or empty."""
    value = environ.get(name) or ""
    return parse_bool(value, name) if value else False


def selects(selection: bool | _c.Collection[str], name: str) -> bool:
    """Whether ``name`` is in ``selection``: everything (True), nothing (False), or the names given, as ``--upgrade``
    and ``--upgrade-collection``, or ``--reinstall`` and ``--reinstall-collection``, choose collections."""
    return selection is True or (selection is not False and name in selection)
