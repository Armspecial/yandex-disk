"""Точка входа: python -m ydx."""

import sys

from ydx.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
