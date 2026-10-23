# Acknowledgements

alai borrows ideas from a number of projects, and is built on others. If
alai is useful to you, these may be too.

## Inspiration

- **[uv](https://docs.astral.sh/uv/)**, Astral's package and project
  manager for Python, is the model for alai. Its commands, flags, defaults
  and output are where alai starts: `alai add`, `alai lock` and
  `alai sync` are meant to feel like their uv counterparts, and alai only
  differs where Ansible collections need something else.
- **[ModuleFast](https://github.com/JustinGrote/ModuleFast)**, by Justin
  Grote, does for PowerShell modules and the
  [PowerShell Gallery](https://www.powershellgallery.com/) what alai does
  for Ansible collections and Galaxy: a fast, declarative way to install
  modules, made with CI in mind.
- **[Cargo](https://doc.rust-lang.org/cargo/)**, Rust's package manager,
  and its
  [registry index](https://doc.rust-lang.org/cargo/reference/registry-index.html)
  shaped alai's index format. Like Cargo's sparse index, alai's is a set of
  static files, one per collection with a line per version, that any web
  server or CDN can serve, so resolving needs no API.
- **[PwshSpectreConsole](https://pwshspectreconsole.com/)**, by Shaun
  Lawrie, inspired these docs. Its examples are run and recorded, so the
  documentation shows what the commands really print; the recordings in the
  [CLI reference](cli.md) do the same for alai.

## Built with

- **[Ansible Galaxy](https://galaxy.ansible.com/)**, where the collections
  come from: alai's default index is a mirror of it. alai installs a
  collection the way `ansible-galaxy` does, so the result is the same
  either way.
- **[PubGrub](https://github.com/pubgrub-rs/pubgrub)**, the version solving
  algorithm uv and Cargo use, chooses the versions and explains why there's
  no choice when there isn't.
- **[PyYAML](https://pyyaml.org/)** reads a collection's `galaxy.yml` and
  `meta/runtime.yml`, with the same parser `ansible-core` uses.
- **[distlib](https://github.com/pypa/distlib)** reads the `manifest`
  directives in `galaxy.yml` when a collection is installed from its
  directory, as `ansible-galaxy` does.
- **[urllib3](https://urllib3.readthedocs.io/)** and
  **[truststore](https://truststore.readthedocs.io/)** download from the
  index, verified against the operating system's certificates.
- **[argcomplete](https://kislyuk.github.io/argcomplete/)** provides
  [shell completion](shell-completion.md).
- **[Zensical](https://zensical.org/)** builds these docs, and
  **[asciinema-player](https://docs.asciinema.org/manual/player/)** plays
  their recordings.
