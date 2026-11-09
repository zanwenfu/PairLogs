"""Write a release tier's repositories.parquet and its changelog's `labels.owners` again from a fuller owner lookup
(collect.tier_labels.owners_again), changing nothing else (2026-11-03: 18 owners of tier A's copies were missing).

argv: OWNERS.json BACKUP_DIR DATE TIER_NAME=FOLDER [TIER_NAME=FOLDER ...]
- Backs up each tier's repositories.parquet and changelog.json first (BACKUP_DIR must not exist).
- Checks after: no repository "not looked up"; every row that had an owner type keeps it; the changelog's owners note
  is the totals recomputed from the lookup, and its other keys are as they were; every other file kept its size and
  time.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from collect import tier_labels


def stats(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
            if p.is_file() and p.name not in ("repositories.parquet", "changelog.json")}


def main() -> int:
    owners, backup, on = sys.argv[1], Path(sys.argv[2]), date.fromisoformat(sys.argv[3])
    tiers = {k: Path(v) for k, v in (x.split("=", 1) for x in sys.argv[4:])}
    signals = json.loads(Path(owners).read_text())
    backup.mkdir(parents=True)
    old_rows, old_notes, before = {}, {}, {}
    for name, tier in tiers.items():
        shutil.copy2(tier / "repositories.parquet", backup / f"{name}-repositories.parquet")
        shutil.copy2(tier / "changelog.json", backup / f"{name}-changelog.json")
        old_rows[name] = pq.read_table(tier / "repositories.parquet").to_pylist()
        old_notes[name] = json.loads((tier / "changelog.json").read_text())
        before[name] = stats(tier)
    print(f"backed up into {backup}", flush=True)
    notes = tier_labels.owners_again(tiers, signals, on, log=lambda m: print(m, flush=True))
    for name, tier in tiers.items():
        new = pq.read_table(tier / "repositories.parquet").to_pylist()
        assert [r["repo"] for r in new] == [r["repo"] for r in old_rows[name]], "the repositories changed"
        kept = sum(1 for o, n in zip(old_rows[name], new) if o["owner_type"] != "not looked up")
        assert all(n == o for o, n in zip(old_rows[name], new) if o["owner_type"] != "not looked up")
        filled = [n["owner_type"] for o, n in zip(old_rows[name], new) if o["owner_type"] == "not looked up"]
        assert "not looked up" not in {r["owner_type"] for r in new}
        _, totals = tier_labels.owners([r["repo"] for r in new], signals, on)
        note = json.loads((tier / "changelog.json").read_text())
        assert {k: v for k, v in note["labels"]["owners"].items() if k not in ("file", "holds")} == totals
        assert {k: v for k, v in note.items() if k != "labels"} == \
               {k: v for k, v in old_notes[name].items() if k != "labels"}
        assert {k: v for k, v in note["labels"].items() if k != "owners"} == \
               {k: v for k, v in old_notes[name]["labels"].items() if k != "owners"}
        assert stats(tier) == before[name], "another file changed"
        types = {}
        for t in filled:
            types[t] = types.get(t, 0) + 1
        print(f"tier {name.upper()}: {len(new):,} repositories, {kept:,} unchanged, {len(filled)} filled "
              f"{dict(sorted(types.items()))}; owners {totals['owners']}, not looked up "
              f"{totals['owners_not_looked_up']}; other files unchanged {len(before[name]):,}", flush=True)
    print("ALL CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
