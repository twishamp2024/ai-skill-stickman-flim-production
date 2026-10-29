#!/usr/bin/env python3
"""Kage stickman film production tools. Run `kage.py --help`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kagelib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
