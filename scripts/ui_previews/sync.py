"""Copy fresh screenshots into the docs, keeping the old file where only run ids and times changed.

Every demo run has new run ids and timestamps, so every screenshot differs a little. Copying them all would make
each regeneration rewrite every image; keeping the old file when under ALC_PREVIEW_THRESHOLD (default 0.05%) of its
pixels changed leaves only the screens whose layout or content really changed in the diff.

    python sync.py NEW_DIR DOCS_DIR
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops


def changed_share(a: Path, b: Path) -> float:
    with Image.open(a) as x, Image.open(b) as y:
        if x.size != y.size:
            return 1.0
        diff = ImageChops.difference(x.convert("RGB"), y.convert("RGB")).convert("L").point(lambda v: 255 if v > 16 else 0)
        return sum(diff.histogram()[255:]) / (x.width * x.height)


def main(new: Path, docs: Path) -> None:
    limit = float(os.environ.get("ALC_PREVIEW_THRESHOLD", "0.0005"))
    docs.mkdir(parents=True, exist_ok=True)
    fresh = {p.name: p for p in sorted(new.glob("*.png"))}
    counts = {"added": 0, "updated": 0, "kept": 0, "removed": 0}
    top = 0.0
    for name, src in fresh.items():
        dst = docs / name
        if not dst.exists():
            counts["added"] += 1
        elif (share := changed_share(src, dst)) < limit:
            counts["kept"] += 1
            top = max(top, share)
            continue
        else:
            counts["updated"] += 1
            print(f"  updated {name} ({share:.1%} of pixels changed)")
        shutil.copyfile(src, dst)
    for old in docs.glob("*.png"):
        if old.name not in fresh:
            old.unlink()
            counts["removed"] += 1
    print(", ".join(f"{v} {k}" for k, v in counts.items()), f"(kept files differed by at most {top:.2%})")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
