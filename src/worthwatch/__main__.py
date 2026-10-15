"""`python -m worthwatch` and the `worthwatch` command run the command line."""
import sys

from .core import StateFileDamaged, main


def run():
    try:
        main()
    except StateFileDamaged as e:
        print(f"\nSTOPPED: {e}")
        sys.exit(2)


if __name__ == "__main__":
    run()
