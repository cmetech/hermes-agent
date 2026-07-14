"""outlook-cli: thin CLI entry point."""
import sys
from outlook_cli import run


def main():
    print(run(*sys.argv[1:]))


if __name__ == "__main__":
    main()
