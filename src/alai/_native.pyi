# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

# alai's native code, built from src/rust/ by maturin. Each releases the GIL while it works, but for the TOML
# functions, which build Python objects as they go.

import os

class NoSolution(Exception):
    """``resolve``'s requirements can't all be met: ``str()`` is the explanation, a derivation in uv's words, one
    sentence per line, with a blank line between its parts."""

class HttpError(Exception):
    """A request got no usable response: ``(kind, detail)``.

    ``kind`` is one of ``dns``, ``connect``, ``tls``, ``timeout`` (``detail`` is empty), ``redirect``, ``too_large``
    (``detail`` is the limit), ``not_magic`` or ``other``.
    """

class HttpClient:
    """HTTP/2 or HTTP/1.1 over rustls, verified by the OS unless ``SSL_CERT_FILE`` or ``SSL_CERT_DIR`` is set, with
    the environment's proxies. Safe to share between threads; each call blocks its own without the GIL."""

    cert_warnings: list[str]
    """Problems reading ``SSL_CERT_FILE`` or ``SSL_CERT_DIR``."""

    def __init__(
        self,
        user_agent: str,
        /,
        *,
        connect_timeout: float,
        read_timeout: float,
        retries: int,
        backoff: float,
        max_idle: int,
    ) -> None: ...
    def get(
        self, url: str, headers: list[tuple[str, str]], /
    ) -> tuple[int, str, list[tuple[int, str]], str | None, bytes]:
        """``(status, reason, redirects, ETag, body)``, where ``redirects`` is each redirect followed, as its
        status and the URL it led to."""
    def download(
        self, url: str, path: str, max_size: int, magic: bytes, /
    ) -> tuple[int, str, list[tuple[int, str]], str, int]:
        """``(status, reason, redirects, SHA-256, size)``; only a 200 creates ``path``."""
    def proxy(self, url: str, /) -> str | None:
        """The proxy a request to ``url`` goes through, as ``scheme://host:port`` without credentials, or None."""
    def preconnect(self, url: str, /) -> None:
        """Connect to ``url``'s host in the background, so later requests find a connection ready."""

def link_tree(source: str | os.PathLike[str], dest: str | os.PathLike[str], mode: str, /) -> None:
    """Recreate the directory ``source`` as the new directory ``dest``: directories with their modes and symlinks as
    they are, each file ``clone``d, ``hardlink``ed, ``copy``d with its mode, or ``symlink``ed to its absolute path. An
    ``OSError`` placing a file names the file in the cache."""

_Clauses = tuple[tuple[str, str], ...]
"""A requirement, as an operator (``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``) and a version's text per clause. All of
them must hold, and none accepts every version."""
_Entry = tuple[str, str | None, tuple[tuple[str, _Clauses], ...]]
"""One version, as its text, why it can't be chosen (a predicate such as ``is yanked``) or None, and its dependencies
by name."""

def resolve(
    packages: dict[str, tuple[_Entry, ...]],
    versions: dict[str, tuple[int, bool]],
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

    ``versions`` gives every version text in the call its precedence's rank, its place among every precedence there,
    and whether it's a pre-release. Versions are ordered by rank, then by text, so this needs no version parsing of
    its own, and a requirement compares ranks alone, as it compares precedence.

    Of each collection's versions in a range, the highest is chosen, every stable version before any pre-release, but
    ``preferred``'s (the locked or installed version, by text) first while it fits. A version that can't be chosen
    is reported by its reason and passed over, so a failure can say ``a.a==2.0.0 is yanked``. A name in ``missing`` has
    no versions, and the explanation gives its reason (``no configured index has a.a``) instead; a name in ``sources``
    is read from a directory, tarball or commit, described that way (``from ../col``) when its one version doesn't
    fit; a name in ``overrides`` has ``[project.overrides]`` added wherever something depends on it. ``root_depends``
    and ``root_unsat`` are the words for the roots: ``the project depends on`` and ``the project's requirements are
    unsatisfiable``.
    """
