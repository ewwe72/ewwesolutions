"""PDF rasterization for vision LLM input."""

from __future__ import annotations

from pathlib import Path

import pymupdf

MAX_PAGES = 10
MAX_WIDTH = 1024
MAX_HEIGHT = 1448


def pdf_to_png_bytes(pdf_path: Path, max_pages: int = MAX_PAGES) -> list[bytes]:
    """Render PDF pages to PNG bytes, downscaled to fit ≤1024×1448.

    Raises ValueError when the document exceeds max_pages so the operator
    is forced to split it (rather than silently truncating and missing
    line items on later pages).
    """
    images: list[bytes] = []
    with pymupdf.open(pdf_path) as doc:
        if doc.page_count > max_pages:
            raise ValueError(
                f"{pdf_path.name}: {doc.page_count} pages > max {max_pages}"
            )
        for page in doc:
            rect = page.rect
            scale = min(MAX_WIDTH / rect.width, MAX_HEIGHT / rect.height)
            matrix = pymupdf.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            images.append(pix.tobytes("png"))
    return images
