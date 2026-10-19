# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""One module per top-level command, each with a ``register`` function that adds its parser.

Every command is registered on every run, so these modules import only what building the parser needs. A handler
imports anything else it uses when it runs.
"""
