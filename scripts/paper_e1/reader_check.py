"""The readers other than Entire's, checked against a second, simple reader of the same files (the second review,
11-06: "the SpecStory, Aider and DataClaw readers were not" checked).

python reader_check.py BUILD COPIED OUT.json [--n 50] [--seed reader-check-2026-11-06]

BUILD: the version as built (run2/build-fix2/2026-11-02); COPIED: the copied datasets' files (run2/data/copied).
Per source, sessions drawn at random among the kept ones (duplicate_of null). For each, the user entries counted two
ways: our reader's rows (main conversation table, role 'user'), and a second reader that knows only each format's
speaker marks:
- SpecStory: lines that open a user turn (`_**User`, or `## Prompt` in one repository's hooks-made layout) in the file;
- Aider: runs of consecutive lines that start with `#### ` (the user's input) in the session's file;
- DataClaw: messages whose role is "user" in the session's own line of its conversations.jsonl.
Counts only, never text.
"""
import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("build")
ap.add_argument("copied")
ap.add_argument("out")
ap.add_argument("--n", type=int, default=50)
ap.add_argument("--seed", default="reader-check-2026-11-06")
args = ap.parse_args()
B = args.build
con = duckdb.connect()
SPEC_USER = re.compile(r"^(?:_\*\*User\b|## Prompt\b)", re.M)   # SpecStory's own export; "## Prompt" in a hooks-made layout


def aider_turns(text):
    runs, inside = 0, False
    for line in text.splitlines():
        if line.startswith("#### "):
            if not inside:
                runs += 1
            inside = True
        else:
            inside = False
    return runs


def dataclaw_line(session_key):
    # dataclaw:<owner>/<dataset>:<path in the dataset>:<line>
    _, rest = session_key.split(":", 1)
    ds, tail = rest.split(":", 1)
    path, line = tail.rsplit(":", 1)
    root = Path(args.copied) / "dataclaw" / ds.replace("/", "__")
    revs = [p for p in root.iterdir() if p.is_dir()]
    assert len(revs) == 1, (root, revs)
    return revs[0] / path, int(line)


out = {"seed": args.seed, "n": args.n, "by_source": {}}
for src in ("specstory", "aider", "dataclaw"):
    rows = con.execute(f"""SELECT session_key, session_id, files FROM read_parquet('{B}/data/{src}/*.parquet', union_by_name=true)
                           WHERE duplicate_of IS NULL""").fetchall()
    rows.sort(key=lambda r: hashlib.md5((args.seed + "|" + r[0]).encode()).hexdigest())
    pick = rows[:args.n]
    keys = [r[0] for r in pick]
    con.execute("CREATE OR REPLACE TABLE k(session_key VARCHAR)")
    con.executemany("INSERT INTO k VALUES (?)", [(x,) for x in keys])
    ours = dict(con.execute(f"""SELECT session_key, count(*) FILTER (WHERE role = 'user')
                                FROM read_parquet('{B}/conversations/{src}/*.parquet', union_by_name=true)
                                WHERE session_key IN (SELECT session_key FROM k) GROUP BY 1""").fetchall())
    res = Counter()
    diffs = []
    for key, sid, files in pick:
        if src == "specstory":
            assert files and len(files) >= 1, key
            simple = sum(len(SPEC_USER.findall(f["content"] or "")) for f in files)
        elif src == "aider":
            assert files and len(files) >= 1, key
            simple = sum(aider_turns(f["content"] or "") for f in files)
        else:
            path, line = dataclaw_line(key)
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            rec = None
            for cand in (line, line - 1):   # the line number's base, settled by the record's own session id
                if 0 <= cand < len(lines):
                    d = json.loads(lines[cand])
                    if d.get("session_id") == sid:
                        rec = d
                        break
            if rec is None:
                res["dataclaw record not found by session id"] += 1
                continue
            simple = sum(1 for m in rec.get("messages") or [] if m.get("role") == "user")
        o = ours.get(key, 0)
        res["same"] += o == simple
        res["ours more"] += o > simple
        res["ours fewer"] += o < simple
        if o != simple:
            diffs.append({"session_key": key, "ours": o, "simple": simple})
    out["by_source"][src] = {"sessions": len(pick), **res, "user_entries_ours": sum(ours.get(k, 0) for k in keys),
                             "differences": diffs}
json.dump(out, open(args.out, "w"), indent=1)
print(json.dumps({s: {k: v for k, v in d.items() if k != "differences"} for s, d in out["by_source"].items()}, indent=1))
