#!/usr/bin/env python3
"""Render cv.html to cv.pdf using a headless Chromium via Playwright.

Usage:
    pipx install playwright && playwright install chromium
    python scripts/build_cv.py

Output: writes ./cv.pdf at the repo root, A4, print-css respected,
@page rules honoured. Network access is required the first time so
the Google Fonts (Inter, JetBrains Mono) embed into the PDF.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "cv.html"
PDF = ROOT / "cv.pdf"


def _resolve_playwright_python() -> str:
    """Find a Python interpreter that has the playwright package installed.

    Prefers a pipx-managed playwright venv, falls back to the current
    interpreter if it can import playwright itself.
    """
    pipx_python = Path.home() / ".local/share/pipx/venvs/playwright/bin/python"
    if pipx_python.exists():
        return str(pipx_python)
    try:
        import playwright  # noqa: F401
        return sys.executable
    except ModuleNotFoundError:
        sys.exit(
            "playwright not found. Install with:\n"
            "    pipx install playwright && playwright install chromium"
        )


RENDER_SCRIPT = r"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

html_path = Path(sys.argv[1]).resolve()
pdf_path = Path(sys.argv[2]).resolve()

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(html_path.as_uri(), wait_until="networkidle")
    page.emulate_media(media="print")
    page.pdf(
        path=str(pdf_path),
        format="A4",
        print_background=True,
        prefer_css_page_size=True,
    )
    browser.close()
print(f"wrote {pdf_path}")
"""


def main() -> int:
    if not HTML.exists():
        sys.exit(f"missing {HTML}")
    py = _resolve_playwright_python()
    result = subprocess.run(
        [py, "-c", RENDER_SCRIPT, str(HTML), str(PDF)],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
