"""The Hugging Face upload folder for one release, without copying data: tier A's files hard-linked at the root, tier
B's under tier-b/, the dataset page (docs/hf-card.md) as README.md, the licence note as LICENSE.md, and MEMBERS.sha256
listing every file by its path in the folder (the dataset page's check: sha256sum -c MEMBERS.sha256). Refuses a
folder that exists, a dataset page with a placeholder left in it, or a path that would collide. Prints counts only.

python hf_layout.py RELEASE_DIR OUT_DIR --card docs/hf-card.md --licence LICENSE_NOTE.md [--procs 16]
(RELEASE_DIR holds a-names/ and b/)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PLACEHOLDER = re.compile(r"\b(REPO_ID|NWITHTYPED|AGREE_SWE|AGREE_OURS|SECOND_SCAN|REMOVAL|CITATION)\b")


def digest(path: str) -> str:
    d = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            d.update(block)
    return d.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("release", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--card", type=Path, required=True)
    ap.add_argument("--licence", type=Path, required=True)
    ap.add_argument("--procs", type=int, default=16)
    a = ap.parse_args()
    if a.out.exists():
        sys.exit(f"refused: {a.out} exists")
    card = a.card.read_text(encoding="utf-8")
    left = sorted(set(PLACEHOLDER.findall(card)))
    if left:
        sys.exit(f"refused: the dataset page still holds {left}")
    plan: dict[str, Path] = {}
    for tier, prefix in (("a-names", ""), ("b", "tier-b/")):
        root = a.release / tier
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = prefix + str(p.relative_to(root))
                if rel in plan or rel in ("README.md", "LICENSE.md", "MEMBERS.sha256"):
                    sys.exit(f"refused: {rel} twice")
                plan[rel] = p
    for rel, src in plan.items():
        dst = a.out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.link(src, dst)
    (a.out / "README.md").write_text(card, encoding="utf-8")
    (a.out / "LICENSE.md").write_text(a.licence.read_text(encoding="utf-8"), encoding="utf-8")
    names = sorted(plan) + ["LICENSE.md", "README.md"]
    with ProcessPoolExecutor(a.procs) as ex:
        sums = list(ex.map(digest, [str(a.out / n) for n in names], chunksize=8))
    (a.out / "MEMBERS.sha256").write_text("".join(f"{h}  {n}\n" for h, n in zip(sums, names)))
    size = sum((a.out / n).stat().st_size for n in names)
    print(f"{a.out}: {len(names):,} files ({sum(1 for n in names if n.startswith('tier-b/')):,} in tier-b/), "
          f"{size / 1e9:.1f} GB, all linked, none copied; MEMBERS.sha256 lists every one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
