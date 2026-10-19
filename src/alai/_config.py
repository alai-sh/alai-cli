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
import re
import sys

from ._output import debug

CONFIG_FILE_NAME = "alai.toml"
DEFAULT_INDEX_NAME = "alai"
DEFAULT_INDEX_URL = "https://index.alai.sh/"

_INDEX_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_INDEX_URL_SCHEMES = ("http://", "https://", "file://")
_TRUE = frozenset(("1", "true", "yes", "on"))
_FALSE = frozenset(("0", "false", "no", "off"))


class ConfigError(Exception):
    """A config file, environment variable or flag holds an invalid value."""


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


class _Record:
    """A value class with equality and repr from its ``__slots__``.

    Used instead of dataclasses, which cost ~3.5 ms to import and apply on every command.
    """

    __slots__: tuple[str, ...] = ()

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and all(getattr(self, n) == getattr(other, n) for n in self.__slots__)

    def __repr__(self) -> str:
        fields = ", ".join(f"{n}={getattr(self, n)!r}" for n in self.__slots__)
        return f"{type(self).__name__}({fields})"


class Index(_Record):
    __slots__ = ("default", "name", "source", "url")

    def __init__(self, name: str, url: str, default: bool = False, source: str = "") -> None:
        self.name = name
        self.url = url
        self.default = default
        self.source = source
        """Where the index was defined, for messages."""


class Project(_Record):
    __slots__ = ("config_file", "marker", "root")

    def __init__(self, root: str, config_file: str | None, marker: str | None) -> None:
        self.root = root
        self.config_file = config_file
        """The project's ``alai.toml``, if it has one."""
        self.marker = marker
        """The marker that chose ``root``, or None when no marker was found."""


class Overrides(_Record):
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
        **settings: str | _c.Sequence[str] | None,
    ) -> None:
        if unknown := sorted(settings.keys() - {s.name for s in SETTINGS if s.flag}):
            raise TypeError(f"Overrides got an unexpected keyword argument '{unknown[0]}'")
        self.project = project
        self.config_file = config_file
        self.no_config = no_config
        self.settings = settings


class ConfigFile(_Record):
    __slots__ = ("level", "path", "read")

    def __init__(self, level: Level, path: str, *, read: bool) -> None:
        self.level = level
        self.path = path
        self.read = read
        """False when alai looked for the file and it doesn't exist."""


class Settings(_Record):
    __slots__ = (
        "_cache_dir",
        "collections_dir",
        "config_files",
        "indexes",
        "link_mode",
        "no_config",
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
        searched: tuple[ConfigFile, ...] = (),
        no_config: bool = False,
        sources: dict[str, str] | None = None,
        overridden: dict[str, tuple[tuple[object, str], ...]] | None = None,
    ) -> None:
        self.project = project
        self.config_files = config_files
        """The config files read, highest precedence first."""
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

    @property
    def cache_dir(self) -> str:
        """The cache directory.

        Raises ConfigError if it wasn't set and there's no home directory for the default. That's left until a
        command uses it, so one that never touches the cache still runs without a home directory.
        """
        if self._cache_dir is None:
            raise ConfigError("cannot find the home directory for the cache, set ALAI_CACHE_DIR")
        return self._cache_dir


class _Source(_Record):
    __slots__ = ("base_dir", "description", "home", "level")

    def __init__(self, level: Level, description: str, base_dir: str, home: str | None) -> None:
        self.level = level
        self.description = description
        self.base_dir = base_dir
        """What a relative path is relative to."""
        self.home = home
        """What a leading ``~`` expands to, or None if the home directory is unknown."""


class _Layer(_Record):
    """The settings one level sets, by each setting's ``target``."""

    __slots__ = ("lists", "overridden", "sources", "values")

    def __init__(self) -> None:
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


class SettingDefinition(_Record):
    """A setting read from config files, environment variables or flags.

    Every setting is listed once in ``SETTINGS``. Reading each level, merging them, the argparse options and
    ``config show`` all work from that list, so adding a setting means adding it there and to ``Settings``.
    """

    __slots__ = (
        "choices",
        "env",
        "flag",
        "global_flag",
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
            global_flag: The flag is accepted by every command, rather than only those that resolve or install
                collections.
            many: The value is a list, concatenated across levels highest first, rather than a single value taken
                from the highest level that sets it.
            repeat: The flag can be given more than once, and the environment variable holds several values
                separated by whitespace.
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


def load_settings(
    overrides: Overrides | None = None,
    *,
    cwd: str | None = None,
    environ: _c.Mapping[str, str] | None = None,
) -> Settings:
    """Resolve the settings for a command run from ``cwd``."""
    overrides = overrides or Overrides()
    cwd = cwd or os.getcwd()
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
    no_config = overrides.no_config or _env_bool(environ, "ALAI_NO_CONFIG")
    if config_file:
        path = _resolve_path(config_file, here)
        layers.append(_file_layer(_read_toml(path), path, Level.EXPLICIT, home))
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
                layers.append(_file_layer(_read_toml(path), path, level, home))
                searched.append(ConfigFile(level, path, read=True))
                break

    merged = _merge(layers)
    # Each value is what its setting's parser returned; the isinstance checks give the types back.
    collections_dir = merged.values.get("collections_dir")
    cache_dir = merged.values.get("cache_dir")
    link_mode = merged.values.get("link_mode")
    indexes = [i for i in merged.lists.get("indexes", ()) if isinstance(i, Index)]
    sources = {s.target: merged.sources.get(s.target, "default") for s in SETTINGS if not s.many}
    return Settings(
        project=project,
        config_files=tuple(f.path for f in searched if f.read),
        searched=tuple(searched),
        no_config=no_config and not config_file,
        sources=sources,
        overridden={target: tuple(values) for target, values in merged.overridden.items()},
        collections_dir=collections_dir
        if isinstance(collections_dir, str)
        else os.path.join(project.root, "collections"),
        cache_dir=cache_dir if isinstance(cache_dir, str) else default_cache_dir(environ),
        link_mode=link_mode if isinstance(link_mode, LinkMode) else LinkMode.AUTO,
        indexes=_resolve_indexes(indexes),
    )


def find_project(start: str) -> Project:
    """Find the project root by walking up from ``start``.

    The nearest ``alai.toml`` wins, then the nearest ``ansible.cfg``, then the nearest
    ``collections/ansible_collections``.
    """
    start = os.path.realpath(start)
    ansible_cfg: Project | None = None
    collections: Project | None = None

    directory = start
    while True:
        alai_toml = os.path.join(directory, CONFIG_FILE_NAME)
        if os.path.isfile(alai_toml):
            return Project(directory, alai_toml, CONFIG_FILE_NAME)

        if ansible_cfg is None and os.path.isfile(os.path.join(directory, "ansible.cfg")):
            ansible_cfg = Project(directory, None, "ansible.cfg")
        if collections is None and os.path.isdir(os.path.join(directory, "collections", "ansible_collections")):
            collections = Project(directory, None, "collections")

        parent = os.path.dirname(directory)
        if parent == directory:
            return ansible_cfg or collections or Project(start, None, None)
        directory = parent


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


def _as_table(value: object) -> dict[str, object] | None:
    # TOML tables always have string keys.
    return value if isinstance(value, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _read_toml(path: str) -> dict[str, object]:
    # Imported here so a command run without any config files never pays for it.
    if sys.version_info >= (3, 11):
        import tomllib  # pyright: ignore[reportUnreachable]  # checked against the 3.10 floor
    else:
        import tomli as tomllib

    try:
        with open(path, "rb") as fd:
            return tomllib.load(fd)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror or e}") from None


def _file_layer(table: _c.Mapping[str, object], path: str, level: Level, home: str | None) -> _Layer:
    description = path
    source = _Source(level, description, os.path.dirname(path), home)
    layer = _Layer()

    # A quoted key such as "project.collections_dir" is one key, not the table, so it can't name a setting.
    if dotted := [key for key in table if "." in key]:
        raise ConfigError(f"{description}: '{dotted[0]}' is a quoted key, not a table; use [{dotted[0].split('.')[0]}]")
    values = dict(table)
    # [tool] is left to other tools, as pyproject.toml's is: alai never reads it.
    if "tool" in values and _as_table(values.pop("tool")) is None:
        raise ConfigError(f"{description}: 'tool' must be a table ([tool.<name>])")
    if "project" in values:
        # What the project is, rather than how alai runs, so a user or system file can't set it for every project.
        if level in (Level.USER, Level.SYSTEM):
            raise ConfigError(f"{description}: [project] is only allowed in project configuration")
        project = _as_table(values.pop("project"))
        if project is None:
            raise ConfigError(f"{description}: 'project' must be a table ([project])")
        values.update((f"project.{key}", value) for key, value in project.items())

    for key, value in values.items():
        setting = FILE_SETTINGS.get(key)
        if setting is None:
            raise ConfigError(f"{description}: unknown setting '{key}'{suggest_key(key)}")

        if setting.parse_file:
            for item in setting.parse_file(value, source):
                layer.add(setting, item, source)
        else:
            layer.add(setting, setting.parse(_str_value(key, value, source), source), source)

    return layer


def _index_tables(value: object, source: _Source) -> _c.Iterator[Index]:
    if not isinstance(value, list):
        raise ConfigError(f"{source.description}: 'index' must be an array of tables ([[index]])")

    seen: set[str] = set()
    items: list[object] = value  # pyright: ignore[reportUnknownVariableType]
    for i, item in enumerate(items):
        where = f"{source.description}: index[{i}]"
        entry = _as_table(item)
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
        seen.add(name)

        yield Index(_index_name(name, where), _index_url(url, where), default, source.description)


def _cli_layer(overrides: Overrides, cwd: str, home: str | None) -> _Layer:
    layer = _Layer()
    for setting in SETTINGS:
        value = overrides.settings.get(setting.name)
        source = _Source(Level.CLI, setting.flag, cwd, home)
        if isinstance(value, str):
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
    return _arg_index(name, url, value, source, default=False)


def _default_index_arg(value: str, source: _Source) -> Index:
    """Parse ``NAME=URL`` or a bare ``URL``, named ``alai``, from ``--default-index`` or ``ALAI_DEFAULT_INDEX``."""
    name, sep, url = value.partition("=")
    if not sep or not _INDEX_NAME_RE.fullmatch(name):
        name, url = DEFAULT_INDEX_NAME, value
    return _arg_index(name, url, value, source, default=True)


def _arg_index(name: str, url: str, value: str, source: _Source, *, default: bool) -> Index:
    where = f"{source.description} '{value}'"
    return Index(_index_name(name, where), _index_url(url, where), default, source.description)


def _index_name(name: str, where: str) -> str:
    if not _INDEX_NAME_RE.fullmatch(name):
        raise ConfigError(
            f"{where}: invalid index name '{name}', use lowercase letters, digits, '-' and '_', "
            + "starting with a letter or digit"
        )
    return name


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
            debug(f"Ignoring index '{index.name}' from {index.source}, already defined at a higher level")
            continue
        seen.add(index.name)

        if not index.default:
            ordered.append(index)
        elif default is None:
            default = index
        else:
            debug(f"Ignoring default index '{index.name}' from {index.source}, default is '{default.name}'")

    if default:
        ordered.append(default)
    return tuple(ordered)


def _str_value(key: str, value: object, source: _Source) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{source.description}: '{key}' must be a non-empty string")
    return value


def _link_mode(value: str, source: _Source) -> LinkMode:
    try:
        return LinkMode(value)
    except ValueError:
        choices = ", ".join(m.value for m in LinkMode)
        raise ConfigError(f"{source.description}: invalid link mode '{value}', expected one of {choices}") from None


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


def _env_bool(environ: _c.Mapping[str, str], name: str) -> bool:
    value = _env(environ, name).lower()
    if not value or value in _FALSE:
        return False
    if value in _TRUE:
        return True
    raise ConfigError(f"{name}: expected a boolean (1, true, yes, on, 0, false, no, off), got '{value}'")


def suggest_key(key: str) -> str:
    """``, did you mean 'KEY'?`` for the config file key closest to ``key``, or an empty string."""
    import difflib

    matches = difflib.get_close_matches(key, list(FILE_SETTINGS), n=1)
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
    ),
)
FILE_SETTINGS = {s.key: s for s in SETTINGS if s.key}
"""The settings a config file can set, by key."""
