"""The Zenodo upload set for one release (Zenodo takes at most 100 files and 50 GB a record).

- Tier A's folders as uncompressed ZIP archives (the Parquet files are compressed already), one a folder and source
  (tierA-conversations-entire.zip, ...), split into parts of at most --max-gb; members keep their paths inside the
  tier, so unzipping every archive into one folder gives tier A's layout back, its loose top-level files
  (changelog.json, croissant.json, repositories.parquet) beside them as they are.
- Tier B whole as one archive, its members under tier-b/.
- Each archive deterministic (members sorted, one fixed time and mode), so the same tier makes the same bytes.
- Each archive read back: every member's CRC, and its bytes against the source file's SHA-256.
- SHA256SUMS: every archive and loose file; MEMBERS.sha256: every file inside the archives, by its path.

python pack_zenodo.py TIER_A TIER_B OUT [--max-gb 4] [--procs 6]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

WHEN = (2026, 11, 2, 0, 0, 0)
BLOCK = 8 << 20


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(BLOCK):
            h.update(b)
    return h.hexdigest()


def plan(tier_a: Path, tier_b: Path, max_bytes: int) -> tuple[list[tuple[str, list[tuple[Path, str]]]], list[Path]]:
    """(archive name, [(source file, member path)]) for every archive, and tier A's loose top-level files."""
    archives: list[tuple[str, list[tuple[Path, str]]]] = []
    for folder in sorted(p for p in tier_a.iterdir() if p.is_dir()):
        groups = sorted(p for p in folder.iterdir() if p.is_dir())
        loose = [p for p in folder.iterdir() if p.is_file()]
        units = [(f"tierA-{folder.name}-{g.name}", sorted(f for f in g.rglob("*") if f.is_file())) for g in groups]
        if loose:
            units.append((f"tierA-{folder.name}", sorted(loose)))
        for name, files in units:
            parts, cur, size = [], [], 0
            for f in files:
                n = f.stat().st_size
                if cur and size + n > max_bytes:
                    parts.append(cur)
                    cur, size = [], 0
                cur.append(f)
                size += n
            if cur:
                parts.append(cur)
            for i, part in enumerate(parts, 1):
                label = name if len(parts) == 1 else f"{name}-part{i:02d}"
                archives.append((f"{label}.zip", [(f, str(f.relative_to(tier_a))) for f in part]))
    b_files = sorted(f for f in tier_b.rglob("*") if f.is_file())
    archives.append(("tierB.zip", [(f, "tier-b/" + str(f.relative_to(tier_b))) for f in b_files]))
    loose_a = sorted(p for p in tier_a.iterdir() if p.is_file())
    return archives, loose_a


def make(job: tuple[str, list[tuple[str, str]], str]) -> dict:
    """One archive written, then read back and every member compared with its source."""
    name, members, out = job
    dst = Path(out) / name
    tmp = dst.with_suffix(".zip.part")
    want: dict[str, str] = {}
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for src, arc in members:
            info = zipfile.ZipInfo(arc, date_time=WHEN)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            h = hashlib.sha256()
            size = Path(src).stat().st_size
            with open(src, "rb") as fh, zf.open(info, "w", force_zip64=size > (1 << 30)) as w:
                while b := fh.read(BLOCK):
                    h.update(b)
                    w.write(b)
            want[arc] = h.hexdigest()
    bad = []
    with zipfile.ZipFile(tmp) as zf:
        if zf.testzip() is not None:
            bad.append("crc")
        names = zf.namelist()
        if sorted(names) != sorted(want):
            bad.append("members")
        for arc in names:
            h = hashlib.sha256()
            with zf.open(arc) as r:
                while b := r.read(BLOCK):
                    h.update(b)
            if h.hexdigest() != want.get(arc):
                bad.append(arc)
    for src, arc in members:   # the source unchanged while it was packed
        if sha256(Path(src)) != want[arc]:
            bad.append(f"source changed: {arc}")
    if bad:
        return {"name": name, "bad": bad[:5], "n_bad": len(bad)}
    tmp.rename(dst)
    return {"name": name, "members": want, "bytes": dst.stat().st_size, "sha256": sha256(dst)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier_a", type=Path)
    ap.add_argument("tier_b", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--max-gb", type=float, default=4.0)
    ap.add_argument("--procs", type=int, default=6)
    a = ap.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        sys.exit(f"refused: {a.out} is not empty")
    a.out.mkdir(parents=True, exist_ok=True)
    archives, loose = plan(a.tier_a, a.tier_b, int(a.max_gb * 1e9))
    n_src = sum(len(m) for _, m in archives) + len(loose)
    n_tier = sum(1 for p in a.tier_a.rglob("*") if p.is_file()) + sum(1 for p in a.tier_b.rglob("*") if p.is_file())
    print(f"{len(archives)} archives + {len(loose)} loose files, from {n_src:,} files of {n_tier:,} in the tiers",
          flush=True)
    if n_src != n_tier or len(archives) + len(loose) > 90:
        sys.exit("refused: not every tier file is planned once, or too many files for one Zenodo record")
    jobs = [(name, [(str(s), arc) for s, arc in members], str(a.out)) for name, members in archives]
    got = []
    with ProcessPoolExecutor(a.procs) as pool:
        for r in pool.map(make, sorted(jobs, key=lambda j: -sum(Path(s).stat().st_size for s, _ in j[1]))):
            print(r["name"], "FAILED " + str(r["bad"]) if "bad" in r else f"{r['bytes'] / 1e9:,.2f} GB ok", flush=True)
            got.append(r)
    if any("bad" in r for r in got):
        print("pack: FAIL")
        return 1
    sums = [(r["sha256"], r["name"]) for r in got]
    for p in loose:
        (a.out / p.name).write_bytes(p.read_bytes())
        sums.append((sha256(a.out / p.name), p.name))
    (a.out / "SHA256SUMS").write_text("".join(f"{h}  {n}\n" for h, n in sorted(sums, key=lambda x: x[1])))
    members = sorted((h, arc) for r in got for arc, h in r["members"].items())
    (a.out / "MEMBERS.sha256").write_text("".join(f"{h}  {arc}\n" for h, arc in sorted(members, key=lambda x: x[1])))
    total = sum(p.stat().st_size for p in a.out.iterdir())
    print(f"pack: PASS: {len(list(a.out.iterdir()))} files, {total / 1e9:,.2f} GB, {len(members):,} members")
    return 0


if __name__ == "__main__":   # the pool's workers start by forkserver (Python 3.14), which imports this file
    sys.exit(main())
