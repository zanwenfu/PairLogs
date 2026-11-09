"""The growth test's reading: a fresh read of the version's GitHub repositories (fresh state, no token) against the
version's own sessions. Counts only, never text.

For each source: sessions read again with the same content (same), with other content (changed: grown or rewritten),
sessions the version lacks (new), and of those the ones whose dedup key the version lacks too (new and not a copy of
anything in it), the ones with a user entry, the ones started after the version's last fetch (and with no start time); the version's
sessions in the repositories read that did not come back (gone), apart for repositories that gave no session at all.

python compare_growth.py VERSION_INDEX.parquet REPOS.txt LEDGER.sqlite OUT.json [ERRORS.txt]

ERRORS.txt: repositories the fetch reported an error for (from the job's log); they are left out of every count of
the version's sessions read again or gone, and counted apart, since an error (GitHub refusing, a timeout) says
nothing about whether the repository still holds its sessions.
"""
import json
import sqlite3
import sys
from collections import Counter

import duckdb

IDX, REPOS, LEDGER, OUT = sys.argv[1:5]
errors = {x.strip() for x in open(sys.argv[5]) if x.strip()} if len(sys.argv) > 5 else set()
listed = {x.strip() for x in open(REPOS) if x.strip()}
repos = listed - errors
led = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
fresh = []
for src, sid, h, dk, row in led.execute("SELECT source, source_id, content_sha256, dedup_key, row FROM items"):
    r = json.loads(row)
    # user entries as read (every source's reader fills n_user_entries; n_user_messages, the typed ones, is filled
    # only for Entire before the build, so it would count SpecStory's and Aider's new sessions as holding none)
    fresh.append((src, sid, h, dk, r.get("repo"), r.get("started_at"), int(r.get("n_user_entries") or 0)))
assert fresh, "the fresh read holds no session"
con = duckdb.connect()
con.execute("CREATE TABLE f(source VARCHAR, source_id VARCHAR, h VARCHAR, dk VARCHAR, repo VARCHAR, started VARCHAR, n_user INT)")
con.executemany("INSERT INTO f VALUES (?, ?, ?, ?, ?, ?, ?)", fresh)
con.execute(f"CREATE TABLE v AS SELECT * FROM read_parquet('{IDX}')")
last_fetch = con.execute("SELECT max(fetched_at) FROM v WHERE source IN ('entire', 'specstory', 'aider')").fetchone()[0]
con.execute("CREATE TABLE r(repo VARCHAR)")
con.executemany("INSERT INTO r VALUES (?)", [(x,) for x in sorted(repos)])
out = {"repositories_listed": len(listed), "repositories_with_errors": len(errors & listed), "repositories_read": len(repos),
       "version_last_fetch": last_fetch, "fresh_sessions": len(fresh), "by_source": {}}
for src in ("entire", "specstory", "aider"):
    q = lambda sql: con.execute(sql, [src]).fetchone()[0]
    s = {
        "fresh": q("SELECT count(*) FROM f WHERE source = ?"),
        "same": q("SELECT count(*) FROM f JOIN v USING (source, source_id) WHERE f.source = ? AND v.content_sha256 = f.h"),
        "changed": q("SELECT count(*) FROM f JOIN v USING (source, source_id) WHERE f.source = ? AND v.content_sha256 <> f.h"),
        "new": q("SELECT count(*) FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ?"),
        "new_not_a_copy": q("""SELECT count(*) FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ?
                               AND (f.dk IS NULL OR f.dk NOT IN (SELECT dedup_key FROM v WHERE dedup_key IS NOT NULL))"""),
        "new_with_user_entry": q("SELECT count(*) FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ? AND f.n_user > 0"),
        "new_start_unknown": q("SELECT count(*) FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ? AND f.started IS NULL"),
        "new_started_after_last_fetch": q(f"""SELECT count(*) FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ?
                                              AND f.started > '{last_fetch}'"""),
        "new_repositories": q("SELECT count(DISTINCT f.repo) FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ?"),
        "version_sessions_in_repos_read": q("SELECT count(*) FROM v JOIN r USING (repo) WHERE v.source = ?"),
        "gone_repo_gave_nothing": q("""SELECT count(*) FROM v JOIN r USING (repo) WHERE v.source = ?
                                       AND v.repo NOT IN (SELECT repo FROM f WHERE repo IS NOT NULL)"""),
        "gone_from_a_repo_read": q("""SELECT count(*) FROM v JOIN r USING (repo) WHERE v.source = ?
                                      AND v.repo IN (SELECT repo FROM f WHERE repo IS NOT NULL)
                                      AND NOT EXISTS (SELECT 1 FROM f WHERE f.source = v.source AND f.source_id = v.source_id)"""),
    }
    top = con.execute("""SELECT f.repo, count(*) n FROM f ANTI JOIN v USING (source, source_id) WHERE f.source = ?
                         GROUP BY 1 ORDER BY n DESC LIMIT 5""", [src]).fetchall()
    s["new_top5_repos_share"] = round(sum(n for _, n in top) / s["new"], 3) if s["new"] else None
    assert s["same"] + s["changed"] + s["new"] == s["fresh"], (src, s)
    out["by_source"][src] = s
out["repos_with_no_session_now"] = con.execute("SELECT count(*) FROM r WHERE repo NOT IN (SELECT repo FROM f WHERE repo IS NOT NULL)").fetchone()[0]
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out, indent=1))
