# Documentation design

## Decisions

- **`docs/` describes only what's implemented**, so users never read about a
  feature that doesn't exist yet. Plans and rationale live here in `design/`,
  which isn't published.
- **Zensical over Sphinx.** There's no public Python API to autodoc, and
  Zensical needs little maintenance.
- **Plain Markdown plus admonitions**, so the docs could move to another
  generator without a rewrite.
- **Deployed to GitHub Pages on every push to `main`** that touches the docs
  (`.github/workflows/docs.yml`), not tied to a release. This is fine while
  alai is pre-release. At the first real release, decide whether docs follow
  `main`, follow releases, or are versioned.
