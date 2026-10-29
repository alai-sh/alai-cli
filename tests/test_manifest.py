# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest
from alai_core.pep440 import Version as AnsibleVersion
from alai_core.semver import Version, VersionReq
from helpers import write_file

from alai._common import ConfigError, read_toml
from alai._config import LinkMode, Overrides, Project, find_project, load_settings, raw_manifest
from alai._manifest import IndexPin, Manifest, Requirement, collection_name, parse_manifest


def load_manifest(project: Project) -> Manifest:
    assert project.config_file is not None
    return parse_manifest(raw_manifest(read_toml(project.config_file), project.config_file))


def manifest(cwd: Path) -> Manifest:
    return load_manifest(find_project(str(cwd)))


class TestRequirement:
    @pytest.mark.parametrize(
        ("text", "name", "clauses"),
        [
            ("ansible.windows", "ansible.windows", ()),
            ("community.general>=9,<11", "community.general", ((">=", "9.0.0"), ("<", "11.0.0"))),
            ("community.general >= 9", "community.general", ((">=", "9.0.0"),)),
            (" corp.tools ==1.0.0 ", "corp.tools", (("==", "1.0.0"),)),
            ("ns._private_1*", "ns._private_1", ()),
            ("ns.name!=1.0.0", "ns.name", (("!=", "1.0.0"),)),
        ],
    )
    def test_parse(self, text: str, name: str, clauses: tuple[tuple[str, str], ...]) -> None:
        requirement = Requirement.parse(text)

        assert requirement.name == name
        assert [(op, v.text) for op, v in requirement.req.clauses] == list(clauses)
        assert str(requirement) == text

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("", "expected a collection name"),
            (">=1.0", "expected a collection name"),
            ("general", "expected namespace.name"),
            ("a.b.c", "expected namespace.name"),
            ("Community.General", "collection names are lowercase"),
            ("community.class", "'class' must be a Python identifier that isn't a keyword"),
            ("community.1abc", "'1abc' must be a Python identifier"),
            ("community-x.general", "'community-x' must be a Python identifier"),
            ("internal:corp.tools>=1.2", "'internal:corp' must be a Python identifier"),
            ("ns.name 1.0.0", "needs an operator"),
            ("ns.name^1.0.0", "ranges aren't supported"),
            ("ns.name>=1,", "empty clause"),
        ],
    )
    def test_invalid(self, text: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            Requirement.parse(text)

    def test_collection_name(self) -> None:
        assert collection_name("community.general") == "community.general"


class TestManifest:
    def test_full(self, tmp_path: Path) -> None:
        path = write_file(
            tmp_path / "alai.toml",
            """\
[project]
collections_dir = "collections"
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "corp.tools>=1.2",
]

[project.groups.test]
collections = ["ansible.posix", "community.crypto"]

[project.groups.lint-only]

[project.overrides]
"ansible.netcommon" = ">=6"
"community.crypto" = "*"
ignore_requires_ansible = ["some.collection"]

[project.sources]
"corp.tools" = { index = "internal" }
"ansible.netcommon" = { index = "internal" }
""",
        )

        result = manifest(tmp_path)

        assert result.path == str(path)
        assert [r.text for r in result.collections] == [
            "community.general>=9,<11",
            "ansible.windows",
            "corp.tools>=1.2",
        ]
        # A source can name a collection the project doesn't list, here a dependency of corp.tools.
        assert result.sources == {"corp.tools": IndexPin("internal"), "ansible.netcommon": IndexPin("internal")}
        assert result.requires_ansible is not None
        assert result.requires_ansible.contains(AnsibleVersion("2.18.1"))
        assert not result.requires_ansible.contains(AnsibleVersion("2.16.0"))
        assert {name: [r.name for r in reqs] for name, reqs in result.groups.items()} == {
            "test": ["ansible.posix", "community.crypto"],
            "lint-only": [],
        }
        assert result.overrides == {
            "ansible.netcommon": VersionReq(">=6"),
            "community.crypto": VersionReq("*"),
        }
        assert result.ignore_requires_ansible == ("some.collection",)
        assert result.overrides["ansible.netcommon"].contains(Version("6.0.0"))

    def test_empty(self, tmp_path: Path) -> None:
        path = write_file(tmp_path / "alai.toml", 'link_mode = "copy"\n')

        assert manifest(tmp_path) == Manifest(str(path))

    def test_ignore_requires_ansible_deduplicated(self, tmp_path: Path) -> None:
        write_file(tmp_path / "alai.toml", '[project.overrides]\nignore_requires_ansible = ["a.b", "c.d", "a.b"]\n')

        assert manifest(tmp_path).ignore_requires_ansible == ("a.b", "c.d")


class TestTypes:
    """Every command checks the manifest's keys and types, so these fail on loading settings as well."""

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('project = "x"', r"'project' must be a table \(\[project\]\)"),
            ('[project]\ncollections = "ns.name"', "'project.collections' must be an array of requirement strings"),
            ("[project]\ncollections = [1]", "'project.collections' must be an array of requirement strings"),
            ("[project]\nrequires_ansible = 2.17", "'project.requires_ansible' must be a string"),
            ('[project]\ngroups = "x"', r"'project\.groups' must be a table \(\[project\.groups\.<name>\]\)"),
            ('[project.groups]\ntest = "x"', r"'project\.groups\.test' must be a table \(\[project\.groups\.test\]\)"),
            ('[project.groups.test]\ncollection = ["a.b"]', "unknown key 'project.groups.test.collection'"),
            ('[project]\noverrides = "x"', r"'project\.overrides' must be a table \(\[project\.overrides\]\)"),
            ('[project.overrides]\n"a.b" = 1', r'project\.overrides\."a\.b" must be a version requirement string'),
            ('[project.overrides]\nignore_requires_ansible = "a.b"', "must be an array of collection names"),
        ],
    )
    def test_invalid(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "alai.toml", content)

        for load in (lambda: load_settings(cwd=tmp_path, environ=environ), lambda: manifest(tmp_path)):
            with pytest.raises(ConfigError, match=match) as e:
                load()
            assert str(e.value).startswith(str(tmp_path / "alai.toml"))

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            (
                '[project]\ncollection = ["a.b"]',
                "unknown setting 'project.collection', did you mean 'project.collections'",
            ),
            (
                '[project.group.test]\ncollections = ["a.b"]',
                "unknown setting 'project.group', did you mean 'project.groups'",
            ),
        ],
    )
    def test_suggests_manifest_keys(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "alai.toml", content)

        with pytest.raises(ConfigError, match=match):
            load_settings(cwd=tmp_path, environ=environ)


class TestValues:
    """What the values mean is only checked when a command reads the manifest."""

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('[project]\ncollections = ["ns"]', r"project\.collections\[0\]: invalid collection name 'ns'"),
            (
                '[project]\ncollections = ["a.b>=1", "c.d", "a.b<2"]',
                r"project\.collections\[2\]: 'a\.b' is already listed at project\.collections\[0\]",
            ),
            ('[project]\nrequires_ansible = ","', "'project.requires_ansible' is empty"),
            (
                '[project]\nrequires_ansible = "2.17"',
                "project.requires_ansible: invalid specifier '2.17', expected PEP",
            ),
            ("[project.groups.Test]", "invalid group name 'Test'"),
            (
                '[project.groups.test]\ncollections = ["a.b", "a.b"]',
                r"project\.groups\.test\.collections\[1\]: 'a\.b' is already",
            ),
            ('[project.overrides]\n"ansible" = ">=1"', r'project\.overrides\."ansible": invalid collection name'),
            ('[project.overrides]\n"a.b" = "1.0"', r'project\.overrides\."a\.b": .*needs an operator'),
            ('[project.overrides]\nignore_requires_ansible = ["A.b"]', r"ignore_requires_ansible\[0\]: .*lowercase"),
        ],
    )
    def test_invalid(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "alai.toml", content)

        _ = load_settings(cwd=tmp_path, environ=environ)
        with pytest.raises(ConfigError, match=match) as e:
            _ = manifest(tmp_path)
        assert str(e.value).startswith(str(tmp_path / "alai.toml"))


class TestManifestAndConfig:
    """The manifest comes from the project's alai.toml whichever config files are read, and only from there."""

    def test_no_config(self, tmp_path: Path, environ: dict[str, str]) -> None:
        # --no-config doesn't read the project file, so even a wrong type there goes unnoticed until it's used.
        write_file(tmp_path / "alai.toml", 'link_mode = "copy"\n[project]\ncollections = "a.b"\n')

        settings = load_settings(Overrides(no_config=True), cwd=tmp_path, environ=environ)

        assert settings.link_mode == LinkMode.AUTO
        assert settings.manifest is None
        with pytest.raises(ConfigError, match="must be an array"):
            _ = load_manifest(settings.project)

    def test_config_file_elsewhere(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write_file(tmp_path / "alai.toml", '[project]\ncollections = ["a.b"]\n')
        write_file(tmp_path / "ci.toml", 'link_mode = "copy"\n')

        settings = load_settings(Overrides(config_file="ci.toml"), cwd=tmp_path, environ=environ)

        assert settings.link_mode == LinkMode.COPY
        # Not read as a config file, so load_project reads it then.
        assert settings.manifest is None
        from alai._project import load_project

        _, result = load_project(settings, "")
        assert [r.name for r in result.collections] == ["a.b"]

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('[project]\ncollections = ["a.b"]', r"\[project\] is only allowed in the project's alai\.toml"),
            ("[project.groups.test]", r"\[project\] is only allowed in the project's alai\.toml"),
            ('[project.overrides]\n"a.b" = ">=1"', r"\[project\] is only allowed in the project's alai\.toml"),
        ],
    )
    def test_not_in_config_file(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "ci.toml", content)

        with pytest.raises(ConfigError, match=match) as e:
            load_settings(Overrides(config_file="ci.toml"), cwd=tmp_path, environ=environ)
        assert str(e.value).startswith(str(tmp_path / "ci.toml"))

    @pytest.mark.parametrize("table", ["[project.groups.test]", '[project.overrides]\n"a.b" = ">=1"'])
    def test_not_in_user_file(self, tmp_path: Path, environ: dict[str, str], table: str) -> None:
        write_file(Path(environ["HOME"]) / ".config" / "alai" / "alai.toml", table)

        with pytest.raises(ConfigError, match=r"is only allowed in the project's alai\.toml"):
            load_settings(cwd=tmp_path, environ=environ)


class TestProjectSubTables:
    """Everything that describes the project is under [project]."""

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('[groups.test]\ncollections = ["a.b"]', r"unknown setting 'groups', did you mean 'project\.groups'\?"),
            ('[overrides]\n"a.b" = ">=1"', r"unknown setting 'overrides', did you mean 'project\.overrides'\?"),
            ('[sources]\n"a.b" = { index = "x" }', r"unknown setting 'sources', did you mean 'project\.sources'\?"),
        ],
    )
    def test_top_level_is_unknown(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "alai.toml", content)

        with pytest.raises(ConfigError, match=match):
            load_settings(cwd=tmp_path, environ=environ)

    def test_sub_tables_without_project_header(self, tmp_path: Path) -> None:
        # TOML defines [project] implicitly from its sub-tables.
        write_file(
            tmp_path / "alai.toml", '[project.groups.test]\ncollections = ["a.b"]\n[project.overrides]\n"c.d" = "*"\n'
        )

        result = manifest(tmp_path)

        assert [r.name for r in result.groups["test"]] == ["a.b"]
        assert list(result.overrides) == ["c.d"]


class TestSources:
    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('[project]\nsources = "x"', r"'project\.sources' must be a table \(\[project\.sources\]\)"),
            ('[project.sources]\n"a.b" = "internal"', r'project\.sources\."a\.b" must be a table, such as'),
        ],
    )
    def test_types(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "alai.toml", content)

        for load in (lambda: load_settings(cwd=tmp_path, environ=environ), lambda: manifest(tmp_path)):
            with pytest.raises(ConfigError, match=match):
                load()

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('[project.sources]\n"ansible" = { index = "x" }', r'project\.sources\."ansible": invalid collection name'),
            ('[project.sources]\n"a.b" = { index = "Bad Name" }', "'Bad Name' isn't a valid index name"),
            ('[project.sources]\n"a.b" = { index = 1 }', r'an index source is \{ index = "<name>" \} alone, got index'),
            ('[project.sources]\n"a.b" = { svn = "https://x" }', r'or \{ git = "<url>" \}, got svn'),
            ('[project.sources]\n"a.b" = { git = "" }', r'a git source is \{ git = "<url>" \}'),
            ('[project.sources]\n"a.b" = { git = "https://x", tag = "v1" }', "got git, tag"),
            (
                '[project.sources]\n"a.b" = { git = "git@github.com:org/repo.git" }',
                "is git's scp-like form, which isn't a URL: write it as ssh://git@github.com/org/repo.git",
            ),
            ('[project.sources]\n"a.b" = { git = "/srv/repo" }', "'/srv/repo' isn't a URL"),
            ('[project.sources]\n"a.b" = { git = "https://x", rev = "" }', "'rev' must be a non-empty string"),
            (
                '[project.sources]\n"a.b" = { git = "https://x", subdirectory = 1 }',
                "'subdirectory' must be a non-empty",
            ),
            (
                '[project.sources]\n"a.b" = { git = "https://x", subdirectory = "../up" }',
                "subdirectory '../up' must be a relative path inside the repository",
            ),
            ('[project.sources]\n"a.b" = { git = "https://x", subdirectory = "/abs" }', "must be a relative path"),
            ('[project.sources]\n"a.b" = { git = "https://x", version = "1.0" }', r"'version': invalid version"),
            ('[project.sources]\n"a.b" = { path = "p", version = 1 }', "'version' must be a string"),
            ('[project.sources]\n"a.b" = { url = "https://x/a.tar.gz", version = "1.0.0" }', "got url, version"),
            ('[project.sources]\n"a.b" = { index = "x", version = "1.0.0" }', "got index, version"),
            ('[project.sources]\n"a.b" = { path = 1 }', r'a path source is \{ path = "<dir or file>" \}'),
            ('[project.sources]\n"a.b" = { path = "" }', r'a path source is \{ path = "<dir or file>" \}'),
            ('[project.sources]\n"a.b" = { url = 1 }', r'a URL source is \{ url = "<tarball URL>" \} alone, got url'),
            ('[project.sources]\n"a.b" = { url = "https://x/a.tar.gz", editable = true }', "got editable, url"),
            (
                '[project.sources]\n"a.b" = { url = "file:///a.tar.gz" }',
                r"isn't an http or https URL; a tarball on disk",
            ),
            ('[project.sources]\n"a.b" = { url = "a.tar.gz" }', r"isn't an http or https URL"),
            ('[project.sources]\n"a.b" = { path = "p", rev = "x" }', "got path, rev"),
            ('[project.sources]\n"a.b" = { path = "p", editable = "yes" }', "'editable' must be true or false"),
            ('[project.sources]\n"a.b" = { index = "x", path = "p" }', "got index, path"),
            ('[project.sources]\n"a.b" = {}', "got none"),
        ],
    )
    def test_values(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write_file(tmp_path / "alai.toml", content)

        _ = load_settings(cwd=tmp_path, environ=environ)
        with pytest.raises(ConfigError, match=match):
            _ = manifest(tmp_path)
