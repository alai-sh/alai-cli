# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest

from alai import _config
from alai._common import ConfigError
from alai._config import (
    DEFAULT_INDEX_URL,
    SETTINGS,
    ConfigFile,
    Index,
    Level,
    LinkMode,
    Overrides,
    Settings,
    find_project,
    load_settings,
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def environ(home: Path, tmp_path: Path) -> dict[str, str]:
    # Point the system config at an empty directory so the host's /etc/xdg is never read.
    xdg_dirs = tmp_path / "xdg"
    xdg_dirs.mkdir()
    return {"HOME": str(home), "XDG_CONFIG_DIRS": str(xdg_dirs)}


def write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def load(cwd: Path, environ: dict[str, str], overrides: Overrides | None = None) -> Settings:
    return load_settings(overrides, cwd=cwd, environ=environ)


def index_names(settings: Settings) -> list[str]:
    return [i.name for i in settings.indexes]


class TestFindProject:
    def test_no_marker_uses_start(self, tmp_path: Path) -> None:
        start = tmp_path / "a" / "b"
        start.mkdir(parents=True)

        project = find_project(start)

        assert project.root == str(start)
        assert project.config_file is None
        assert project.marker is None

    @pytest.mark.parametrize("marker", ["alai.toml", "ansible.cfg"])
    def test_marker_in_parent(self, tmp_path: Path, marker: str) -> None:
        write(tmp_path / "repo" / marker)
        start = tmp_path / "repo" / "roles" / "foo"
        start.mkdir(parents=True)

        project = find_project(start)

        assert project.root == str(tmp_path / "repo")
        assert project.marker == marker

    def test_collections_dir_marker(self, tmp_path: Path) -> None:
        (tmp_path / "repo" / "collections" / "ansible_collections").mkdir(parents=True)
        start = tmp_path / "repo" / "playbooks"
        start.mkdir()

        project = find_project(start)

        assert project.root == str(tmp_path / "repo")
        assert project.marker == "collections"
        assert project.config_file is None

    def test_collections_without_ansible_collections_is_not_marker(self, tmp_path: Path) -> None:
        (tmp_path / "repo" / "collections").mkdir(parents=True)

        assert find_project(tmp_path / "repo").marker is None

    def test_strong_marker_beats_nearer_weak(self, tmp_path: Path) -> None:
        config = write(tmp_path / "repo" / "alai.toml")
        write(tmp_path / "repo" / "playbooks" / "ansible.cfg")

        project = find_project(tmp_path / "repo" / "playbooks")

        assert project.root == str(tmp_path / "repo")
        assert project.config_file == str(config)

    def test_ansible_cfg_beats_nearer_collections(self, tmp_path: Path) -> None:
        write(tmp_path / "repo" / "ansible.cfg")
        (tmp_path / "repo" / "playbooks" / "collections" / "ansible_collections").mkdir(parents=True)

        project = find_project(tmp_path / "repo" / "playbooks")

        assert project.root == str(tmp_path / "repo")
        assert project.marker == "ansible.cfg"

    def test_nearest_weak_marker_wins(self, tmp_path: Path) -> None:
        write(tmp_path / "repo" / "ansible.cfg")
        write(tmp_path / "repo" / "sub" / "ansible.cfg")

        assert find_project(tmp_path / "repo" / "sub").root == str(tmp_path / "repo" / "sub")


class TestDefaults:
    def test_defaults(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        settings = load(tmp_path, environ)

        assert settings.project.root == str(tmp_path)
        assert settings.config_files == ()
        assert settings.collections_dir == str(tmp_path / "collections")
        assert settings.cache_dir == str(home / ".cache" / "alai")
        assert settings.link_mode == LinkMode.AUTO
        assert settings.indexes == (Index("alai", DEFAULT_INDEX_URL, default=True, source="built-in"),)

    def test_xdg_cache_home(self, tmp_path: Path, environ: dict[str, str]) -> None:
        environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")

        assert load(tmp_path, environ).cache_dir == str(tmp_path / "cache" / "alai")

    def test_relative_xdg_cache_home_ignored(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        environ["XDG_CACHE_HOME"] = "cache"

        assert load(tmp_path, environ).cache_dir == str(home / ".cache" / "alai")


class TestConfigFiles:
    def test_project_file(self, tmp_path: Path, environ: dict[str, str]) -> None:
        config = write(
            tmp_path / "repo" / "alai.toml", 'link_mode = "copy"\n[project]\ncollections_dir = "ansible/collections"\n'
        )
        (tmp_path / "repo" / "sub").mkdir()

        settings = load(tmp_path / "repo" / "sub", environ)

        assert settings.config_files == (str(config),)
        # Relative to the config file, not the cwd.
        assert settings.collections_dir == str(tmp_path / "repo" / "ansible" / "collections")
        assert settings.link_mode == LinkMode.COPY

    def test_user_file(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        config = write(home / ".config" / "alai" / "alai.toml", 'cache_dir = "~/my-cache"\n')

        settings = load(tmp_path, environ)

        assert settings.config_files == (str(config),)
        assert settings.cache_dir == str(home / "my-cache")

    def test_xdg_config_home(self, tmp_path: Path, environ: dict[str, str]) -> None:
        config = write(tmp_path / "cfg" / "alai" / "alai.toml", 'link_mode = "copy"\n')
        environ["XDG_CONFIG_HOME"] = str(tmp_path / "cfg")

        assert load(tmp_path, environ).config_files == (str(config),)

    def test_system_file_first_xdg_dir(self, tmp_path: Path, environ: dict[str, str]) -> None:
        second = write(tmp_path / "xdg2" / "alai" / "alai.toml")
        write(tmp_path / "xdg3" / "alai" / "alai.toml")
        environ["XDG_CONFIG_DIRS"] = f"relative:{tmp_path / 'xdg1'}:{tmp_path / 'xdg2'}:{tmp_path / 'xdg3'}"

        assert load(tmp_path, environ).config_files == (str(second),)

    def test_precedence(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        project = write(tmp_path / "alai.toml", 'link_mode = "copy"\n')
        user = write(home / ".config" / "alai" / "alai.toml", 'link_mode = "hardlink"\ncache_dir = "/user"\n')
        system = write(tmp_path / "xdg" / "alai" / "alai.toml", 'link_mode = "symlink"\ncache_dir = "/system"\n')

        settings = load(tmp_path, environ)

        assert settings.config_files == (str(project), str(user), str(system))
        assert settings.link_mode == LinkMode.COPY
        assert settings.cache_dir == "/user"

    def test_env_beats_files_and_cli_beats_env(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n')
        environ["ALAI_LINK_MODE"] = "hardlink"

        assert load(tmp_path, environ).link_mode == LinkMode.HARDLINK
        assert load(tmp_path, environ, Overrides(link_mode="clone")).link_mode == LinkMode.CLONE
        assert load(tmp_path, environ, Overrides(link_mode="auto")).link_mode == LinkMode.AUTO

    def test_empty_env_is_unset(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n')
        environ["ALAI_LINK_MODE"] = ""

        assert load(tmp_path, environ).link_mode == LinkMode.COPY

    def test_empty_flag_is_unset(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n')

        assert load(tmp_path, environ, Overrides(link_mode="")).link_mode == LinkMode.COPY

    def test_user_file_read_once_from_its_own_dir(self, home: Path, environ: dict[str, str]) -> None:
        config = write(home / ".config" / "alai" / "alai.toml")

        assert load(config.parent, environ).config_files == (str(config),)

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_no_config(self, tmp_path: Path, home: Path, environ: dict[str, str], value: str) -> None:
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n')
        write(home / ".config" / "alai" / "alai.toml", 'link_mode = "copy"\n')
        environ["ALAI_NO_CONFIG"] = value

        settings = load(tmp_path, environ)

        assert settings.config_files == ()
        assert settings.link_mode == LinkMode.AUTO
        # The project is still found by its marker.
        assert settings.project.root == str(tmp_path)

    def test_no_config_false(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml")
        environ["ALAI_NO_CONFIG"] = "off"

        assert len(load(tmp_path, environ).config_files) == 1

    def test_no_config_invalid(self, tmp_path: Path, environ: dict[str, str]) -> None:
        environ["ALAI_NO_CONFIG"] = "maybe"

        with pytest.raises(ConfigError, match="ALAI_NO_CONFIG: expected a boolean"):
            load(tmp_path, environ)

    def test_config_file_replaces_discovery(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n')
        write(home / ".config" / "alai" / "alai.toml", 'cache_dir = "/user"\n')
        explicit = write(tmp_path / "other" / "ci.toml", 'link_mode = "hardlink"\n')

        settings = load(tmp_path, environ, Overrides(config_file="other/ci.toml"))

        assert settings.config_files == (str(explicit),)
        assert settings.link_mode == LinkMode.HARDLINK
        assert settings.cache_dir == str(home / ".cache" / "alai")

    @pytest.mark.parametrize("name", ["ci.toml", "alai.toml"])
    def test_config_file_settings_only(self, tmp_path: Path, environ: dict[str, str], name: str) -> None:
        # Even the project's own alai.toml given as --config-file: the flag takes settings, not a project.
        write(tmp_path / name, '[project]\ncollections_dir = "c"\n')

        with pytest.raises(
            ConfigError, match=r"\[project\] is only allowed in the project's alai\.toml; --config-file"
        ):
            load(tmp_path, environ, Overrides(config_file=name))

    def test_config_file_missing(self, tmp_path: Path, environ: dict[str, str]) -> None:
        with pytest.raises(ConfigError, match=r"missing\.toml"):
            load(tmp_path, environ, Overrides(config_file="missing.toml"))


class TestProjectOverride:
    def test_project_override(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "repo" / "alai.toml")
        (tmp_path / "repo" / "sub").mkdir()
        (tmp_path / "elsewhere").mkdir()

        settings = load(tmp_path / "elsewhere", environ, Overrides(project="../repo/sub"))

        assert settings.project.root == str(tmp_path / "repo")

    def test_project_env(self, tmp_path: Path, environ: dict[str, str]) -> None:
        (tmp_path / "repo").mkdir()
        environ["ALAI_PROJECT"] = str(tmp_path / "repo")

        assert load(tmp_path, environ).collections_dir == str(tmp_path / "repo" / "collections")

    def test_project_missing(self, tmp_path: Path, environ: dict[str, str]) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            load(tmp_path, environ, Overrides(project="nope"))


class TestValidation:
    def test_tool_table_ignored(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n[tool.my-ci]\nanything = [1, "x"]\n')
        write(home / ".config" / "alai" / "alai.toml", "[tool.other]\nkey = true\n")

        assert load(tmp_path, environ).link_mode == LinkMode.COPY

    def test_tool_not_table(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", 'tool = "x"\n')

        with pytest.raises(ConfigError, match=r"'tool' must be a table"):
            load(tmp_path, environ)

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ("link_mode = ", "invalid TOML"),
            ('collections_dir = "x"', "unknown setting 'collections_dir', did you mean 'project.collections_dir'"),
            ('[project]\ncollection_dir = "x"', "unknown setting 'project.collection_dir', did you mean 'project.co"),
            ('collections-dir = "x"', "unknown setting 'collections-dir', did you mean 'project.collections_dir'"),
            ('"project.collections_dir" = "x"', "'project.collections_dir' is a quoted key, not a table"),
            ('project = "x"', r"'project' must be a table \(\[project\]\)"),
            ('zzz = "x"', "unknown setting 'zzz'$"),
            ("cache_dir = 1", "'cache_dir' must be a non-empty string"),
            ('cache_dir = ""', "'cache_dir' must be a non-empty string"),
            ('link_mode = "reflink"', "invalid link mode 'reflink'"),
            ('index = "x"', r"must be an array of tables \(\[\[index\]\]\)"),
            ("index = [1]", r"index\[0\]: expected a table"),
            ('[[index]]\nurl = "https://x/"', "'name' is required"),
            ('[[index]]\nname = "x"', "'url' is required"),
            ('[[index]]\nname = "x"\nurl = "https://x/"\ndefault = "yes"', "'default' must be a boolean"),
            ('[[index]]\nname = "x"\nurl = "https://x/"\nexplicit = true', "unknown key 'explicit'"),
            ('[[index]]\nname = "My:Index"\nurl = "https://x/"', "invalid index name 'My:Index'"),
            ('[[index]]\nname = "x"\nurl = "ftp://x/"', "must start with http://, https:// or file://"),
            ('[[index]]\nname = "x"\nurl = "https://x/?token=1"', "must not have a query or fragment"),
            (
                '[[index]]\nname = "x"\nurl = "https://a/"\n[[index]]\nname = "x"\nurl = "https://b/"',
                r"index\[1\]: index 'x' is defined more than once",
            ),
        ],
    )
    def test_invalid_project_file(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write(tmp_path / "alai.toml", content)

        with pytest.raises(ConfigError, match=match) as e:
            load(tmp_path, environ)
        assert str(e.value).startswith(str(tmp_path / "alai.toml"))

    @pytest.mark.parametrize("level", ["user", "system"])
    def test_project_table_project_only(self, tmp_path: Path, home: Path, environ: dict[str, str], level: str) -> None:
        base = home / ".config" if level == "user" else tmp_path / "xdg"
        write(base / "alai" / "alai.toml", '[project]\ncollections_dir = "/shared"\n')

        with pytest.raises(ConfigError, match=r"\[project\] is only allowed in the project's alai\.toml$"):
            load(tmp_path, environ)

    def test_invalid_link_mode_env(self, tmp_path: Path, environ: dict[str, str]) -> None:
        environ["ALAI_LINK_MODE"] = "fast"

        with pytest.raises(ConfigError, match="ALAI_LINK_MODE: invalid link mode 'fast'"):
            load(tmp_path, environ)


class TestBoolean:
    """system_certs, the first true-or-false setting: on by default, and either way from any level."""

    def test_default(self, tmp_path: Path, environ: dict[str, str]) -> None:
        assert load(tmp_path, environ).system_certs is True

    def test_file(self, tmp_path: Path, environ: dict[str, str]) -> None:
        config = write(tmp_path / "alai.toml", "system_certs = false\n")

        settings = load(tmp_path, environ)

        assert settings.system_certs is False
        assert settings.sources["system_certs"] == str(config)

    @pytest.mark.parametrize(("value", "expected"), [("0", False), ("off", False), ("YES", True), ("1", True)])
    def test_env(self, tmp_path: Path, environ: dict[str, str], value: str, expected: bool) -> None:
        write(tmp_path / "alai.toml", f"system_certs = {str(not expected).lower()}\n")
        environ["ALAI_SYSTEM_CERTS"] = value

        assert load(tmp_path, environ).system_certs is expected

    @pytest.mark.parametrize("flag", [True, False])
    def test_flag_beats_env_either_way(self, tmp_path: Path, environ: dict[str, str], flag: bool) -> None:
        environ["ALAI_SYSTEM_CERTS"] = "0" if flag else "1"

        settings = load(tmp_path, environ, Overrides(system_certs=flag))

        assert settings.system_certs is flag
        assert settings.sources["system_certs"] == "--system-certs"

    def test_invalid_env(self, tmp_path: Path, environ: dict[str, str]) -> None:
        environ["ALAI_SYSTEM_CERTS"] = "maybe"

        with pytest.raises(ConfigError, match=r"ALAI_SYSTEM_CERTS: expected a boolean .* got 'maybe'"):
            load(tmp_path, environ)

    @pytest.mark.parametrize("value", ['"false"', "0", "[true]"])
    def test_file_needs_toml_boolean(self, tmp_path: Path, environ: dict[str, str], value: str) -> None:
        write(tmp_path / "alai.toml", f"system_certs = {value}\n")

        with pytest.raises(ConfigError, match="'system_certs' must be true or false"):
            load(tmp_path, environ)


class TestPaths:
    @pytest.mark.parametrize("value", ["collections/ansible_collections", "collections"])
    def test_collections_dir_strips_ansible_collections(
        self, tmp_path: Path, environ: dict[str, str], value: str
    ) -> None:
        write(tmp_path / "alai.toml", f'[project]\ncollections_dir = "{value}"\n')

        assert load(tmp_path, environ).collections_dir == str(tmp_path / "collections")

    def test_env_and_cli_paths_relative_to_cwd(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml")
        cwd = tmp_path / "sub"
        cwd.mkdir()
        environ["ALAI_COLLECTIONS_DIR"] = "env-collections"

        assert load(cwd, environ).collections_dir == str(cwd / "env-collections")
        overrides = Overrides(collections_dir="cli/ansible_collections", cache_dir="cache")
        settings = load(cwd, environ, overrides)
        assert settings.collections_dir == str(cwd / "cli")
        assert settings.cache_dir == str(cwd / "cache")

    def test_tilde(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        environ["ALAI_CACHE_DIR"] = "~"

        assert load(tmp_path, environ).cache_dir == str(home)

    def test_absolute(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", f'cache_dir = "{tmp_path / "abs"}"\n')

        assert load(tmp_path, environ).cache_dir == str(tmp_path / "abs")


class TestIndexes:
    def test_order_and_merge(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        write(
            tmp_path / "alai.toml",
            '[[index]]\nname = "proj"\nurl = "https://proj"\n[[index]]\nname = "shared"\nurl = "https://proj-shared/"\n',
        )
        write(
            home / ".config" / "alai" / "alai.toml",
            '[[index]]\nname = "shared"\nurl = "https://user-shared/"\n[[index]]\nname = "user"\nurl = "file:///idx"\n',
        )
        environ["ALAI_INDEX"] = "env=https://env/  other=https://other/"

        settings = load(tmp_path, environ, Overrides(index=["cli=https://cli/"]))

        assert index_names(settings) == ["cli", "env", "other", "proj", "shared", "user", "alai"]
        by_name = {i.name: i for i in settings.indexes}
        assert by_name["proj"].url == "https://proj/"
        assert by_name["shared"].url == "https://proj-shared/"
        assert by_name["shared"].source == str(tmp_path / "alai.toml")

    def test_default_from_file_replaces_builtin(self, tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
        write(
            tmp_path / "alai.toml",
            '[[index]]\nname = "mirror"\nurl = "https://mirror/"\ndefault = true\n'
            + '[[index]]\nname = "extra"\nurl = "https://extra/"\n',
        )
        write(
            home / ".config" / "alai" / "alai.toml",
            '[[index]]\nname = "user-default"\nurl = "https://ud/"\ndefault = true\n',
        )

        settings = load(tmp_path, environ)

        assert index_names(settings) == ["extra", "mirror"]
        assert settings.indexes[-1].default

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("https://mirror.example.com", ("alai", "https://mirror.example.com/")),
            ("https://mirror.example.com/a=b", ("alai", "https://mirror.example.com/a=b/")),
            ("corp=https://corp/", ("corp", "https://corp/")),
        ],
    )
    def test_default_index_env(
        self, tmp_path: Path, environ: dict[str, str], value: str, expected: tuple[str, str]
    ) -> None:
        environ["ALAI_DEFAULT_INDEX"] = value

        settings = load(tmp_path, environ)

        assert [(i.name, i.url) for i in settings.indexes] == [expected]

    def test_default_index_cli_beats_file(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", '[[index]]\nname = "mirror"\nurl = "https://mirror/"\ndefault = true\n')

        settings = load(tmp_path, environ, Overrides(default_index="https://cli-default/"))

        assert [(i.name, i.url) for i in settings.indexes] == [("alai", "https://cli-default/")]

    def test_alai_must_be_the_default(self, tmp_path: Path, environ: dict[str, str]) -> None:
        # The name always means the default index, so taking it without replacing the default is an error, rather
        # than dropping the built-in index without saying so.
        write(tmp_path / "alai.toml", '[[index]]\nname = "alai"\nurl = "https://pinned/"\n')

        with pytest.raises(ConfigError, match=r"'alai' is the default index's name; set default = true"):
            load(tmp_path, environ)

    def test_alai_as_the_default(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", '[[index]]\nname = "alai"\nurl = "https://mirror/"\ndefault = true\n')

        settings = load(tmp_path, environ)

        assert [(i.name, i.url, i.default) for i in settings.indexes] == [("alai", "https://mirror/", True)]

    def test_alai_index_arg_refused(self, tmp_path: Path, environ: dict[str, str]) -> None:
        with pytest.raises(ConfigError, match=r"'alai' is the default index's name; replace the default index with"):
            load(tmp_path, environ, Overrides(index=["alai=https://x/"]))

    @pytest.mark.parametrize("value", ["", "https://no-name/", "Bad=https://x/", "x=ftp://x/"])
    def test_invalid_index_arg(self, tmp_path: Path, environ: dict[str, str], value: str) -> None:
        with pytest.raises(ConfigError, match="--index"):
            load(tmp_path, environ, Overrides(index=[value]))


def test_every_setting_has_a_settings_attribute() -> None:
    # Settings is written out by hand for its types, so this catches one added to SETTINGS but not to Settings.
    assert all(hasattr(Settings, s.target) or s.target in Settings.__slots__ for s in SETTINGS)


def test_setting_names_unique() -> None:
    names = [s.name for s in SETTINGS]
    assert len(names) == len(set(names))


def test_overrides_unknown_setting() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'link_mod'"):
        Overrides(link_mod="copy")


def test_sources_and_overridden(tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
    project = write(tmp_path / "alai.toml", 'link_mode = "copy"\n')
    user = write(home / ".config" / "alai" / "alai.toml", 'link_mode = "hardlink"\ncache_dir = "/user"\n')
    environ["ALAI_LINK_MODE"] = "symlink"

    settings = load(tmp_path, environ, Overrides(link_mode="clone"))

    assert settings.sources == {
        "collections_dir": "default",
        "cache_dir": str(user),
        "link_mode": "--link-mode",
        "system_certs": "default",
        "offline": "default",
    }
    assert settings.overridden == {
        "link_mode": (
            (LinkMode.SYMLINK, "ALAI_LINK_MODE"),
            (LinkMode.COPY, str(project)),
            (LinkMode.HARDLINK, str(user)),
        ),
    }


def test_searched(tmp_path: Path, home: Path, environ: dict[str, str]) -> None:
    project = write(tmp_path / "alai.toml")
    xdg = tmp_path / "xdg"
    environ["XDG_CONFIG_DIRS"] = f"{xdg / 'a'}:{xdg / 'b'}"
    system = write(xdg / "b" / "alai" / "alai.toml")

    settings = load(tmp_path, environ)

    assert settings.searched == (
        ConfigFile(Level.PROJECT, str(project), read=True),
        ConfigFile(Level.USER, str(home / ".config" / "alai" / "alai.toml"), read=False),
        ConfigFile(Level.SYSTEM, str(xdg / "a" / "alai" / "alai.toml"), read=False),
        # The first system file found is read and the search stops, so /etc/alai/alai.toml isn't looked for.
        ConfigFile(Level.SYSTEM, str(system), read=True),
    )
    assert settings.config_files == (str(project), str(system))


def test_cache_dir_default_needs_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]) -> None:
    monkeypatch.setattr(_config, "_home", lambda _environ: None)

    # Loading still works; only using the cache directory fails.
    settings = load(tmp_path, environ)

    with pytest.raises(ConfigError, match="cannot find the home directory for the cache, set ALAI_CACHE_DIR"):
        _ = settings.cache_dir
    environ["ALAI_CACHE_DIR"] = "/cache"
    assert load(tmp_path, environ).cache_dir == "/cache"
