# Indexes

!!! warning "Still in flux"

    The index format, and how alai uses indexes, are still being worked
    out and may change without notice. This page will be filled in once
    they settle. Until then it only covers what you need to use the
    default index.

alai reads which collections exist, their versions and what they depend on
from an index, rather than from the Galaxy API. The default is
`https://index.alai.sh/`, a static mirror of Galaxy with a few changes to
make that metadata quicker to fetch. Collections are downloaded from the
location the index gives, and checked against the checksum it lists, which
`alai.lock` records.

Nothing needs configuring to use it. Other indexes, such as a mirror of
`index.alai.sh`, can be added with [`[[index]]`](alai-toml.md#index) or
`--index`, and a collection tied to one with
[`[project.sources]`](alai-toml.md#projectsources). See
[Configuration](configuration.md#indexes) for more information around how
indexes are defined and ordered.
