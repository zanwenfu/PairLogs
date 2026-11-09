"""The work labels as a release's side table: for each part of a release (tier A's full text, tier B's pointers), its
sessions' kind of task, and kind of project and domain (from the session's repository, or the session itself), and
the repositories' labels; with the labelling's record. No evidence quotes (they are the sessions' own words, and tier
B carries none); a session with no typed message in the release's version gets no row (its labels were read from
text the typed rules now give to a program, or it had none). Writes into each part's folder: work_labels/
sessions.parquet, work_labels/repositories.parquet, work_labels/meta.json, each written beside and moved over (a part
made as a hard-linked copy of another keeps the other's files as they were).

python work_labels_release.py VERSION_DIR TIER_A_DIR TIER_B_DIR LABELS_DIR META.json
"""
import json
import os
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


def replace(path: Path, write) -> None:
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)


def main() -> None:
    V, A, B, L, M = sys.argv[1:6]
    con = duckdb.connect()
    typed = {k for (k,) in con.execute(f"""select distinct session_key from read_parquet('{V}/conversations/*/*.parquet',
        union_by_name=true) where typed_by_developer and copy_of is null""").fetchall()}
    parts = {"A": {k for (k,) in con.execute(f"select session_key from read_parquet('{A}/data/*/*.parquet', union_by_name=true)"
                                             " where duplicate_of is null").fetchall()},
             "B": {k for (k,) in con.execute(f"select session_key from read_parquet('{B}/pointers.parquet')").fetchall()}}
    rows = [json.loads(l) for l in open(Path(L) / "sessions.jsonl", encoding="utf-8")]
    repos = [json.loads(l) for l in open(Path(L) / "repos.jsonl", encoding="utf-8")]
    lab = lambda v: (v or {}).get("label")
    also = lambda v: list((v or {}).get("also") or [])
    meta = json.load(open(M, encoding="utf-8"))
    for part, out in (("A", A), ("B", B)):
        keep = [r for r in rows if r["session_key"] in parts[part] and r["session_key"] in typed]
        src = (f"read_parquet('{out}/data/*/*.parquet', union_by_name=true)" if part == "A"
               else f"read_parquet('{out}/pointers.parquet')")
        names = {(x or "").lower() for (x,) in con.execute(f"select distinct repo from {src}").fetchall()}
        rep = [r for r in repos if r["repo"].lower() in names]
        d = Path(out) / "work_labels"
        d.mkdir(exist_ok=True)
        replace(d / "sessions.parquet", lambda f: pq.write_table(pa.table({
            "session_key": [r["session_key"] for r in keep], "source": [r["source"] for r in keep],
            "task": [lab(r["task"]) for r in keep], "task_also": [also(r["task"]) for r in keep],
            "project": [lab(r["project"]) for r in keep], "project_also": [also(r["project"]) for r in keep],
            "domain": [lab(r["domain"]) for r in keep], "domain_also": [also(r["domain"]) for r in keep],
            "project_from": [r["project_from"] for r in keep]}), f))
        replace(d / "repositories.parquet", lambda f: pq.write_table(pa.table({
            "repo": [r["repo"] for r in rep], "project": [lab(r.get("project")) for r in rep],
            "project_also": [also(r.get("project")) for r in rep], "domain": [lab(r.get("domain")) for r in rep],
            "domain_also": [also(r.get("domain")) for r in rep]}), f))
        replace(d / "meta.json", lambda f: f.write_text(json.dumps(
            {**meta, "part": part, "sessions": len(keep), "repositories": len(rep), "sessions_in_part": len(parts[part])},
            indent=1) + "\n", encoding="utf-8"))
        print(f"tier {part}: {len(keep):,} of {len(parts[part]):,} sessions labelled, {len(rep):,} repositories")


if __name__ == "__main__":
    main()
