"""`python -m worthwatch` runs the command line."""
import sys

from .core import StateFileDamaged, main

if __name__ == "__main__":
    try:
        main()
    except StateFileDamaged as e:
        print(f"\nSTOPPED: {e}")
        sys.exit(2)
