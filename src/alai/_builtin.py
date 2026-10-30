# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The plugins ansible-core and ansible-rulebook ship, ``ansible.builtin`` and ``eda.builtin``, for ``alai find`` and
``alai collection path``: read from each one's latest sdist on the Python package index (``python_index``) by
alai-core's extractor. The sdist's source and its plugin index lines are kept in the cache. The design is in
``design/find.md``."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import typing as _t
import urllib.parse

from alai_core.pep440 import Version

from ._artifacts import GZIP_MAGIC, max_unpacked_size
from ._common import Error, Record, write_atomic
from ._index import url_dir
from ._output import debug, trace, warn
from ._plugins import BUILTIN, EDA_BUILTIN, PluginIndex

if _t.TYPE_CHECKING:
    from alai_core.extract import Collection

    from ._config import Settings
    from ._fetch import Fetcher
    from ._progress import Task

LAYOUT = "python-index-v1"
"""The cache directory the pages, sources and lines are kept in, under ``cache_dir``, one directory for each
``python_index`` as ``index/`` has for each index: its number changes when what's written changes."""
_ACCEPT = "application/vnd.pypi.simple.v1+json"
"""PEP 691: the simple index's JSON, which PyPI and private indexes serve for this ``Accept``, as uv asks for it."""
_MAX_SDIST_SIZE = 1 << 27
"""The most an sdist may be, 128 MiB: ansible-core's is about 5 MB."""


class BuiltinError(Error):
    """A project's plugins can't be read from the index."""


class Project(Record):
    """A Python project whose sdist holds plugins alai lists as a collection's."""

    __slots__ = ("collection", "extractor", "include", "name")

    def __init__(self, name: str, collection: str, extractor: str, include: tuple[str, ...]) -> None:
        self.name = name
        self.collection = collection
        self.extractor = extractor
        """The ``alai_core.extract`` function that reads its source."""
        self.include = include
        """What ``extract_sdist`` takes from the sdist: what the extractor reads."""


PROJECTS = (
    Project(
        "ansible-core",
        BUILTIN,
        "ansible_core",
        ("lib/ansible/modules/", "lib/ansible/plugins/", "lib/ansible/config/", "lib/ansible/release.py"),
    ),
    Project("ansible-rulebook", EDA_BUILTIN, "ansible_rulebook", ("PKG-INFO", "ansible_rulebook/")),
)


class Release(Record):
    __slots__ = ("sha256", "url", "version")

    def __init__(self, version: str, url: str, sha256: str | None) -> None:
        self.version = version
        self.url = url
        """The sdist."""
        self.sha256 = sha256
        """Its SHA-256 as the index gives it, or None if it doesn't."""


def load_builtin(settings: Settings, fetcher: Fetcher, task: Task) -> list[PluginIndex]:
    """A plugin index for each of ``PROJECTS``: its latest release's lines, made now if they aren't cached. One that
    can't be had now, as offline or with the index unreachable, is the newest cached, or left out with a warning, so a
    search still covers the collections."""
    indexes: list[PluginIndex] = []
    root = os.path.join(settings.cache_dir, LAYOUT, url_dir(settings.python_index))
    for project in PROJECTS:
        task.show(project.name)
        try:
            version, data = _lines(settings, fetcher, project, root, task)
        except Error as e:
            cached = _newest_cached(os.path.join(root, project.name))
            if cached is None:
                warn(f"{project.collection} isn't searchable: {e}")
                task.advance()
                continue
            version, data = cached
            warn(f"{e}; using {project.name} {version} from the cache")
        debug(f"{project.collection} is {project.name} {version}'s")
        indexes.append(
            PluginIndex({"python_index": settings.python_index, "package": project.name, "version": version}, data)
        )
        task.advance()
    return indexes


def _lines(settings: Settings, fetcher: Fetcher, project: Project, root: str, task: Task) -> tuple[str, bytes]:
    """The project's latest release and its lines, from the cache when that release's are there."""
    release = _latest(fetcher, settings.python_index, project, root)
    path = os.path.join(root, project.name, f"{release.version}.jsonl")
    try:
        with open(path, "rb") as f:
            return release.version, f.read()
    except FileNotFoundError:
        pass
    from alai_core import extract

    source = _source(fetcher, project, release, root, task)
    try:
        collection: Collection = getattr(extract, project.extractor)(source, docs=True)
    except OSError as e:
        raise BuiltinError(f"can't extract {project.name} {release.version}: {e}") from e
    for issue in collection.issues:
        trace(f"{project.name} {release.version}: {issue.path}: {issue.kind} {issue.detail}".rstrip())
    data = render_lines(collection, project.collection)
    write_atomic(path, data)
    return release.version, data


def locate_builtin(
    settings: Settings, fetcher: Fetcher, collection: str, *, download: bool = True
) -> tuple[str, str, str] | None:
    """The version, source directory, and whether it was ``cached`` or ``downloaded``, of the project whose plugins
    are ``collection``, ``ansible.builtin`` or ``eda.builtin``, for ``alai collection path``: its latest release,
    downloaded into the cache if it isn't there. Without ``download``, or when the latest can't be had, as offline,
    the newest in the cache; None without ``download`` if there's none."""
    from ._progress import Live, Task

    project = next(p for p in PROJECTS if p.collection == collection)
    root = os.path.join(settings.cache_dir, LAYOUT, url_dir(settings.python_index))
    if download:
        try:
            release = _latest(fetcher, settings.python_index, project, root)
            cached = os.path.isdir(os.path.join(root, project.name, release.version))
            with Live(Task(f"Fetching {project.name}...")) as task:
                path = _source(fetcher, project, release, root, task)
            return release.version, path, "cached" if cached else "downloaded"
        except Error as e:
            found = _newest_source(os.path.join(root, project.name))
            if found is None:
                raise
            warn(f"{e}, so using {project.name} {found[0]} from the cache")
            return *found, "cached"
    found = _newest_source(os.path.join(root, project.name))
    return (*found, "cached") if found is not None else None


def _latest(fetcher: Fetcher, index_url: str, project: Project, root: str) -> Release:
    """The project's highest release with an sdist that isn't a pre-release or yanked, from its page on the simple
    index. The page is cached and revalidated as an index's files are, so a new release is seen on the next run."""
    url = f"{index_url}{project.name}/"
    page = fetcher.get(url, os.path.join(root, "pages", f"{project.name}.json"), accept=_ACCEPT)
    if page is None:
        raise BuiltinError(f"{index_url} has no {project.name}")
    try:
        files = json.loads(page)["files"]
        candidates = [
            (version, Release(version.text, urllib.parse.urljoin(url, f["url"]), f.get("hashes", {}).get("sha256")))
            for f in files
            if not f.get("yanked") and (version := _sdist_version(project.name, f["filename"])) is not None
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise BuiltinError(f"{url} isn't a PEP 691 JSON page, as a Python simple index serves: {e}") from e
    if not candidates:
        raise BuiltinError(f"{index_url} has no release of {project.name} with an sdist")
    return max(candidates, key=lambda c: c[0])[1]


def _sdist_version(project: str, filename: str) -> Version | None:
    """The version an sdist's file name, ``ansible_core-2.20.1.tar.gz``, gives, or None for a pre-release, a wheel, or
    another project's. Names compare normalized, as PEP 503 compares them."""
    if not filename.endswith(".tar.gz"):
        return None
    name, _, text = filename[: -len(".tar.gz")].rpartition("-")
    if re.sub(r"[-_.]+", "-", name.lower()) != re.sub(r"[-_.]+", "-", project.lower()):
        return None
    try:
        version = Version(text)
    except ValueError:
        return None
    return None if version.is_prerelease else version


def _source(fetcher: Fetcher, project: Project, release: Release, root: str, task: Task) -> str:
    """The release's source in the cache, ``<project>/<version>/``: what the extractor reads, from its sdist, which is
    downloaded and extracted if it isn't there. Kept for ``alai collection path``, which gives it to read docs from."""
    from alai_core import artifact

    target = os.path.join(root, project.name, release.version)
    if os.path.isdir(target):
        return target
    task.show(f"{project.name} {release.version}: downloading")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    work = tempfile.mkdtemp(dir=root, prefix=".tmp-")
    try:
        tarball = os.path.join(work, "sdist.tar.gz")
        digest, _ = fetcher.download(
            release.url, tarball, max_size=_MAX_SDIST_SIZE, magic=GZIP_MAGIC, kind="gzip file, so not an sdist"
        )
        if release.sha256 is not None and digest != release.sha256:
            raise BuiltinError(
                f"{release.url} doesn't match the index: its SHA-256 is {digest}, where the index says {release.sha256}"
            )
        task.show(f"{project.name} {release.version}: extracting")
        source = os.path.join(work, "source")
        try:
            artifact.extract_sdist(tarball, source, include=project.include, limit=max_unpacked_size(os.environ))
        except (artifact.ExtractError, OSError) as e:
            raise BuiltinError(f"can't extract {project.name} {release.version}: {e}") from e
        try:
            os.rename(source, target)
        except OSError:
            # Another alai extracted the same release meanwhile.
            if not os.path.isdir(target):
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def render_lines(collection: Collection, name: str) -> bytes:
    """The collection's plugins as plugin index lines for ``name``, as the index spec wrote ``ansible.builtin``'s:
    ``since`` is a plugin's documented ``version_added``, which ansible-core keeps accurate, left out when it's
    ``historical``; there's no ``until``, as what's gone is in the routing's tombstones; and routing with no plugin is
    a line of its own. ``action`` routing isn't: an action plugin isn't named in a play, and ansible-core's mirrors
    its modules' routing."""
    from alai_core.plugins import PluginEntry

    routing = {(r.type, r.name): r.entry for r in collection.routing if r.type != "action"}
    lines: list[str] = []
    for plugin in collection.plugins:
        docs = plugin.docs.documentation if plugin.docs is not None else None
        added = docs.get("version_added") if docs is not None else None
        since = str(added) if added is not None and str(added) != "historical" else None
        entry = PluginEntry(
            plugin.type,
            plugin.name,
            name,
            since=since,
            short_description=plugin.short_description,
            routing=routing.pop((plugin.type, plugin.name), None),
        )
        lines.append(entry.to_json())
    lines.extend(PluginEntry(type_, short, name, routing=entry).to_json() for (type_, short), entry in routing.items())
    return "".join(f"{line}\n" for line in lines).encode()


def _newest_cached(directory: str) -> tuple[str, bytes] | None:
    """The highest version's cached lines in a project's directory, or None if there are none."""
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    versions: list[Version] = []
    for file in names:
        if file.endswith(".jsonl"):
            try:
                versions.append(Version(file[: -len(".jsonl")]))
            except ValueError:
                continue
    if not versions:
        return None
    newest = max(versions)
    try:
        with open(os.path.join(directory, f"{newest.text}.jsonl"), "rb") as f:
            return newest.text, f.read()
    except OSError:
        return None


def _newest_source(directory: str) -> tuple[str, str] | None:
    """The highest version's cached source in a project's directory, as (version, path), or None if there's none."""
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    versions: list[Version] = []
    for name in names:
        if os.path.isdir(os.path.join(directory, name)):
            try:
                versions.append(Version(name))
            except ValueError:
                continue
    if not versions:
        return None
    newest = max(versions).text
    return newest, os.path.join(directory, newest)
