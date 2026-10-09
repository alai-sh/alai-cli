import sys

from . import __version__


def main() -> int:
    print(f"alai {__version__}: a package manager for Ansible collections (in development)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
