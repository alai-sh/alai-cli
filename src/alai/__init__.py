# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)


def __getattr__(name: str) -> str:
    # Looked up on first use: importlib.metadata costs ~25 ms to import, which every command would pay otherwise.
    if name == "__version__":
        from importlib.metadata import version

        value = version("alai")
        globals()["__version__"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
