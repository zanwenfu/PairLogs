"""The version's repositories (Entire, SpecStory, Aider) that gave no session on the Nov 6 re-read, with the sources
the version holds from each; written as two lists (deleted or private / still public)."""
import json, sqlite3, sys
from collections import defaultdict
import duckdb
IDX, GONE, LEDGERS, OUT = sys.argv[1:5]
gone = {x.strip() for x in open(GONE) if x.strip()}
fresh = set()
for led in LEDGERS.split(","):
    for (row,) in sqlite3.connect(f"file:{led}?mode=ro", uri=True).execute("SELECT row FROM items"):
        fresh.add(json.loads(row).get("repo"))
src = defaultdict(lambda: defaultdict(int))
for s, r in duckdb.connect().execute(f"SELECT source, repo FROM read_parquet('{IDX}') WHERE source IN ('entire','specstory','aider')").fetchall():
    if r not in fresh:
        src[r][s] += 1
with open(OUT, "w") as f:
    for r in sorted(src):
        f.write(f"{'gone' if r in gone else 'public'}\t{r}\t{json.dumps(dict(src[r]))}\n")
print(len(src), sum(r in gone for r in src))
