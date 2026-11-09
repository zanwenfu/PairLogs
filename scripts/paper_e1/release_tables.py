"""Rows and files of each released table, per tier (Parquet metadata only): the paper's schema table.

python scripts/paper_e1/release_tables.py RELEASE_DIR OUT.json     (RELEASE_DIR holds a-names/ and b/)
"""
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

R = Path(sys.argv[1])
TABLES = {"a": {"sessions": "data/*/*.parquet", "messages": "conversations/*/*.parquet",
                "helper messages": "conversations_subagents/*/*.parquet",
                "work labels, sessions": "work_labels/sessions.parquet",
                "work labels, repositories": "work_labels/repositories.parquet",
                "attribution": "attribution/entire.parquet", "repositories": "repositories.parquet"},
          "b": {"pointers": "pointers.parquet", "message labels": "labels/conversations/*/*.parquet",
                "SWE-chat prompts": "labels/swe_chat_prompts.parquet",
                "work labels, sessions": "work_labels/sessions.parquet",
                "work labels, repositories": "work_labels/repositories.parquet",
                "attribution": "attribution/entire.parquet", "repositories": "repositories.parquet"}}
out = {}
for tier, tables in TABLES.items():
    root = R / ("a-names" if tier == "a" else tier)
    got = {}
    for name, pat in tables.items():
        files = sorted(root.glob(pat))
        got[name] = {"files": len(files), "rows": sum(pq.ParquetFile(f).metadata.num_rows for f in files),
                     "columns": len(pq.read_schema(files[0]).names) if files else 0}
    copied = [p for p in (root / "copied").rglob("*") if p.is_file()] if (root / "copied").exists() else []
    got["copied files"] = {"files": len(copied), "bytes": sum(p.stat().st_size for p in copied)}
    got["all files"] = {"files": sum(1 for p in root.rglob("*") if p.is_file()),
                        "bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file())}
    out["tier A" if tier == "a" else "tier B"] = got
Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
print(json.dumps(out))
