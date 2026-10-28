# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""alai's wording for TOML errors. alai-core's own tests cover reading, writing and editing."""

from __future__ import annotations

import typing as _t
from pathlib import Path

import pytest

from alai._common import ConfigError, read_toml, read_toml_document


class TestErrors:
    @pytest.mark.parametrize("read", [read_toml, read_toml_document])
    def test_errors_name_the_file(self, tmp_path: Path, read: _t.Callable[[str], object]) -> None:
        path = tmp_path / "alai.toml"
        _ = path.write_text("a = \n")

        with pytest.raises(ConfigError, match=rf"^{path}: invalid TOML: .* \(at line 1, column 5\)$"):
            _ = read(str(path))
        with pytest.raises(ConfigError, match=rf"^can't read {tmp_path}: Is a directory$"):
            _ = read(str(tmp_path))

    @pytest.mark.parametrize("read", [read_toml, read_toml_document])
    def test_errors_shorten_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: _t.Callable[[str], object]
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        path = tmp_path / "project" / "alai.lock"
        path.parent.mkdir()
        _ = path.write_text("a = \n")

        with pytest.raises(ConfigError, match=r"^~/project/alai\.lock: invalid TOML: "):
            _ = read(str(path))
