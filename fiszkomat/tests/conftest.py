"""Pytest config for fiszkomat unit tests.

Ensures `src/` is on `sys.path` so `from fiszkomat...` imports resolve when
pytest is invoked from the project root, matching the README convention
(`pytest tests/` from inside `fiszkomat/`).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
