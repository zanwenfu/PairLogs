"""The originals a fix copied from, checked against their checksum lists: the release's MEMBERS.sha256 (tier A at the
root, tier B under tier-b/) and SHA256SUMS' loose files (tier A's), and the delivery's sha256 list. A file to read and
a main guard: worker processes started from a fork server import the main module again from its file (code fed on
stdin has none, and the pool fails before hashing anything, as on 2026-11-04).

python check_originals.py MEMBERS.sha256 RELEASE_DIR DELIVERY.sha256 DELIVERY_ROOT [--procs 16]
Exits 1 if any file differs or is missing.
"""

from __future__ import annotations

import hashlib
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def digest(path: str) -> str:
    d = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                d.update(block)
    except FileNotFoundError:
        return "missing"
    return d.hexdigest()


def jobs(members: Path, release: Path, dlist: Path, droot: Path) -> list[tuple[str, str]]:
    out = []
    for line in open(members):
        want, rel = line.rstrip("\n").split("  ", 1)
        out.append((want, str(release / ("b/" + rel[len("tier-b/"):] if rel.startswith("tier-b/") else "a-names/" + rel))))
    for line in open(members.with_name("SHA256SUMS")):   # the record's loose files: tier A's
        want, name = line.rstrip("\n").split("  ", 1)
        if not name.endswith(".zip") and name != "MEMBERS.sha256":
            out.append((want, str(release / "a-names" / name)))
    for line in open(dlist):
        want, rel = line.rstrip("\n").split("  ", 1)
        out.append((want, str(droot / rel)))
    return out


def main() -> int:
    members, release, dlist, droot = map(Path, sys.argv[1:5])
    procs = int(sys.argv[sys.argv.index("--procs") + 1]) if "--procs" in sys.argv else 16
    todo = jobs(members, release, dlist, droot)
    with ProcessPoolExecutor(procs) as ex:
        got = list(ex.map(digest, [p for _, p in todo], chunksize=8))
    bad = [(p, g) for (w, p), g in zip(todo, got) if w != g]
    print(f"originals: {len(todo) - len(bad):,} of {len(todo):,} files match their lists")
    for p, g in bad[:20]:
        print("  differs:" if g != "missing" else "  missing:", p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
