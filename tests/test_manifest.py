# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

from pathlib import Path

import pytest

from alai._common import ConfigError
from alai._config import LinkMode, Overrides, find_project, load_settings
from alai._manifest import Manifest, Requirement, collection_name, load_manifest
from alai._pep440 import Version as AnsibleVersion
from alai._semver import Version, VersionReq


@pytest.fixture
def environ(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    xdg_dirs = tmp_path / "xdg"
    xdg_dirs.mkdir()
    return {"HOME": str(home), "XDG_CONFIG_DIRS": str(xdg_dirs)}


def write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def manifest(cwd: Path) -> Manifest:
    result = load_manifest(find_project(str(cwd)))
    assert result is not None
    return result


class TestRequirement:
    @pytest.mark.parametrize(
        ("text", "name", "index", "clauses"),
        [
            ("ansible.windows", "ansible.windows", None, ()),
            ("community.general>=9,<11", "community.general", None, ((">=", "9.0.0"), ("<", "11.0.0"))),
            ("community.general >= 9", "community.general", None, ((">=", "9.0.0"),)),
            ("internal:corp.tools>=1.2", "corp.tools", "internal", ((">=", "1.2.0"),)),
            (" internal : corp.tools ==1.0.0 ", "corp.tools", "internal", (("==", "1.0.0"),)),
            ("ns._private_1*", "ns._private_1", None, ()),
            ("ns.name!=1.0.0", "ns.name", None, (("!=", "1.0.0"),)),
        ],
    )
    def test_parse(self, text: str, name: str, index: str | None, clauses: tuple[tuple[str, str], ...]) -> None:
        requirement = Requirement.parse(text)

        assert requirement.name == name
        assert requirement.index == index
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
            ("Bad:ns.name", "'Bad' isn't a valid index name"),
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
        path = write(
            tmp_path / "alai.toml",
            """\
[project]
collections_dir = "collections"
requires_ansible = ">=2.17"
collections = [
    "community.general>=9,<11",
    "ansible.windows",
    "internal:corp.tools>=1.2",
]

[groups.test]
collections = ["ansible.posix", "community.crypto"]

[groups.lint-only]

[overrides]
"ansible.netcommon" = ">=6"
"community.crypto" = "*"
ignore_requires_ansible = ["some.collection"]
""",
        )

        result = manifest(tmp_path)

        assert result.path == str(path)
        assert [r.text for r in result.collections] == [
            "community.general>=9,<11",
            "ansible.windows",
            "internal:corp.tools>=1.2",
        ]
        assert result.requires_ansible is not None
        assert result.requires_ansible.contains(AnsibleVersion("2.18.1"))
        assert not result.requires_ansible.contains(AnsibleVersion("2.16.0"))
        assert {name: [r.name for r in reqs] for name, reqs in result.groups.items()} == {
            "test": ["ansible.posix", "community.crypto"],
            "lint-only": [],
        }
        assert result.overrides == {
            "ansible.netcommon": VersionReq.parse(">=6"),
            "community.crypto": VersionReq("*", ()),
        }
        assert result.ignore_requires_ansible == ("some.collection",)
        assert result.overrides["ansible.netcommon"].contains(Version("6.0.0"))

    def test_empty(self, tmp_path: Path) -> None:
        path = write(tmp_path / "alai.toml", 'link_mode = "copy"\n')

        assert manifest(tmp_path) == Manifest(str(path))

    def test_no_project_file(self, tmp_path: Path) -> None:
        write(tmp_path / "ansible.cfg")

        assert load_manifest(find_project(str(tmp_path))) is None

    def test_ignore_requires_ansible_deduplicated(self, tmp_path: Path) -> None:
        write(tmp_path / "alai.toml", '[overrides]\nignore_requires_ansible = ["a.b", "c.d", "a.b"]\n')

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
            ('groups = "x"', r"'groups' must be a table \(\[groups\.<name>\]\)"),
            ('[groups]\ntest = "x"', r"'groups\.test' must be a table \(\[groups\.test\]\)"),
            ('[groups.test]\ncollection = ["a.b"]', "unknown key 'groups.test.collection'"),
            ('overrides = "x"', r"'overrides' must be a table \(\[overrides\]\)"),
            ('[overrides]\n"a.b" = 1', r'overrides\."a\.b" must be a version requirement string'),
            ('[overrides]\nignore_requires_ansible = "a.b"', "must be an array of collection names"),
        ],
    )
    def test_invalid(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write(tmp_path / "alai.toml", content)

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
            ('[group.test]\ncollections = ["a.b"]', "unknown setting 'group', did you mean 'groups'"),
        ],
    )
    def test_suggests_manifest_keys(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write(tmp_path / "alai.toml", content)

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
            ("[groups.Test]", "invalid group name 'Test'"),
            ('[groups.test]\ncollections = ["a.b", "a.b"]', r"groups\.test\.collections\[1\]: 'a\.b' is already"),
            ('[overrides]\n"ansible" = ">=1"', r'overrides\."ansible": invalid collection name'),
            ('[overrides]\n"a.b" = "1.0"', r'overrides\."a\.b": .*needs an operator'),
            ('[overrides]\nignore_requires_ansible = ["A.b"]', r"ignore_requires_ansible\[0\]: .*lowercase"),
        ],
    )
    def test_invalid(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write(tmp_path / "alai.toml", content)

        _ = load_settings(cwd=tmp_path, environ=environ)
        with pytest.raises(ConfigError, match=match) as e:
            _ = manifest(tmp_path)
        assert str(e.value).startswith(str(tmp_path / "alai.toml"))


class TestManifestAndConfig:
    """The manifest comes from the project's alai.toml whichever config files are read, and only from there."""

    def test_no_config(self, tmp_path: Path, environ: dict[str, str]) -> None:
        # --no-config doesn't read the project file, so even a wrong type there goes unnoticed until it's used.
        write(tmp_path / "alai.toml", 'link_mode = "copy"\n[project]\ncollections = "a.b"\n')

        settings = load_settings(Overrides(no_config=True), cwd=tmp_path, environ=environ)

        assert settings.link_mode == LinkMode.AUTO
        with pytest.raises(ConfigError, match="must be an array"):
            _ = load_manifest(settings.project)

    def test_config_file_elsewhere(self, tmp_path: Path, environ: dict[str, str]) -> None:
        write(tmp_path / "alai.toml", '[project]\ncollections = ["a.b"]\n')
        write(tmp_path / "ci.toml", 'link_mode = "copy"\n')

        settings = load_settings(Overrides(config_file="ci.toml"), cwd=tmp_path, environ=environ)

        assert settings.link_mode == LinkMode.COPY
        result = load_manifest(settings.project)
        assert result is not None
        assert [r.name for r in result.collections] == ["a.b"]

    @pytest.mark.parametrize(
        ("content", "match"),
        [
            ('[project]\ncollections = ["a.b"]', r"\[project\] is only allowed in the project's alai\.toml"),
            ("[groups.test]", r"\[groups\] is only allowed in the project's alai\.toml"),
            ('[overrides]\n"a.b" = ">=1"', r"\[overrides\] is only allowed in the project's alai\.toml"),
        ],
    )
    def test_not_in_config_file(self, tmp_path: Path, environ: dict[str, str], content: str, match: str) -> None:
        write(tmp_path / "ci.toml", content)

        with pytest.raises(ConfigError, match=match) as e:
            load_settings(Overrides(config_file="ci.toml"), cwd=tmp_path, environ=environ)
        assert str(e.value).startswith(str(tmp_path / "ci.toml"))

    @pytest.mark.parametrize("table", ["[groups.test]", '[overrides]\n"a.b" = ">=1"'])
    def test_not_in_user_file(self, tmp_path: Path, environ: dict[str, str], table: str) -> None:
        write(Path(environ["HOME"]) / ".config" / "alai" / "alai.toml", table)

        with pytest.raises(ConfigError, match=r"is only allowed in the project's alai\.toml"):
            load_settings(cwd=tmp_path, environ=environ)
