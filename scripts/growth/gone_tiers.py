"""The version's sessions in repositories that gave no session on the Nov 6 re-read, by reason (deleted or private /
public but holding none now), kept or copy, and release tier (A: full text in the upload folder; B: pointers). Counts."""
import json, sqlite3, sys
from collections import Counter
import duckdb
BUILD, UPLOAD, GONE, LEDGERS = sys.argv[1:5]
gone = {x.strip() for x in open(GONE) if x.strip()}
fresh_repos = set()
for led in LEDGERS.split(","):
    for (row,) in sqlite3.connect(f"file:{led}?mode=ro", uri=True).execute("SELECT row FROM items"):
        fresh_repos.add(json.loads(row).get("repo"))
con = duckdb.connect()
rows = con.execute(f"""SELECT session_key, source, repo, duplicate_of IS NULL FROM read_parquet('{BUILD}/data/*/*.parquet', union_by_name=true)
                       WHERE source IN ('entire', 'specstory', 'aider')""").fetchall()
ta = {k for (k,) in con.execute(f"SELECT session_key FROM read_parquet('{UPLOAD}/data/*/*.parquet', union_by_name=true)").fetchall()}
tb = {k for (k,) in con.execute(f"SELECT session_key FROM read_parquet('{UPLOAD}/tier-b/pointers.parquet')").fetchall()}
c, repos = Counter(), Counter()
listed = {r for _, _, r, _ in rows}
print("version repos in these sources", len(listed), "of them read back with a session", len(listed & fresh_repos))
for key, src, repo, kept in rows:
    if repo in fresh_repos:
        continue
    why = "deleted or private" if repo in gone else "public, holds none now"
    tier = "A" if key in ta else "B" if key in tb else "neither"
    c[(why, "kept" if kept else "copy", tier)] += 1
    repos[(why, tier, repo)] += 1
for k, n in sorted(c.items()):
    print(k, n)
print("repositories by reason and tier:", Counter((w, t) for w, t, _ in repos))
print("largest:", [(w, t, n) for (w, t, r), n in repos.most_common(8)])
