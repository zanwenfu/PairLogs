"""The update test's reading on the version's kept sessions only (copies left out: duplicate_of null), so its counts
share the paper's basis (61,609 kept sessions) rather than the session rows of the repositories read (copies included).
Read-only on the same fresh ledger, repository list and error list compare_growth.py read. Counts only, never text.

python compare_growth_kept.py VERSION_INDEX.parquet REPOS.txt LEDGER.sqlite OUT.json ERRORS.txt
"""
import json
import sqlite3
import sys

import duckdb

IDX, REPOS, LEDGER, OUT, ERR = sys.argv[1:6]
errors = {x.strip() for x in open(ERR) if x.strip()}
repos = {x.strip() for x in open(REPOS) if x.strip()} - errors
led = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
fresh = [(src, sid, h, json.loads(row).get("repo")) for src, sid, h, row in
         led.execute("SELECT source, source_id, content_sha256, row FROM items")]
con = duckdb.connect()
con.execute("CREATE TABLE f(source VARCHAR, source_id VARCHAR, h VARCHAR, repo VARCHAR)")
con.executemany("INSERT INTO f VALUES (?, ?, ?, ?)", fresh)
con.execute(f"CREATE TABLE v AS SELECT * FROM read_parquet('{IDX}') WHERE duplicate_of IS NULL")   # kept only
con.execute("CREATE TABLE r(repo VARCHAR)")
con.executemany("INSERT INTO r VALUES (?)", [(x,) for x in sorted(repos)])
out = {"repositories_read": len(repos), "by_source": {}}
for src in ("entire", "specstory", "aider"):
    q = lambda sql: con.execute(sql, [src]).fetchone()[0]
    s = {"kept_in_repos_read": q("SELECT count(*) FROM v JOIN r USING (repo) WHERE v.source = ?"),
         "same": q("""SELECT count(*) FROM v JOIN r USING (repo) JOIN f USING (source, source_id)
                      WHERE v.source = ? AND v.content_sha256 = f.h"""),
         "changed": q("""SELECT count(*) FROM v JOIN r USING (repo) JOIN f USING (source, source_id)
                         WHERE v.source = ? AND v.content_sha256 <> f.h"""),
         "gone": q("""SELECT count(*) FROM v JOIN r USING (repo) WHERE v.source = ?
                      AND NOT EXISTS (SELECT 1 FROM f WHERE f.source = v.source AND f.source_id = v.source_id)"""),
         "gone_repo_gave_nothing": q("""SELECT count(*) FROM v JOIN r USING (repo) WHERE v.source = ?
                                        AND v.repo NOT IN (SELECT repo FROM f WHERE repo IS NOT NULL)""")}
    assert s["same"] + s["changed"] + s["gone"] == s["kept_in_repos_read"], (src, s)
    out["by_source"][src] = s
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out))
