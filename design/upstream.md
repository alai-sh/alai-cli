# Upstream changes

Changes alai needs, or would benefit from, in other projects, mostly
ansible-test for [collection projects](collection-development.md). Each
says where the change goes, why alai needs it, what alai does until a
release has it, and what to remove from alai once one does. Update the
status here as each is proposed, merged and released, and the note that
depends on it.

A reproduction for the first two, with the collections, a script running
every ansible-test mode in three layouts, and a patch, was built on
2026-10-21; it isn't kept in this repo.

| # | Change | Project | Status |
|---|---|---|---|
| 1 | [`units` passes absolute test paths](#1-units-passes-absolute-test-paths) | ansible-test | Not proposed |
| 2 | [The origin's working directory is the content root](#2-the-origins-working-directory-is-the-content-root) | ansible-test | Not proposed |
| 3 | [Document `ANSIBLE_TEST_CONTENT_ROOT`](#3-document-ansible_test_content_root) | ansible-test | Not proposed |
| 4 | [Map a content root reached through a link to its real path](#4-map-a-content-root-reached-through-a-link-to-its-real-path) | ansible-test | Not proposed |
| 5 | [Record a module's real source path for debuggers](#5-record-a-modules-real-source-path-for-debuggers) | ansible-core | Not proposed; an alternative to 4 for modules |

## 1. `units` passes absolute test paths

- **Where:** `commands/units/__init__.py`: `cmd.extend(paths)` passes paths
  relative to the working directory.
- **Why:** after `os.chdir(content.root)`, the working directory is the
  checkout's real path, so pytest resolves the test files there and
  ansible-test's pytest plugin rejects them as outside
  `ANSIBLE_COLLECTIONS_PATH`. `ansible-test units --local` fails in every
  collection project.
- **Fix:** join each path to `data_context().content.root`.
- **alai until then:** nothing; the docs say to use `--docker` for unit
  tests ([collection-development.md](collection-development.md#why-local-unit-tests-fail)).
- **Remove when released:** the warning in `docs/running-ansible.md`, or
  name the release in it.

## 2. The origin's working directory is the content root

- **Where:** `host_profiles.py`: `OriginProfile.get_working_directory`
  returns `os.getcwd()`.
- **Why:** delegation passes it to the child ansible-test as
  `ANSIBLE_TEST_CONTENT_ROOT`; through a link it's the real path, which
  has no `ansible_collections` in it, so every `--venv` command fails.
- **Fix:** return `data_context().content.root`.
- **alai until then:** nothing; as for 1.
- **Remove when released:** as for 1.

## 3. Document `ANSIBLE_TEST_CONTENT_ROOT`

- **Where:** ansible-test's documentation.
- **Why:** alai points ansible-test at the tree with it, but it's
  internal: delegation sets it for the child process.
- **alai until then:** relies on it anyway.

## 4. Map a content root reached through a link to its real path

- **Where:** `host_profiles.py`: `DebuggableProfile.get_source_mapping`.
- **Why:** the debugger needs paths the editor knows mapped to the paths
  Ansible loads. The editor knows the checkout; Ansible loads the
  collection through the tree's link. ansible-test only maps the
  collection root to itself, then drops that entry, so modules' breakpoints
  set in the checkout never hit, and tracebacks show the tree's paths.
- **Fix:** when the content root differs from its `os.path.realpath()`,
  add `{realpath + "/": content_root + "/"}`. Under delegation, the
  content root's path on the origin is what has to be resolved, before it's
  mapped to the remote's.
- **alai until then:** `alai run` and `alai env` add the same entry to
  `_ANSIBLE_ANSIBALLZ_DEBUGPY_CONFIG` and `_ANSIBLE_ANSIBALLZ_PYDEVD_CONFIG`
  when they're set (`_map_module_sources` in `_environ.py`), for local
  runs only (decided 2026-10-22: kept while this is pursued upstream, as
  it fixes what it can). Best effort: those are ansible-core's internal
  settings. It can't help:
  - `ansible-test integration`, which passes the settings as inventory
    variables rather than through the environment;
  - delegated runs (`--docker`, `--remote`, `--venv`). The ansible-test
    started on the origin works out the mappings, with the tree's paths
    rather than the checkout's, and the commands then run where alai isn't,
    so nothing of alai's can correct them; rewriting the keys of an
    existing mapping was considered for that and dropped for this reason.
    Only this fix makes modules debuggable there.

  `docs/editors.md` documents the limit, per ansible-test command:
  module breakpoints don't hit in `ansible-test integration` or delegated
  runs (seen for `integration`, 2026-10-22). Setting the breakpoint in
  the file opened through the collections directory's path should match
  the recorded path, but isn't documented as a workaround, as it hasn't
  been shown to work. Controller code run on this machine is unaffected, as
  pydevd matches its breakpoints on real paths there. Delegated
  (`--docker`, `--remote`), the controller's code runs on the other host
  too, and ansible-test's mapping from this machine's paths to that host's
  starts from the tree's link, so controller breakpoints set in the
  checkout miss there as well (from reading `get_source_mapping`; not
  tried).
- **Remove when released:** `_map_module_sources`, its tests, and the
  `alai run` note in `docs/editors.md`, for releases with the fix.

## 5. Record a module's real source path for debuggers

- **Where:** ansible-core, `executor/module_common.py`: the AnsiballZ
  source mapping is keyed by the path the plugin loader found the module
  at.
- **Why:** the same as 4, from ansible-core's side: the module process's
  debugger matches breakpoints by prefix, not by real path, unlike the
  controller's. Keying the mapping by the `realpath` as well would make
  modules loaded through any link debuggable, without ansible-test.
- **alai until then:** as for 4.
