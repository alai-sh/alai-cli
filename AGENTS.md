# Agent notes

Rules the code and tooling don't show. Keep this short: link to `design/` for
reasons, and drop anything a tool now enforces.

- Modules in `src/alai/` are private (`_name.py`) until a public Python API is
  designed. Only `__init__.py` and `__main__.py` are public.
- Every `.py` file, in `src/` and `tests/`, starts with the two-line
  copyright/MIT header; copy it from an existing file.
- `docs/` is the published user documentation and describes current behaviour
  only. Keep it to plain Markdown plus admonitions.
- Plans, rationale and open questions go in `design/` (see
  `design/README.md`). Update the relevant note in the same change as the
  code; turn resolved questions and finished plans into decisions.
- Checks:
  - `uv run tox run -f sanity` (ruff and basedpyright)
  - `uv run tox run -f py314` (tests; any of `py310` to `py315`)
  - `uv run --only-group docs zensical build --strict`
