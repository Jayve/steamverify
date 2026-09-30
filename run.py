#!/usr/bin/env python3
"""Run steamverify straight from a source checkout.

Useful when you do not want to install anything::

    python run.py verify --game witcher3 --dir "S:/SteamLibrary/steamapps/common/The Witcher 3"
    python run.py list

The console script installed by ``pip install .`` is equivalent.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from steamverify.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
