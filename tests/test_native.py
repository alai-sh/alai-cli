# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The native extension as a whole, rather than what it does, which the tests of each caller cover."""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from alai import _native


def test_one_build_beside_it() -> None:
    # An editable install (uv sync) builds the module into src/alai/, named for its ABI, so switching .venv's Python
    # leaves the other build there; 3.15 loads an old _native.abi3.so before a new _native.abi3t.so. A wheel has one.
    builds = sorted(p.name for p in Path(_native.__file__).parent.glob("_native*.so"))

    assert builds == [Path(_native.__file__).name], f"delete the stale builds of alai._native in src/alai/: {builds}"


@pytest.mark.skipif(not sysconfig.get_config_var("Py_GIL_DISABLED"), reason="a free-threaded build of Python only")
def test_leaves_the_gil_off() -> None:
    # Python turns the GIL back on, with a warning, when it imports an extension that doesn't declare itself safe
    # without it; the module declares gil_used = false. A new interpreter, so nothing imported earlier hides it.
    env = {k: v for k, v in os.environ.items() if k != "PYTHON_GIL"}
    code = "import sys, alai._native; print(sys._is_gil_enabled())"

    done = subprocess.run(  # noqa: S603
        [sys.executable, "-W", "error::RuntimeWarning", "-c", code], capture_output=True, text=True, check=True, env=env
    )

    assert done.stdout.strip() == "False"
