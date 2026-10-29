# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Git sources: a revision resolved to its commit, and that commit's files fetched into the cache, through the ``git``
CLI as uv and ansible-galaxy run it, so the user's SSH config, agent, ``known_hosts`` and credential helpers apply.

A commit's files are ``<cache_dir>/git-v1/<url hash>/<commit>/``, fetched at depth 1 into a temporary directory, with
``.git`` removed, and renamed into place, as an artifact is extracted: there's no repository shared between runs, so
nothing needs a lock. Over ssh, one connection per host is shared by every git command in a run, so a key is used, and
an agent asks to approve it, once. The design is in ``design/install.md``.
"""

from __future__ import annotations

import atexit
import collections.abc as _c
import concurrent.futures
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.parse

from ._common import Error
from ._lock import GIT_COMMIT_RE
from ._output import DIM, status, styled, trace
from ._progress import Suspended

LAYOUT = "git-v1"
"""The cache directory for commits' files; a new name if what's kept, or how, ever changes."""
_HEX_RE = re.compile(r"[0-9a-fA-F]{4,63}")
_MAX_FETCHES = 8
"""How many repositories are fetched at once."""
_UNADVERTISED = "Server does not allow request for unadvertised object"
"""What git says when a server won't serve a commit that isn't a branch or tag's tip by its hash."""
_CONTROL_PERSIST = 60
"""Seconds a shared ssh connection outlives its last use, should alai be killed before it closes them."""


class GitError(Error):
    """git isn't there, or a repository can't be read or fetched."""


def repository_dir(cache_dir: str, url: str) -> str:
    """Where the commits fetched from ``url`` are kept."""
    return os.path.join(cache_dir, LAYOUT, hashlib.sha256(url.encode()).hexdigest()[:16])


def cached(cache_dir: str, url: str, commit: str) -> str | None:
    """Where ``commit``'s files from ``url`` are, or None if they aren't cached."""
    path = os.path.join(repository_dir(cache_dir, url), commit)
    return path if os.path.isdir(path) else None


def resolve(url: str, rev: str | None, *, offline: bool) -> str:
    """The commit ``rev``, a branch, tag or full commit hash, is at in ``url``, or the default branch's when None.

    A full hash is taken as it is, without asking the repository. Anything else is looked up with ``git ls-remote``,
    a tag before a branch of the same name, as git itself prefers one; an annotated tag is the commit it points at.
    An abbreviated hash can't be found without the history, so it's an error.
    """
    if rev is not None and GIT_COMMIT_RE.fullmatch(rev):
        return rev
    shown = f"{url}@{rev}" if rev is not None else url
    if offline:
        raise GitError(f"can't resolve {shown} to a commit with --offline; give the full commit hash as its rev")
    # An annotated tag's commit is only listed when asked for by its own pattern.
    patterns = [rev, f"{rev}^{{}}"] if rev is not None else ["HEAD"]
    refs: dict[str, str] = {}
    for line in _git(["ls-remote", "--", url, *patterns], network=True, url=url).splitlines():
        commit, _, ref = line.partition("\t")
        refs[ref] = commit
    candidates = (
        [f"refs/tags/{rev}^{{}}", f"refs/tags/{rev}", f"refs/heads/{rev}", rev] if rev is not None else ["HEAD"]
    )
    for ref in candidates:
        if ref in refs:
            trace(f"{shown}: {ref} is {refs[ref]}")
            return refs[ref]
    if rev is not None and _HEX_RE.fullmatch(rev):
        raise GitError(f"{url} has no branch or tag '{rev}'; give a commit by its full hash, not an abbreviated one")
    raise GitError(f"{url} has no branch or tag '{rev}'" if rev is not None else f"{url} has no default branch")


def fetch(cache_dir: str, url: str, commit: str, *, offline: bool) -> str:
    """Where ``commit``'s files from ``url`` are, fetching them first if they aren't cached.

    Fetched by its hash at depth 1, which servers allow for any commit a branch or tag leads to unless they still
    speak git's first protocol and don't allow it; for those, every branch and tag is fetched instead. Submodules are
    fetched too, and LFS files aren't, as uv does.
    """
    path = cached(cache_dir, url, commit)
    if path is not None:
        trace(f"{url}@{commit}: cached at {path}")
        return path
    if offline:
        raise GitError(f"{url}@{commit[:7]} isn't in the cache, and --offline doesn't fetch it")
    parent = repository_dir(cache_dir, url)
    target = os.path.join(parent, commit)
    try:
        os.makedirs(parent, exist_ok=True)
        work = tempfile.mkdtemp(dir=parent, prefix=".tmp-")
    except OSError as e:
        raise GitError(f"can't write to the cache at {parent}: {e.strerror or e}") from e
    try:
        checkout = os.path.join(work, "checkout")
        _ = _git(["init", "--quiet", checkout])
        try:
            _ = _git(["fetch", "--quiet", "--depth=1", "--", url, commit], cwd=checkout, network=True, url=url)
        except GitError as e:
            if _UNADVERTISED not in str(e):
                raise
            trace(f"{url}: the server won't fetch {commit} by its hash; fetching every branch and tag")
            _ = _git(
                ["fetch", "--quiet", "--", url, "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*"],
                cwd=checkout,
                network=True,
                url=url,
            )
        try:
            _ = _git(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=checkout)
        except GitError as e:
            raise GitError(f"{url} has no commit {commit} on any branch or tag") from e
        _ = _git(["checkout", "--quiet", "--detach", commit], cwd=checkout)
        if os.path.exists(os.path.join(checkout, ".gitmodules")):
            _ = _git(["submodule", "update", "--quiet", "--init", "--recursive"], cwd=checkout, network=True, url=url)
        # Only the files are kept: nothing a collection's build reads is in .git.
        for root, dirs, files in os.walk(checkout):
            for name in [*dirs, *files]:
                if name == ".git":
                    path = os.path.join(root, name)
                    if os.path.isdir(path) and not os.path.islink(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
            dirs[:] = [d for d in dirs if d != ".git"]
        try:
            os.rename(checkout, target)
        except OSError:
            # Another alai fetched the same commit meanwhile; theirs is as good as ours.
            if not os.path.isdir(target):
                raise
    except OSError as e:
        raise GitError(f"can't fetch {url}@{commit[:7]} into the cache: {e.strerror or e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)
    trace(f"{url}@{commit}: fetched to {target}")
    return target


def update(
    cache_dir: str,
    url: str,
    rev: str | None,
    commit: str | None = None,
    *,
    offline: bool,
    names: _c.Sequence[str] = (),
) -> tuple[str, str]:
    """The commit ``rev`` is at, or ``commit`` when it's known already, and where its files are, resolving and
    fetching what isn't cached.

    When it has to go to the repository, it says so as uv does, ``Updating <names> from <url> (<rev>)`` before and
    ``Updated <names> from <url> (<commit>)`` after, ``names`` being the collections it's fetched for, when known. They
    stay on lines of their own: progress isn't drawn while git reaches the network, in case ssh asks something, so
    these say what alai is waiting on, and why, when an agent or password manager asks to use a key; and as they're
    never cleared, a question ssh writes under them is left alone.
    """
    if commit is None and rev is not None and GIT_COMMIT_RE.fullmatch(rev):
        commit = rev
    if commit is not None and (path := cached(cache_dir, url, commit)) is not None:
        trace(f"{url}@{commit}: cached at {path}")
        return commit, path
    what = f"{', '.join(names)} from {url}" if names else url
    if not offline:
        wanted = rev or (commit[:7] if commit is not None else "HEAD")
        status(f"Updating {what} {styled(f'({wanted})', DIM)}")
    if commit is None:
        commit = resolve(url, rev, offline=offline)
    path = fetch(cache_dir, url, commit, offline=offline)
    if not offline:
        status(f"Updated {what} {styled(f'({commit})', DIM)}")
    return commit, path


def update_all(
    cache_dir: str, wanted: _c.Iterable[tuple[str, str | None, str | None, str]], *, offline: bool
) -> dict[str, tuple[str, str]]:
    """Each ``(url, rev, commit, name)``'s commit and files, by ``name``, as ``update`` gives them, resolving and
    fetching what isn't cached in parallel, as uv fetches repositories: ``name`` is the collection it's for, and
    those that share a commit, or a ``rev`` still to resolve, share one ``update``.

    Every one is attempted; if any fails, the first in order is raised once the rest have finished.
    """
    unique: dict[tuple[str, str | None], tuple[str | None, str | None, list[str]]] = {}
    for url, rev, commit, name in wanted:
        unique.setdefault((url, commit if commit is not None else rev), (rev, commit, []))[2].append(name)
    with concurrent.futures.ThreadPoolExecutor(_MAX_FETCHES, thread_name_prefix="alai-git") as pool:
        futures = {
            key: pool.submit(update, cache_dir, key[0], rev, commit, offline=offline, names=sorted(names))
            for key, (rev, commit, names) in sorted(unique.items(), key=lambda item: (item[0][0], item[0][1] or ""))
        }
    return {name: future.result() for key, future in futures.items() for name in unique[key][2]}


class _Sharing:
    """One ssh connection per host for every git command in a run, through ssh's own connection sharing
    (``ControlMaster``), so a key is used once per host, not once per command: each use is a request an agent may ask
    the user to approve, and resolving a revision then fetching it would otherwise be two.

    The sockets are in a directory of alai's under ``/tmp``, not ``TMPDIR``: a socket's path is limited to 104 bytes on
    macOS, whose ``TMPDIR`` takes about 50 before the 40 of ssh's ``%C``. The connections are closed when alai exits,
    and outlive a killed alai by ``_CONTROL_PERSIST`` seconds. None of this is used when the user runs their own ssh
    command (``GIT_SSH_COMMAND``, ``GIT_SSH`` or ``core.sshCommand``), which alai doesn't replace, or for a host whose
    ssh config shares connections already, whose connection may be open, and its key approved, already.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled: bool | None = None
        self._hosts: dict[str, bool] = {}
        self._dir: str | None = None
        self._first: dict[str, threading.Event] = {}

    def command(self, url: str) -> str | None:
        """The ``GIT_SSH_COMMAND`` that shares connections to ``url``'s host, or None when the user has an ssh command
        of their own, or shares connections to the host themselves."""
        with self._lock:
            if self._enabled is None:
                self._enabled = not (
                    os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH") or _configured("core.sshCommand")
                )
                if not self._enabled:
                    trace("ssh: not sharing connections, as git has an ssh command of the user's")
            if not self._enabled:
                return None
            host = _connection(url)
            if host not in self._hosts:
                self._hosts[host] = not _shares_already(url)
                if not self._hosts[host]:
                    trace(f"ssh: not sharing connections to {host}, as its ssh config does")
            if not self._hosts[host]:
                return None
            if self._dir is None:
                self._dir = tempfile.mkdtemp(prefix="alai-ssh-", dir="/tmp")
                _ = atexit.register(self.close)
            return f"ssh -o ControlMaster=auto -o ControlPath={self._dir}/%C -o ControlPersist={_CONTROL_PERSIST}"

    @contextlib.contextmanager
    def turn(self, host: str) -> _c.Generator[None]:
        """Wait while another command makes the first connection to ``host``, so later ones share it rather than each
        making, and authenticating, their own; once it's made, any number run at once."""
        with self._lock:
            event = self._first.get(host)
            mine = event is None
            if event is None:
                event = self._first[host] = threading.Event()
        if not mine:
            _ = event.wait()
        try:
            yield
        finally:
            if mine:
                event.set()

    def close(self) -> None:
        """Close the shared connections and remove their directory."""
        if self._dir is None:
            return
        for name in os.listdir(self._dir) if os.path.isdir(self._dir) else ():
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                _ = subprocess.run(  # noqa: S603 # ssh from PATH, as git runs it
                    ["ssh", "-O", "exit", "-o", f"ControlPath={os.path.join(self._dir, name)}", "alai"],  # noqa: S607
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
        shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = None


_SHARING = _Sharing()


def _configured(key: str) -> bool:
    """Whether git's config sets ``key``."""
    try:
        done = subprocess.run(  # noqa: S603 # git from PATH, as it's run for everything else
            ["git", "config", "--get", key],  # noqa: S607
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False  # no git: running it for the fetch says so
    return done.returncode == 0 and bool(done.stdout.strip())


def _shares_already(url: str) -> bool:
    """Whether the user's ssh config shares connections to ``url``'s host itself: ``ssh -G`` prints the config ssh
    would use, without connecting."""
    args = ["ssh", "-G"]
    if port := urllib.parse.urlsplit(url).port:
        args += ["-p", str(port)]
    try:
        done = subprocess.run(  # noqa: S603 # ssh from PATH, as git runs it
            [*args, _ssh_target(url)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(
        line.lower() not in ("controlmaster false", "controlmaster no")
        for line in done.stdout.splitlines()
        if line.lower().startswith("controlmaster ")
    )


def _is_ssh(url: str) -> bool:
    """Whether git reaches ``url`` over ssh; a source's URL is never scp-like (``_manifest._git_url``)."""
    return urllib.parse.urlsplit(url).scheme.lower() in ("ssh", "git+ssh", "ssh+git")


def _git(args: list[str], *, cwd: str | None = None, network: bool = False, url: str = "") -> str:
    """Run ``git`` with ``args`` and return what it printed, or raise GitError with what it said on stderr.

    One that reaches the network can't prompt for an HTTPS username or password (``GIT_TERMINAL_PROMPT=0``), as uv
    runs it, though a credential helper still answers; ssh is left alone, so it can still ask for a passphrase or to
    trust a new host's key on the terminal, and progress isn't drawn meanwhile so the prompt shows.
    """
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    if network:
        env["GIT_TERMINAL_PROMPT"] = "0"
    command = _SHARING.command(url) if network and _is_ssh(url) else None
    if command is not None:
        env["GIT_SSH_COMMAND"] = command
    trace(f"git {' '.join(args)}")
    try:
        with (
            _SHARING.turn(_connection(url)) if command is not None else contextlib.nullcontext(),
            Suspended() if network else contextlib.nullcontext(),
        ):
            done = subprocess.run(  # noqa: S603 # git's path from PATH, as uv runs it; the arguments are a list
                ["git", *args],  # noqa: S607
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
    except FileNotFoundError as e:
        raise GitError("git isn't installed, or isn't on PATH, and git sources need it") from e
    except OSError as e:
        raise GitError(f"can't run git: {e.strerror or e}") from e
    if done.returncode != 0:
        said = done.stderr.strip() or f"exit status {done.returncode}"
        message = f"git {args[0]} failed: {said}"
        if hint := _hint(said, url):
            message += f"\n\nhint: {hint}"
        raise GitError(message)
    return done.stdout


def _hint(said: str, url: str) -> str | None:
    """What to do about the failures people meet on their first fetch from a host, or None."""
    host = _host(url)
    if "Host key verification failed" in said:
        return (
            f"ssh doesn't trust {host}'s key yet, and couldn't ask: add it to ~/.ssh/known_hosts first, such as by "
            + f"running ssh -T {_ssh_target(url)} once and checking the fingerprint it shows against the one "
            + f"{host} publishes"
        )
    if "Permission denied (publickey" in said:
        return (
            f"{host} accepted none of ssh's keys: load yours into an agent with ssh-add, or check {host} has its "
            + "public key"
        )
    if "terminal prompts disabled" in said or "could not read Username" in said:
        return (
            f"{host} wants a username and password, which alai doesn't let git ask for: set up a git credential "
            + "helper, or use an SSH URL"
        )
    return None


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or url


def _ssh_target(url: str) -> str:
    """``url``'s user and host, as ``ssh`` takes them."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.username}@{parts.hostname}" if parts.username else _host(url)


def _connection(url: str) -> str:
    """Which ssh connection ``url`` is reached over: its user, host and port."""
    return f"{_ssh_target(url)}:{urllib.parse.urlsplit(url).port or 22}"
