"""Dedup eval_set/ by SHA-1 of file content.

Groups files by content hash. For each group with >1 file, keeps the one
with the earliest filename (alphabetic), moves the rest to eval_set/_dupes/.
Lexicographic order tends to prefer original sender (e.g. 'senetic_*')
over self-forward ('gmail_*').
"""

from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval_set"
DUPE_DIR = EVAL_DIR / "_dupes"


def sha1_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    if not EVAL_DIR.exists():
        print(f"ERROR: {EVAL_DIR} does not exist", file=sys.stderr)
        return 1

    DUPE_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(
        p for p in EVAL_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )
    print(f"Hashing {len(pdfs)} files...")

    groups: dict[str, list[Path]] = defaultdict(list)
    for p in pdfs:
        groups[sha1_of(p)].append(p)

    dupes_moved = 0
    groups_collapsed = 0
    for digest, files in groups.items():
        if len(files) < 2:
            continue
        groups_collapsed += 1
        keep, *rest = files  # already sorted
        for d in rest:
            target = DUPE_DIR / d.name
            if target.exists():
                target.unlink()
            d.rename(target)
            dupes_moved += 1

    remaining = sum(1 for _ in EVAL_DIR.glob("*.pdf"))
    print(f"\nCollapsed {groups_collapsed} duplicate groups")
    print(f"Moved {dupes_moved} -> {DUPE_DIR.relative_to(ROOT)}/")
    print(f"Remaining in {EVAL_DIR.relative_to(ROOT)}/: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
