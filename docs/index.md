# alai

A fast package and environment manager for Ansible collections, in the
spirit of [uv](https://docs.astral.sh/uv/).

!!! warning "Early development"

    alai isn't usable yet and is a work in progress.

alai installs the collections an Ansible project needs into the project
itself, in `collections/ansible_collections/` next to your playbooks, where
`ansible-playbook` finds them with no extra configuration. Collections come
from the alai index, a static mirror of Galaxy with a few changes to improve
the time it takes to retrieve the collection metadata before downloading.

- [Projects](projects.md): how alai finds your project, declaring the
  collections it needs, adding and removing them, locking them,
  installing them with `alai sync`, and installing collections by hand.
- [Configuration](configuration.md): where alai reads its settings from,
  and how files, environment variables and flags combine.
- [alai.toml](alai-toml.md): every key and table the file can hold.
- [Shell completion](shell-completion.md): Tab completion for alai's commands
  and options.
