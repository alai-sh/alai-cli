# Agent notes

Rules the code and tooling don't show. Keep this short: link to `design/` for
reasons, and drop anything a tool now enforces.

- Modules in `src/alai/` are private (`_name.py`) until a public Python API is
  designed. Only `__init__.py` and `__main__.py` are public.
- The Rust in `src/rust/` is built into the package as `alai._native` by
  maturin, typed by `src/alai/_native.pyi`; keep the stub in step with it.
  It's required, with no Python fallback, so building alai, the tests and
  the docs needs `cargo`. alai's version is in the root `Cargo.toml`
  (`[workspace.package]`), which `pyproject.toml` takes through maturin.
  `uv sync` builds it into `src/alai/`, named for the Python's ABI, so
  after switching `.venv` to another Python, delete `src/alai/_native*.so`:
  3.15 loads an old `.abi3.so` before a new `.abi3t.so`
  (`tests/test_native.py` fails if more than one is there).
- Every `.py`, `.pyi` and `.rs` file starts with the two-line copyright/MIT
  header; copy it from an existing file.
- What users read is US English: `docs/`, `README.md`, and the CLI's help
  and output. `design/` and code comments can stay as written.
- `docs/` is the published user documentation and describes current behaviour
  only, except `docs/roadmap.md`, which summarises what's planned: keep it in
  step with the roadmap in `design/README.md`. Keep it to plain Markdown plus
  admonitions.
- Plans, rationale and open questions go in `design/` (see
  `design/README.md`). Update the relevant note in the same change as the
  code; turn resolved questions and finished plans into decisions. A
  decision users may want the other way also goes in
  `design/user-questions.md`, with what to ask them.
- Follow uv: before designing a command or behaviour, try what uv does in
  a scratch project and match its defaults, flags and output; for Ansible's
  side, read ansible-core's source. Record what was tested in the design
  note, and why whenever alai differs (see `design/README.md`).
- Nothing is released, so there are no backwards-compatibility shims or
  aliases: rename and replace outright.
- Checks:
  - `uv run tox run -f sanity` (ruff, basedpyright, `cargo fmt` and clippy)
  - `uv run tox run -f py314` (tests; any of `py310` to `py315`, and the
    free-threaded `py314t` and `py315t`)
  - `uv run tox run -e docs` (the CLI reference against the parser, the
    terminal recordings, the strict docs build, then every link between
    the pages, anchors included, which the build doesn't check)
