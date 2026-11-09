"""Read-only: the release's own check (collect.check.run: gitleaks and the strict formats with the not-secrets list,
personal data in the scraped sources' rows, a private key's structure, values naming the run machine) over a release
tier exactly as it will be published, and first over two controls: one real conversations file, and one copied record,
of this tier, each with a made-up GitHub token planted and each checked alone; the check must FAIL on each with the
token found, or a pass proves nothing. Tier B holds no text by design: its tables' columns are listed with their types, for a reader to
confirm. Prints counts and column names only, never a finding's text.

ERRATA_DATA_ROOT and ERRATA_STATE_ROOT must name fresh scratch folders (the check writes its ledger entry and work
files there, never beside the release).

python tier_check.py TIER_A TIER_B WORK [--procs N]
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import string
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from collect import check, config
from collect.ledger import Ledger


def token() -> str:
    return "ghp_" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(36))


def control(tier: Path, work: Path, kind: str) -> Path:
    """A tier of one file with a planted token, plus the changelog: for "conversations" the smallest conversations
    file with rows (the first cell of its text column replaced), for "copied" the smallest copied JSON-lines record
    (one line added). Each kind is checked on its own, so one cannot pass for the other."""
    ctl = work / f"control-{kind}"
    shutil.rmtree(ctl, ignore_errors=True)
    if kind == "conversations":
        convs = sorted((p for p in (tier / "conversations").rglob("*.parquet") if pq.ParquetFile(p).metadata.num_rows),
                       key=lambda p: p.stat().st_size)
        src = convs[0]
        t = pq.read_table(src)
        col = next(n for n in ("content", "text", "message") if n in t.column_names)
        cells = t.column(col).to_pylist()
        cells[0] = f"here is the key: {token()} keep it"
        t = t.set_column(t.column_names.index(col), col, pa.array(cells, type=t.schema.field(col).type))
        dst = ctl / src.relative_to(tier)
        dst.parent.mkdir(parents=True)
        pq.write_table(t, dst)
    else:
        src = sorted((tier / "copied").rglob("*.jsonl"), key=lambda p: p.stat().st_size)[0]
        dst = ctl / src.relative_to(tier)
        dst.parent.mkdir(parents=True)
        shutil.copyfile(src, dst)
        with open(dst, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"role": "user", "content": f"token = {token()}"}) + "\n")
    shutil.copyfile(tier / "changelog.json", ctl / "changelog.json")
    return ctl


def show(name: str, r: dict) -> None:
    print(f"{name}: rows {r['rows']:,}; conversation rows {r['conversation_rows']:,}; files {r['files']:,}; "
          f"scanned {r['scanned_bytes'] / 1e9:,.2f} GB; findings {r['findings'] or 0}; "
          f"values naming the run machine {r['machine_values']}; {'PASS' if r['ok'] else 'FAIL'}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier_a", type=Path)
    ap.add_argument("tier_b", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("--procs", type=int, default=8)
    a = ap.parse_args()
    cfg = config.load()
    for root in (cfg.data, cfg.state):
        if a.tier_a.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(a.tier_a.resolve()):
            sys.exit("refused: the scratch roots and the tier overlap")
    a.work.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(cfg.ledger)

    for kind in ("conversations", "copied"):
        ctl = control(a.tier_a, a.work, kind)
        r = check.run(cfg, ledger, ctl, procs=2, log=lambda m: None)
        show(f"control, a token planted in a {kind} file", r)
        shutil.rmtree(ctl, ignore_errors=True)
        if r["ok"] or not any("github" in k.lower() for k in r["findings"]):
            print(f"tier check: FAIL (the {kind} control's token was not found: the check proves nothing)")
            return 2

    files = sum(1 for p in a.tier_a.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in a.tier_a.rglob("*") if p.is_file())
    print(f"tier A on disk: {files:,} files, {size / 1e9:,.2f} GB", flush=True)
    r = check.run(cfg, ledger, a.tier_a, procs=a.procs, log=print)
    show("tier A", r)
    for e in r["examples"]:   # where each finding is: its rule, file, line and the value's shape (never the value)
        print("  finding:", json.dumps(e))
    for e in r["machine_examples"]:
        print("  value naming the run machine in:", e)

    print("tier B's tables (column: type), to confirm no column holds text:")
    seen: dict[str, set] = {}
    for p in sorted(a.tier_b.rglob("*.parquet")):
        group = str(p.relative_to(a.tier_b).parent) if p.parent != a.tier_b else p.name
        for f in pq.ParquetFile(p).schema_arrow:
            seen.setdefault(group, set()).add(f"{f.name}: {f.type}")
    for group, cols in seen.items():
        print(f"  {group}: " + "; ".join(sorted(cols)))
    others = sorted(str(p.relative_to(a.tier_b)) for p in a.tier_b.rglob("*") if p.is_file() and p.suffix != ".parquet")
    print("tier B's other files:", others)
    return 0 if r["ok"] else 1


if __name__ == "__main__":   # the check's pool starts its workers by forkserver (Python 3.14), which imports this file
    sys.exit(main())
