# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

# alai's native code, built from src/rust/ by maturin. Each releases the GIL while it works.

import os

class NoSolution(Exception):
    """``resolve``'s requirements can't all be met: ``str()`` is the explanation, a derivation in uv's words, one
    sentence per line, with a blank line between its parts."""

class ExtractError(Exception):
    """A tarball can't be read, or its collection can't be extracted safely: ``(kind, entry, detail)``.

    ``kind`` is one of ``corrupt`` (``detail`` says why), ``missing``, ``not_file``, ``not_dir``, ``path``, ``link``
    (``detail`` is the link's target), ``escape``, ``unreadable``, ``checksum``, ``too_large`` (``detail`` is the
    limit), ``json`` (``detail`` says why), ``not_object``, ``manifest`` (``detail`` is the collection it names),
    ``manifest_fields``, ``files_list``, ``files_entry`` (``detail`` is the entry, as JSON), ``files_checksum``,
    ``files_special`` or ``files_twice``.
    """

def extract_collection(
    tarball: str | os.PathLike[str],
    dest: str | os.PathLike[str],
    limit: int,
    expected: tuple[str, str, str] | None = None,
    /,
) -> tuple[str, str, str]:
    """Extract the collection artifact ``tarball`` into the new directory ``dest`` and return the namespace, name and
    version its ``MANIFEST.json`` gives, which with ``expected`` must be them.

    Two passes: the first reads only the headers, for each name's last member (``tar -r`` appends a replacement) and
    the last ``MANIFEST.json`` and ``FILES.json``, which are checked, with every entry ``FILES.json`` lists, before
    anything is written; the second writes only those last members, as the first recorded them, each file checked
    against its SHA-256. Every path and symlink must stay inside ``dest``, by name and on disk. Files are ``0644``, or
    ``0755`` if the tarball marks them executable, and directories ``0755``, masked by the process umask. More than
    ``limit`` bytes unpacked, in either pass, is ``too_large``, counting what a hard link copies and a symlink's
    checksum reads; 0 is no limit.
    """

_IndexFields = tuple[str, int, bool, str, str, bool | str, bool, list[tuple[str, str]], str | None]
"""An index line's ``cksum``, ``size``, ``yanked``, ``origin``, ``published``, ``deprecated``, ``upstream_conflict``,
each of ``deps`` as ``(name, req)``, and ``requires_ansible``, each of its JSON type."""

def parse_index_lines(data: bytes, name: str, /) -> tuple[list[tuple[int, str | None, _IndexFields | str]], int]:
    """Read collection ``name``'s index file, ``alai._index.parse_index_file``'s JSON and type checks: each line that
    isn't blank, by number from 1, with its ``vers`` if it got that far and its fields, or why it can't be read; and how
    many lines were skipped because their ``v`` needs a newer alai.

    The checks are in the order, and with the words, of ``_index._parse_line``'s before them, but for the version
    itself, which the caller parses first when there's a ``vers``, so its message wins as it did. A line is blank as
    ``bytes.strip`` sees it, and a UTF-8 byte order mark is skipped, as ``json.loads`` skips one.
    """

_PluginLine = tuple[str, str, str, str | None, str | None, str | None, dict[str, object] | None, bytes]
"""A plugin index line's ``type``, ``name``, ``collection``, ``since``, ``until``, ``short_description`` (each None
where it isn't a string) and ``routing`` (None where it isn't an object, its keys sorted), and the line itself."""

def plugin_lines(data: bytes, tokens: list[bytes], /) -> list[_PluginLine]:
    """Every readable line of the plugin index ``data`` that contains all of ``tokens``, or every one if there are
    none. A line is readable if it's a JSON object with an integer ``v`` no higher than 1, and ``type``, ``name`` and
    ``collection`` strings, the collection with one dot."""

def plugin_lines_named(data: bytes, names: set[str], /) -> list[_PluginLine]:
    """Every readable line, as ``plugin_lines`` reads them, with a ``"name": "..."`` whose value is one of ``names``,
    each once."""

def plugin_names(data: bytes, /) -> set[str]:
    """Every value of a ``"name"`` key in ``data`` without an escape in it, found without parsing the lines."""

def link_tree(source: str | os.PathLike[str], dest: str | os.PathLike[str], mode: str, /) -> None:
    """Recreate the directory ``source`` as the new directory ``dest``: directories with their modes and symlinks as
    they are, each file ``clone``d, ``hardlink``ed, ``copy``d with its mode, or ``symlink``ed to its absolute path. An
    ``OSError`` placing a file names the file in the cache."""

_Key = tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]
"""``alai._semver.Version._key``: SemVer precedence."""
_Clauses = tuple[tuple[str, _Key], ...]
"""A requirement: an operator (``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``) and a version per clause, all of which must
hold; none accepts every version."""
_Entry = tuple[str, _Key, bool, str | None, tuple[tuple[str, _Clauses], ...]]
"""One version: its text, key, whether it's a pre-release, why it can't be chosen (a predicate, ``is yanked``) or None,
and its dependencies by name."""

def resolve(
    packages: dict[str, tuple[_Entry, ...]],
    roots: tuple[tuple[str, _Clauses], ...],
    groups: tuple[tuple[str, tuple[tuple[str, _Clauses], ...]], ...],
    preferred: dict[str, str],
    missing: dict[str, str],
    sources: dict[str, str],
    overrides: frozenset[str],
    root_depends: str,
    root_unsat: str,
    /,
) -> dict[str, str]:
    """Choose one version of every collection ``roots`` and ``groups`` need, from ``packages``, and return each by
    name as its text, or raise ``NoSolution`` with why there's none.

    Of each collection's versions in a range, the highest is chosen, every stable version before any pre-release, but
    ``preferred``'s (the locked or installed version, by text) first while it fits. A version that can't be chosen
    is reported by its reason and passed over, so a failure can say ``a.a==2.0.0 is yanked``. A name in ``missing`` has
    no versions, and the explanation gives its reason (``no configured index has a.a``) instead; a name in ``sources``
    is read from a directory, tarball or commit, described that way (``from ../col``) when its one version doesn't
    fit; a name in ``overrides`` has ``[project.overrides]`` added wherever something depends on it. ``root_depends``
    and ``root_unsat`` are the words for the roots: ``the project depends on`` and ``the project's requirements are
    unsatisfiable``.
    """
