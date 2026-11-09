"""Read-only, version 2026-10-11: the work-label pilot's sessions and what the labeller reads of each. Three groups:
errata-bench's task sessions (by session id and agent), pushback-review's sessions (by session key of version
2026-10-04), 200 random kept sessions with a typed message (fixed seed). Per session: its kept row's key, agent,
source, repository, and its first ten typed messages (600 characters each, 4,000 in all). argv: errata-bench CSV,
pushback-review TSV, output JSONL; prints coverage."""
import csv
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
eb_csv, pr_tsv, out = sys.argv[1:4]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
con.execute(f"""create temp table s as select session_key, session_id, source, agent, repo, duplicate_of
from read_parquet('{V}/data/*/*.parquet', union_by_name=true)""")
eb = list(csv.DictReader(open(eb_csv)))
pr = list(csv.DictReader(open(pr_tsv), delimiter="\t"))
con.execute("create temp table want(grp varchar, ref varchar, session_id varchar, agent varchar, key varchar)")
con.executemany("insert into want values (?, ?, ?, ?, ?)",
                [("errata-bench", t["task_id"], t["session_id"], t["agent"], None) for t in eb]
                + [("pushback-review:" + p["use"], p["session_key"], None, None, p["session_key"]) for p in pr])
# a wanted session's own row, then its kept row (a duplicate's content lives in the kept copy)
con.execute("""create temp table hit as
select w.grp, w.ref, coalesce(s1.session_key, s2.session_key) own_key,
       coalesce(s1.duplicate_of, s2.duplicate_of) dup_of
from want w
left join s s1 on w.key is not null and s1.session_key = w.key
left join (select * from s where duplicate_of is null) s2 on w.key is null and s2.session_id = w.session_id and s2.agent = w.agent""")
rows = con.execute("select grp, ref, own_key, dup_of from hit").fetchall()
ref_to = {(g, r): (o, d) for g, r, o, d in rows}
# duplicate_of is "source:source_id"; the kept row's session key
kept_key = dict(con.execute("select source || ':' || replace(session_key, source || ':', ''), session_key from s").fetchall())
picked = {}
miss = {}
for (g, r), (own, dup) in ref_to.items():
    key = own if not dup else kept_key.get(dup, None)
    if not key:
        miss[g.split(":")[0]] = miss.get(g.split(":")[0], 0) + 1
        continue
    picked.setdefault(key, {"groups": set(), "refs": []})
    picked[key]["groups"].add(g)
    picked[key]["refs"].append(r)
rnd = [k for (k,) in con.execute("""select s.session_key from s where duplicate_of is null and session_key in
   (select distinct session_key from read_parquet('""" + V + """/conversations/*/*.parquet', union_by_name=true)
    where typed_by_developer and copy_of is null)
   order by hash(session_key || 'p3-pilot-2026-10-26') limit 200""").fetchall()]
for k in rnd:
    picked.setdefault(k, {"groups": set(), "refs": []})["groups"].add("random")
keys = sorted(picked)
con.execute("create temp table k(session_key varchar)")
con.executemany("insert into k values (?)", [(x,) for x in keys])
meta = {r[0]: r[1:] for r in con.execute("select session_key, source, agent, repo from s where session_key in (select session_key from k)").fetchall()}
typed = {}
for key, content in con.execute(f"""select session_key, content from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
    where typed_by_developer and copy_of is null and session_key in (select session_key from k) and content is not null
    order by session_key, turn_number""").fetchall():
    lst = typed.setdefault(key, [])
    if len(lst) < 10 and sum(len(x) for x in lst) < 4000:
        lst.append(content[:600])
with open(out, "w") as f:
    for i, key in enumerate(keys):
        src, agent, repo = meta[key]
        f.write(json.dumps({"item": f"s{i:04d}", "session_key": key, "groups": sorted(picked[key]["groups"]),
                            "refs": picked[key]["refs"], "source": src, "agent": agent, "repo": repo,
                            "typed": typed.get(key, [])}, ensure_ascii=False) + "\n")
by = {}
for v in picked.values():
    for g in v["groups"]:
        by[g] = by.get(g, 0) + 1
print(json.dumps({"sessions": len(keys), "by_group": by, "missing_by_group": miss,
                  "with_repo": sum(1 for k in keys if meta[k][2]), "repos": len({meta[k][2] for k in keys if meta[k][2]}),
                  "no_typed_text": sum(1 for k in keys if not typed.get(k))}, indent=1))
