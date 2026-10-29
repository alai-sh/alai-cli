# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Settings from the command line, environment variables and config files.

The format and lookup rules are specified in ``docs/configuration.md``, and project discovery in
``docs/projects.md``.
"""

from __future__ import annotations

import collections.abc as _c
import enum
import os

from ._common import (
    NAME_RE,
    ConfigError,
    Record,
    as_table,
    check_name_of,
    env_bool,
    home_path,
    inside,
    parse_bool,
    read_toml,
    string_list,
)
from ._output import debug

CONFIG_FILE_NAME = "alai.toml"
DEFAULT_INDEX_NAME = "alai"
DEFAULT_INDEX_URL = "https://index.alai.sh/"
MANIFEST_PROJECT_KEYS = ("collections", "requires_ansible", "groups", "overrides", "sources")
"""Keys of ``[project]`` that are the manifest, read by ``_manifest``; the rest of ``[project]`` are settings."""

_INDEX_URL_SCHEMES = ("http://", "https://", "file://")


class LinkMode(enum.Enum):
    AUTO = "auto"
    """Try clone, then hardlink, then copy."""
    CLONE = "clone"
    HARDLINK = "hardlink"
    COPY = "copy"
    SYMLINK = "symlink"


class Level(enum.Enum):
    """Where a setting came from, highest precedence first."""

    CLI = "cli"
    ENV = "env"
    EXPLICIT = "explicit"  # --config-file
    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"


class Index(Record):
    __slots__ = ("default", "name", "source", "url")

    def __init__(self, name: str, url: str, default: bool = False, source: str = "") -> None:
        self.name = name
        self.url = url
        self.default = default
        self.source = source
        """Where the index was defined, for messages."""


class Project(Record):
    __slots__ = ("collection_file", "config_file", "near", "root")

    def __init__(
        self, root: str, config_file: str | None, collection_file: str | None = None, near: str | None = None
    ) -> None:
        self.root = root
        """The directory holding ``alai.toml``, or where discovery started when there's none."""
        self.config_file = config_file
        """The project's ``alai.toml``, or None when there's no project."""
        self.collection_file = collection_file
        """The ``galaxy.yml`` beside ``alai.toml`` in a collection project, else None."""
        self.near = near
        """With no ``alai.toml``, the nearest ``galaxy.yml``, else ``ansible.cfg``, on the way up, for
        ``no_project`` to suggest as the root."""


class Overrides(Record):
    """Values given on the command line, as typed, before validation.

    ``settings`` holds each setting's flag value by the setting's ``name`` in ``SETTINGS``.
    """

    __slots__ = ("config_file", "no_config", "project", "settings")

    def __init__(
        self,
        *,
        project: str | None = None,
        config_file: str | None = None,
        no_config: bool = False,
        **settings: str | _c.Sequence[str] | bool | None,
    ) -> None:
        if unknown := sorted(settings.keys() - {s.name for s in SETTINGS}):
            raise TypeError(f"Overrides got an unexpected keyword argument '{unknown[0]}'")
        self.project = project
        self.config_file = config_file
        self.no_config = no_config
        self.settings = settings


class ConfigFile(Record):
    __slots__ = ("level", "path", "read")

    def __init__(self, level: Level, path: str, *, read: bool) -> None:
        self.level = level
        self.path = path
        self.read = read
        """False when alai looked for the file and it doesn't exist."""


class RawManifest(Record):
    """The manifest keys of a project's ``alai.toml``, checked for type but not parsed.

    Every command checks them this far, so a misspelt key or a wrong type fails straight away. What the values mean,
    such as whether a requirement string is valid, is ``_manifest``'s to check when a command uses the manifest.
    """

    __slots__ = ("collections", "groups", "ignore_requires_ansible", "overrides", "path", "requires_ansible", "sources")

    def __init__(
        self,
        path: str,
        *,
        collections: list[str],
        requires_ansible: str | None,
        groups: dict[str, list[str]],
        overrides: dict[str, str],
        ignore_requires_ansible: list[str],
        sources: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.path = path
        self.collections = collections
        self.requires_ansible = requires_ansible
        self.groups = groups
        """Each group's ``collections``, by group name."""
        self.overrides = overrides
        """Version requirements by collection name, without ``ignore_requires_ansible``."""
        self.ignore_requires_ansible = ignore_requires_ansible
        self.sources = sources or {}
        """Each ``[project.sources]`` entry's table, such as ``{"index": "internal"}``, by collection name."""


class Settings(Record):
    __slots__ = (
        "_cache_dir",
        "collections_dir",
        "config_files",
        "indexes",
        "link_mode",
        "manifest",
        "no_config",
        "offline",
        "overridden",
        "project",
        "searched",
        "sources",
    )

    def __init__(
        self,
        *,
        project: Project,
        config_files: tuple[str, ...],
        collections_dir: str,
        cache_dir: str | None,
        link_mode: LinkMode,
        indexes: tuple[Index, ...],
        offline: bool = False,
        searched: tuple[ConfigFile, ...] = (),
        no_config: bool = False,
        manifest: RawManifest | None = None,
        sources: dict[str, str] | None = None,
        overridden: dict[str, tuple[tuple[object, str], ...]] | None = None,
    ) -> None:
        self.project = project
        self.config_files = config_files
        """The config files read, highest precedence first."""
        self.manifest = manifest
        """The project's ``alai.toml`` manifest keys, checked as it was read for its settings; None when
        ``--config-file`` or ``--no-config`` skipped it, or there's no project."""
        self.searched = searched
        """Every config file alai looked for, highest precedence first, including those that don't exist."""
        self.no_config = no_config
        """``--no-config`` or ``ALAI_NO_CONFIG`` was given, so no config files were read."""
        self.sources = sources or {}
        """Where each single-valued setting came from, by attribute: a flag, an environment variable, a config file
        path, or ``default``. List settings record it per item, as ``Index.source`` does."""
        self.overridden = overridden or {}
        """Values a higher level replaced, by attribute, highest first, as ``(value, source)``."""
        self.collections_dir = collections_dir
        self._cache_dir = cache_dir
        self.link_mode = link_mode
        self.indexes = indexes
        """The indexes in resolution order, the default index last."""
        self.offline = offline
        """Read only what's cached, never the network."""

    @property
    def cache_dir(self) -> str:
        """The cache directory.

        Raises ConfigError if it wasn't set and there's no home directory for the default. That's left until a
        command uses it, so one that never touches the cache still runs without a home directory.
        """
        if self._cache_dir is None:
            raise ConfigError("cannot find the home directory for the cache, set ALAI_CACHE_DIR")
        return self._cache_dir

    @property
    def collections_dir_set(self) -> bool:
        """Whether a flag, environment variable or config file chose ``collections_dir``, rather than the default:
        outside a project, the commands that work on the directory need one to have been chosen."""
        return self.sources["collections_dir"] != "default"


class _Source(Record):
    __slots__ = ("base_dir", "description", "home", "level")

    def __init__(self, level: Level, description: str, base_dir: str, home: str | None) -> None:
        self.level = level
        self.description = description
        self.base_dir = base_dir
        """What a relative path is relative to."""
        self.home = home
        """What a leading ``~`` expands to, or None if the home directory is unknown."""


class _Layer(Record):
    """The settings one level sets, by each setting's ``target``."""

    __slots__ = ("lists", "manifest", "overridden", "sources", "values")

    def __init__(self) -> None:
        self.manifest: RawManifest | None = None
        """The project's manifest keys, for the project's own ``alai.toml``."""
        self.values: dict[str, object] = {}
        """Single values: the highest level that sets one wins."""
        self.sources: dict[str, str] = {}
        """Where each of ``values`` came from."""
        self.lists: dict[str, list[object]] = {}
        """Values of ``many`` settings, concatenated across levels highest first."""
        self.overridden: dict[str, list[tuple[object, str]]] = {}
        """After merging, the values a higher level replaced, with their sources."""

    def add(self, setting: SettingDefinition, value: object, source: _Source) -> None:
        if setting.many:
            self.lists.setdefault(setting.target, []).append(value)
        else:
            self.values[setting.target] = value
            self.sources[setting.target] = source.description


class SettingDefinition(Record):
    """A setting read from config files, environment variables or flags.

    Every setting is listed once in ``SETTINGS``. Reading each level, merging them, the argparse options and
    ``config show`` all work from that list, so adding a setting means adding it there and to ``Settings``.
    """

    __slots__ = (
        "boolean",
        "choices",
        "env",
        "flag",
        "global_flag",
        "group",
        "help",
        "key",
        "many",
        "metavar",
        "name",
        "parse",
        "parse_file",
        "repeat",
        "target",
    )

    def __init__(
        self,
        name: str,
        *,
        key: str | None,
        flag: str,
        env: str,
        help: str,  # noqa: A002  # argparse's name for it
        parse: _c.Callable[[str, _Source], object],
        parse_file: _c.Callable[[object, _Source], _c.Iterable[object]] | None = None,
        target: str | None = None,
        metavar: str | None = None,
        choices: tuple[str, ...] | None = None,
        global_flag: bool = False,
        many: bool = False,
        repeat: bool = False,
        boolean: bool = False,
        group: str | None = None,
    ) -> None:
        """Define a setting.

        Args:
            name: Unique identifier: the argparse dest, the ``Overrides`` keyword and, unless ``target`` is given,
                the ``Settings`` attribute.
            key: The config file key, dotted if it's in a table (``project.collections_dir``), or None for a
                setting only flags and environment variables can set.
            flag: The command-line option, such as ``--link-mode``.
            env: The environment variable, such as ``ALAI_LINK_MODE``.
            help: The option's help text in ``--help``.
            parse: Turns one string value, from a flag, an environment variable or a config file, into the
                setting's value. ``source`` says where it came from, for relative paths and error messages.
            parse_file: For a ``many`` setting whose config file value isn't a string, such as ``[[index]]``:
                turns that value into its items. Without it the file value must be a string for ``parse``.
            target: The ``Settings`` attribute the value ends up in, when it isn't ``name``. Two settings can
                share one, as ``--index`` and ``--default-index`` both add to ``indexes``.
            metavar: The value's placeholder in ``--help``, such as ``DIR``. A ``DIR`` option also completes only
                directories.
            choices: The values the flag accepts, for argparse to check and for completion.
            global_flag: The flag is accepted by every command, rather than only those that ask for its ``group``.
            many: The value is a list, concatenated across levels highest first, rather than a single value taken
                from the highest level that sets it.
            repeat: The flag can be given more than once, and the environment variable holds several values
                separated by whitespace.
            boolean: The setting is true or false. ``flag`` comes with a ``--no-`` form, so the command line can
                override a file or environment variable either way.
            group: Which commands take the flag: ``resolve`` for those that read indexes, ``install`` for those that
                install collections. A command asks for the groups it uses, so a flag it doesn't use is an argparse
                error. Every setting but a ``global_flag`` one has a group.
        """
        self.name = name
        self.key = key
        self.target = target or name
        self.flag = flag
        self.env = env
        self.help = help
        self.metavar = metavar
        self.choices = choices
        self.global_flag = global_flag
        self.parse = parse
        self.parse_file = parse_file
        self.many = many
        self.repeat = repeat
        self.boolean = boolean
        self.group = group


def load_settings(
    overrides: Overrides | None = None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    environ: _c.Mapping[str, str] | None = None,
) -> Settings:
    """Resolve the settings for a command run from ``cwd``."""
    overrides = overrides or Overrides()
    cwd = os.fspath(cwd) if cwd else os.getcwd()
    environ = os.environ if environ is None else environ
    home = _home(environ)
    # --project and --config-file, or their environment variables, are relative to the current directory.
    here = _Source(Level.CLI, "the command line", cwd, home)

    project_arg = overrides.project or _env(environ, "ALAI_PROJECT")
    if project_arg:
        start = _resolve_path(project_arg, here)
        if not os.path.isdir(start):
            raise ConfigError(f"project directory '{start}' does not exist")
    else:
        start = cwd
    project = find_project(start)

    layers = [
        _cli_layer(overrides, cwd, home),
        _env_layer(environ, cwd, home),
    ]
    searched: list[ConfigFile] = []

    config_file = overrides.config_file or _env(environ, "ALAI_CONFIG_FILE")
    no_config = overrides.no_config or env_bool(environ, "ALAI_NO_CONFIG")
    if config_file:
        path = _resolve_path(config_file, here)
        layers.append(_file_layer(read_toml(path), path, Level.EXPLICIT, home))
        searched.append(ConfigFile(Level.EXPLICIT, path, read=True))
    elif not no_config:
        levels = [
            (Level.PROJECT, [project.config_file] if project.config_file else []),
            (Level.USER, user_config_paths(environ)),
            (Level.SYSTEM, system_config_paths(environ)),
        ]
        for level, paths in levels:
            for path in paths:
                # Running from inside the user config directory finds the user file as the project file too.
                if any(f.path == path for f in searched):
                    break
                if not os.path.isfile(path):
                    searched.append(ConfigFile(level, path, read=False))
                    continue
                layers.append(_file_layer(read_toml(path), path, level, home))
                searched.append(ConfigFile(level, path, read=True))
                break

    merged = _merge(layers)
    # Each value is what its setting's parser returned; the isinstance checks give the types back.
    collections_dir = merged.values.get("collections_dir")
    cache_dir = merged.values.get("cache_dir")
    link_mode = merged.values.get("link_mode")
    offline = merged.values.get("offline")
    indexes = [i for i in merged.lists.get("indexes", ()) if isinstance(i, Index)]
    sources = {s.target: merged.sources.get(s.target, "default") for s in SETTINGS if not s.many}
    if not isinstance(cache_dir, str):
        cache_dir = default_cache_dir(environ)
    if not isinstance(collections_dir, str):
        collections_dir = _default_collections_dir(project, cache_dir)
    elif project.collection_file is not None:
        _check_tree(project.root, collections_dir, sources["collections_dir"])
    return Settings(
        project=project,
        config_files=tuple(f.path for f in searched if f.read),
        searched=tuple(searched),
        no_config=no_config and not config_file,
        manifest=next((layer.manifest for layer in layers if layer.manifest is not None), None),
        sources=sources,
        overridden={target: tuple(values) for target, values in merged.overridden.items()},
        collections_dir=collections_dir,
        cache_dir=cache_dir,
        link_mode=link_mode if isinstance(link_mode, LinkMode) else LinkMode.AUTO,
        indexes=_resolve_indexes(indexes),
        offline=offline if isinstance(offline, bool) else False,
    )


def _default_collections_dir(project: Project, cache_dir: str | None) -> str:
    """``<root>/collections``, or for a collection project a tree of its own in the cache, keyed by the checkout's
    path, as the checkout can't hold its own dependencies: see ``design/collection-development.md``."""
    if project.collection_file is None:
        return os.path.join(project.root, "collections")
    if cache_dir is None:
        raise ConfigError("cannot find the home directory for the cache, set ALAI_CACHE_DIR or ALAI_COLLECTIONS_DIR")
    import hashlib

    key = hashlib.sha256(os.fsencode(project.root)).hexdigest()[:16]
    return os.path.join(cache_dir, "envs", key)


def _check_tree(root: str, collections_dir: str, source: str) -> None:
    """A collection project's checkout and the tree it's linked into mustn't hold each other: the tree inside would be part of
    the collection to ansible-galaxy and ansible-test, and the checkout inside would leave nowhere for its link, with
    an exact sync removing whatever is beside it."""
    tree = os.path.realpath(collections_dir)
    for inner, outer, what in ((tree, root, "inside the collection"), (root, tree, "holds the collection")):
        if inside(inner, outer):
            raise ConfigError(
                f"collections_dir {collections_dir} (from {source}) {what} at {root}; "
                + "a collection's dependencies are installed into a directory outside its checkout"
            )


def find_project(start: str | os.PathLike[str]) -> Project:
    """Find the project root, the nearest directory holding an ``alai.toml``, by walking up from ``start``.

    A ``galaxy.yml`` beside it makes it a collection project. Nothing else marks a root (see
    ``design/configuration.md``), but with no ``alai.toml``, the nearest ``galaxy.yml``, else ``ansible.cfg``, is kept
    for the error to suggest.
    """
    start = os.path.realpath(start)
    galaxy: str | None = None
    ansible_cfg: str | None = None

    directory = start
    while True:
        alai_toml = os.path.join(directory, CONFIG_FILE_NAME)
        galaxy_yml = os.path.join(directory, "galaxy.yml")
        if os.path.isfile(alai_toml):
            return Project(directory, alai_toml, galaxy_yml if os.path.isfile(galaxy_yml) else None)

        if galaxy is None and os.path.isfile(galaxy_yml):
            galaxy = galaxy_yml
        cfg = os.path.join(directory, "ansible.cfg")
        if ansible_cfg is None and os.path.isfile(cfg):
            ansible_cfg = cfg

        parent = os.path.dirname(directory)
        if parent == directory:
            return Project(start, None, near=galaxy or ansible_cfg)
        directory = parent


def no_project(project: Project, why: str) -> ConfigError:
    """The error for a command that needs a project when there's no ``alai.toml``, as uv's "No `pyproject.toml`
    found in current directory or any parent directory", with a hint naming the directory that's likely the root."""
    message = f"no alai.toml found in {home_path(project.root)} or any directory above it: {why}"
    if project.near is None:
        hint = "create an alai.toml, which can be empty, in the project's root directory"
    else:
        directory = home_path(os.path.dirname(project.near))
        if os.path.basename(project.near) == "galaxy.yml":
            hint = f"{directory} has a galaxy.yml. To make it a collection project, create an empty alai.toml there"
        else:
            hint = f"{directory} has an ansible.cfg. If it's the project's root, create an empty alai.toml there"
    return ConfigError(f"{message}\n\nhint: {hint}")


def user_config_paths(environ: _c.Mapping[str, str]) -> list[str]:
    """Where the user config file may be, in the order looked for; the first that exists is read."""
    config_home = _xdg_dir(environ, "XDG_CONFIG_HOME")
    if config_home is None:
        home = _home(environ)
        if home is None:
            return []
        config_home = os.path.join(home, ".config")
    return [os.path.join(config_home, "alai", CONFIG_FILE_NAME)]


def system_config_paths(environ: _c.Mapping[str, str]) -> list[str]:
    """Where the system config file may be, in the order looked for; the first that exists is read."""
    dirs = [d for d in _env(environ, "XDG_CONFIG_DIRS", "/etc/xdg").split(":") if os.path.isabs(d)]
    return [*(os.path.join(d, "alai", CONFIG_FILE_NAME) for d in dirs), f"/etc/alai/{CONFIG_FILE_NAME}"]


def default_cache_dir(environ: _c.Mapping[str, str]) -> str | None:
    """The cache directory when ``cache_dir`` isn't set, or None without a home directory to put it in."""
    if cache_home := _xdg_dir(environ, "XDG_CACHE_HOME"):
        return os.path.join(cache_home, "alai")
    home = _home(environ)
    return None if home is None else os.path.join(home, ".cache", "alai")


def _file_layer(table: _c.Mapping[str, object], path: str, level: Level, home: str | None) -> _Layer:
    """The settings in a config file.

    Only the project's own ``alai.toml`` can have ``[project]`` and the manifest's tables, which describe that one
    project. Its manifest keys are checked here and read by ``_project.load_project``. Every other file, including a
    ``--config-file``, holds settings only.
    """
    source = _Source(level, path, os.path.dirname(path), home)
    layer = _Layer()

    # A quoted key such as "project.collections_dir" is one key, not the table, so it can't name a setting.
    if dotted := [key for key in table if "." in key]:
        raise ConfigError(f"{path}: '{dotted[0]}' is a quoted key, not a table; use [{dotted[0].split('.')[0]}]")
    values = dict(table)
    # [tool] is left to other tools, as pyproject.toml's is: alai never reads it.
    if "tool" in values and as_table(values.pop("tool")) is None:
        raise ConfigError(f"{path}: 'tool' must be a table ([tool.<name>])")
    # Everything under [project] describes the project; everything else is a setting.
    is_project = level is Level.PROJECT
    if not is_project:
        if "project" in values:
            hint = "; --config-file only takes settings" if level is Level.EXPLICIT else ""
            raise ConfigError(f"{path}: [project] is only allowed in the project's alai.toml{hint}")
    else:
        # Also checks [project] is a table.
        layer.manifest = raw_manifest(table, path)
        project = as_table(values.pop("project", None)) or {}
        values.update((f"project.{key}", value) for key, value in project.items() if key not in MANIFEST_PROJECT_KEYS)

    for key, value in values.items():
        setting = FILE_SETTINGS.get(key)
        if setting is None:
            raise ConfigError(f"{path}: unknown setting '{key}'{suggest_key(key, manifest=is_project)}")

        if setting.boolean:
            # TOML's true or false, not a string such as "yes".
            if not isinstance(value, bool):
                raise ConfigError(f"{path}: '{key}' must be true or false")
            layer.add(setting, value, source)
        elif setting.parse_file:
            for item in setting.parse_file(value, source):
                layer.add(setting, item, source)
        else:
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{source.description}: '{key}' must be a non-empty string")
            layer.add(setting, setting.parse(value, source), source)

    return layer


def raw_manifest(table: _c.Mapping[str, object], path: str) -> RawManifest:
    """Check the manifest keys of a parsed project ``alai.toml`` have the right types, and return them."""
    project = _table_value(table, "project", path, "[project]")
    collections = _string_list(project.get("collections", []), path, "project.collections", "requirement strings")
    requires_ansible = project.get("requires_ansible")
    if requires_ansible is not None and not isinstance(requires_ansible, str):
        raise ConfigError(f"{path}: 'project.requires_ansible' must be a string")

    groups: dict[str, list[str]] = {}
    for name, value in _table_value(project, "groups", path, "[project.groups.<name>]", "project.").items():
        where = f"project.groups.{name}"
        group = as_table(value)
        if group is None:
            raise ConfigError(f"{path}: '{where}' must be a table ([{where}])")
        if unknown := sorted(set(group) - {"collections"}):
            raise ConfigError(f"{path}: unknown key '{where}.{unknown[0]}'")
        groups[name] = _string_list(group.get("collections", []), path, f"{where}.collections", "requirement strings")

    overrides: dict[str, str] = {}
    ignore_requires_ansible: list[str] = []
    for key, value in _table_value(project, "overrides", path, "[project.overrides]", "project.").items():
        if key == "ignore_requires_ansible":
            ignore_requires_ansible = _string_list(value, path, f"project.overrides.{key}", "collection names")
        elif isinstance(value, str):
            overrides[key] = value
        else:
            raise ConfigError(
                f"{path}: project.overrides.\"{key}\" must be a version requirement string, such as '>=6'"
            )

    sources: dict[str, dict[str, object]] = {}
    for key, value in _table_value(project, "sources", path, "[project.sources]", "project.").items():
        source = as_table(value)
        if source is None:
            raise ConfigError(f'{path}: project.sources."{key}" must be a table, such as {{ index = "internal" }}')
        sources[key] = source

    return RawManifest(
        path,
        collections=collections,
        requires_ansible=requires_ansible,
        groups=groups,
        overrides=overrides,
        ignore_requires_ansible=ignore_requires_ansible,
        sources=sources,
    )


def _table_value(table: _c.Mapping[str, object], key: str, path: str, form: str, prefix: str = "") -> dict[str, object]:
    """``table[key]``, which must be a table if it's there, or an empty table if it isn't. ``prefix`` is the path to
    ``table`` for messages, such as ``project.``."""
    if key not in table:
        return {}
    value = as_table(table[key])
    if value is None:
        raise ConfigError(f"{path}: '{prefix}{key}' must be a table ({form})")
    return value


def _string_list(value: object, path: str, key: str, what: str) -> list[str]:
    items = string_list(value)
    if items is None:
        raise ConfigError(f"{path}: '{key}' must be an array of {what}")
    return items


def _index_tables(value: object, source: _Source) -> _c.Iterator[Index]:
    if not isinstance(value, list):
        raise ConfigError(f"{source.description}: 'index' must be an array of tables ([[index]])")

    seen: set[str] = set()
    items: list[object] = value  # pyright: ignore[reportUnknownVariableType]
    for i, item in enumerate(items):
        where = f"{source.description}: index[{i}]"
        entry = as_table(item)
        if entry is None:
            raise ConfigError(f"{where}: expected a table")
        if unknown := sorted(set(entry) - {"name", "url", "default"}):
            raise ConfigError(f"{where}: unknown key '{unknown[0]}'")

        name = entry.get("name")
        url = entry.get("url")
        default = entry.get("default", False)
        if not isinstance(name, str):
            raise ConfigError(f"{where}: 'name' is required and must be a string")
        if not isinstance(url, str):
            raise ConfigError(f"{where}: 'url' is required and must be a string")
        if not isinstance(default, bool):
            raise ConfigError(f"{where}: 'default' must be a boolean")
        if name in seen:
            raise ConfigError(f"{where}: index '{name}' is defined more than once")
        if name == DEFAULT_INDEX_NAME and not default:
            # The name always means the default index: one taking it without replacing the default would drop the
            # built-in index without saying so.
            raise ConfigError(
                f"{where}: '{DEFAULT_INDEX_NAME}' is the default index's name; set default = true to replace the "
                + "default index, or choose another name"
            )
        seen.add(name)

        check_name_of("index", name, where)
        yield Index(name, _index_url(url, where), default, source.description)


def _cli_layer(overrides: Overrides, cwd: str, home: str | None) -> _Layer:
    layer = _Layer()
    for setting in SETTINGS:
        value = overrides.settings.get(setting.name)
        source = _Source(Level.CLI, setting.flag, cwd, home)
        if isinstance(value, bool):
            # --offline and --no-offline give the value itself.
            layer.add(setting, value, source)
        elif isinstance(value, str):
            # An empty value, such as --link-mode "", leaves the setting unset.
            if value:
                layer.add(setting, setting.parse(value, source), source)
        elif value:
            # A repeated flag's values are each parsed, so --index "" is an error rather than skipped.
            for item in value:
                layer.add(setting, setting.parse(item, source), source)
    return layer


def _env_layer(environ: _c.Mapping[str, str], cwd: str, home: str | None) -> _Layer:
    layer = _Layer()
    for setting in SETTINGS:
        value = _env(environ, setting.env)
        source = _Source(Level.ENV, setting.env, cwd, home)
        for item in value.split() if setting.repeat else [value]:
            if item:
                layer.add(setting, setting.parse(item, source), source)
    return layer


def _index_arg(value: str, source: _Source) -> Index:
    """Parse ``NAME=URL`` from ``--index`` or ``ALAI_INDEX``."""
    name, sep, url = value.partition("=")
    if not sep:
        raise ConfigError(f"{source.description}: expected NAME=URL, got '{value}'")
    if name == DEFAULT_INDEX_NAME:
        raise ConfigError(
            f"{source.description} '{value}': '{DEFAULT_INDEX_NAME}' is the default index's name; replace the "
            + "default index with --default-index or ALAI_DEFAULT_INDEX, or choose another name"
        )
    return _arg_index(name, url, value, source, default=False)


def _default_index_arg(value: str, source: _Source) -> Index:
    """Parse ``NAME=URL`` or a bare ``URL``, named ``alai``, from ``--default-index`` or ``ALAI_DEFAULT_INDEX``."""
    name, sep, url = value.partition("=")
    if not sep or not NAME_RE.fullmatch(name):
        name, url = DEFAULT_INDEX_NAME, value
    return _arg_index(name, url, value, source, default=True)


def _arg_index(name: str, url: str, value: str, source: _Source, *, default: bool) -> Index:
    where = f"{source.description} '{value}'"
    check_name_of("index", name, where)
    return Index(name, _index_url(url, where), default, source.description)


def _index_url(url: str, where: str) -> str:
    if not url.startswith(_INDEX_URL_SCHEMES):
        raise ConfigError(f"{where}: index URL '{url}' must start with http://, https:// or file://")
    if "?" in url or "#" in url:
        raise ConfigError(f"{where}: index URL '{url}' must not have a query or fragment")
    return url if url.endswith("/") else f"{url}/"


def _merge(layers: _c.Sequence[_Layer]) -> _Layer:
    """Merge layers given highest precedence first: the first scalar set wins, lists concatenate."""
    merged = _Layer()
    for layer in layers:
        for target, value in layer.values.items():
            if target in merged.values:
                merged.overridden.setdefault(target, []).append((value, layer.sources[target]))
            else:
                merged.values[target] = value
                merged.sources[target] = layer.sources[target]
        for target, values in layer.lists.items():
            merged.lists.setdefault(target, []).extend(values)
    return merged


def _resolve_indexes(indexes: _c.Sequence[Index]) -> tuple[Index, ...]:
    builtin = Index(DEFAULT_INDEX_NAME, DEFAULT_INDEX_URL, default=True, source="built-in")
    seen: set[str] = set()
    ordered: list[Index] = []
    default: Index | None = None

    for index in [*indexes, builtin]:
        if index.name in seen:
            debug(f"Ignoring index '{index.name}' from {home_path(index.source)}, already defined at a higher level")
            continue
        seen.add(index.name)

        if not index.default:
            ordered.append(index)
        elif default is None:
            default = index
        else:
            debug(f"Ignoring default index '{index.name}' from {home_path(index.source)}, default is '{default.name}'")

    if default:
        ordered.append(default)
    return tuple(ordered)


def _bool_arg(value: str, source: _Source) -> bool:
    """Parse a boolean environment variable, with the words ``ALAI_NO_CONFIG`` takes."""
    return parse_bool(value, source.description)


def _link_mode(value: str, source: _Source) -> LinkMode:
    try:
        return LinkMode(value)
    except ValueError as e:
        choices = ", ".join(m.value for m in LinkMode)
        raise ConfigError(f"{source.description}: invalid link mode '{value}', expected one of {choices}") from e


def _collections_dir(value: str, source: _Source) -> str:
    path = _resolve_path(value, source)
    # Ansible accepts a collection path given with or without the trailing ansible_collections.
    return os.path.dirname(path) if os.path.basename(path) == "ansible_collections" else path


def _resolve_path(value: str, source: _Source) -> str:
    if value == "~" or value.startswith("~/"):
        if source.home is None:
            raise ConfigError(f"cannot expand '{value}', the home directory is unknown")
        path = os.path.join(source.home, value[2:])
    else:
        path = os.path.expanduser(value)
    # Normalised so "./collections" or a trailing slash compare equal to the plain path.
    return os.path.normpath(os.path.join(source.base_dir, path))


def _home(environ: _c.Mapping[str, str]) -> str | None:
    if home := _env(environ, "HOME"):
        return home
    # Without HOME, expanduser falls back to the password database and returns "~" unchanged if that fails too.
    home = os.path.expanduser("~")
    return None if home == "~" else home


def _xdg_dir(environ: _c.Mapping[str, str], name: str) -> str | None:
    value = _env(environ, name)
    # The XDG spec says relative paths are invalid and must be ignored.
    return value if value and os.path.isabs(value) else None


def _env(environ: _c.Mapping[str, str], name: str, default: str = "") -> str:
    return environ.get(name) or default


def suggest_key(key: str, *, manifest: bool = False) -> str:
    """``, did you mean 'KEY'?`` for the config file key closest to ``key``, or an empty string.

    With ``manifest``, the manifest's keys are suggested too.
    """
    import difflib

    keys = list(FILE_SETTINGS)
    if manifest:
        keys += [f"project.{k}" for k in MANIFEST_PROJECT_KEYS]
    matches = difflib.get_close_matches(key, keys, n=1)
    return f", did you mean '{matches[0]}'?" if matches else ""


# Last in the module, as it refers to the parsers above.
SETTINGS = (
    SettingDefinition(
        "collections_dir",
        key="project.collections_dir",
        flag="--collections-dir",
        env="ALAI_COLLECTIONS_DIR",
        metavar="DIR",
        help="install collections under DIR/ansible_collections",
        parse=_collections_dir,
        group="install",
    ),
    SettingDefinition(
        "cache_dir",
        key="cache_dir",
        flag="--cache-dir",
        env="ALAI_CACHE_DIR",
        metavar="DIR",
        help="the cache directory",
        parse=_resolve_path,
        global_flag=True,
    ),
    SettingDefinition(
        "link_mode",
        key="link_mode",
        flag="--link-mode",
        env="ALAI_LINK_MODE",
        choices=tuple(m.value for m in LinkMode),
        help="how files get from the cache into the collections directory",
        parse=_link_mode,
        group="install",
    ),
    SettingDefinition(
        "offline",
        key="offline",
        flag="--offline",
        env="ALAI_OFFLINE",
        help="use only what's cached, without connecting to any index",
        parse=_bool_arg,
        boolean=True,
        group="resolve",
    ),
    SettingDefinition(
        "index",
        key="index",
        target="indexes",
        flag="--index",
        env="ALAI_INDEX",
        metavar="NAME=URL",
        help="an index to resolve from, before those in config files; can be repeated",
        parse=_index_arg,
        parse_file=_index_tables,
        many=True,
        repeat=True,
        group="resolve",
    ),
    SettingDefinition(
        "default_index",
        key=None,
        target="indexes",
        flag="--default-index",
        env="ALAI_DEFAULT_INDEX",
        metavar="[NAME=]URL",
        help="replace the default index",
        parse=_default_index_arg,
        many=True,
        group="resolve",
    ),
)
FILE_SETTINGS = {s.key: s for s in SETTINGS if s.key}
"""The settings a config file can set, by key."""
