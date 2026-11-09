"""Read-only: the "core" subset of a version (VERSION_DIR), for headline counts a reviewer can trust: kept sessions
with at least one typed message, outside the scripted repositories (m4's rule: 20+ sessions whose first typed texts,
first 60 characters, are at most 1 in 5 distinct). Per source and release tier, new (in no earlier public dataset:
SWE-chat, DataClaw, OpenHands feedback), the 10 largest repositories' share, the vendor's own checkpoint repository's
share (entireio/cli-checkpoints), and sessions per month started. Writes JSON to argv[1]."""
import json
import os
import sys

import duckdb

V = os.environ["VERSION_DIR"]
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
CONV = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"
OUT = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp-core'")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]


con.execute(f"""create temp table s as
select session_key, source, repo, try_cast(started_at as timestamp) started,
       (repo_license is not null and repo_license not in ('none', 'unrecognized', '')) as licensed
from {SESS} where duplicate_of is null""")
con.execute(f"""create temp table firsts as
select session_key, count(*) n_typed, first(content order by turn_number) first_typed
from {CONV} where typed_by_developer and copy_of is null and content is not null group by all""")
con.execute(f"""create temp table earlier as
select distinct duplicate_of k from {SESS}
where duplicate_of is not null and source in ('swe-chat-v2', 'dataclaw', 'openhands-feedback')""")
con.execute("""create temp table scripted as
select s.repo from s join firsts f using (session_key) where s.repo is not null
group by 1 having count(*) >= 20 and count(distinct left(f.first_typed, 60)) <= 0.2 * count(*)""")
con.execute("""create temp table c as
select s.*, coalesce(f.n_typed, 0) n_typed,
       coalesce(f.n_typed, 0) > 0 and (s.repo is null or s.repo not in (select repo from scripted)) as core,
       s.source <> 'swe-chat-v2' and (s.licensed or s.source in ('dataclaw', 'openhands-feedback')) as tier_a,
       s.source in ('entire', 'specstory', 'aider') and s.session_key not in (select k from earlier) as new
from s left join firsts f using (session_key)""")
q("scripted_repos", "select count(*) repos, (select count(*) from c where repo in (select repo from scripted)) sessions from scripted")
q("headline", """select count(*) kept, count(*) filter (where n_typed > 0) with_typed, count(*) filter (where core) core,
       count(*) filter (where core and tier_a) core_tier_a, count(*) filter (where core and new) core_new,
       count(*) filter (where core and new and tier_a) core_new_tier_a,
       count(distinct repo) filter (where core) core_repos,
       count(distinct split_part(repo, '/', 1)) filter (where core) core_owners from c""")
q("core_by_source", """select source, count(*) filter (where core) core, count(*) filter (where core and tier_a) tier_a,
       count(*) filter (where core and new) new, count(*) sessions_kept, count(*) filter (where n_typed > 0) with_typed
       from c group by rollup(source) order by core desc""")
q("top10_share", """with r as (select repo, count(*) n from c where core and repo is not null group by 1 order by n desc limit 10)
       select (select sum(n) from r) top10, (select count(*) from c where core) core,
              round(100.0 * (select sum(n) from r) / (select count(*) from c where core), 1) pct,
              (select count(*) from c where core and repo = 'entireio/cli-checkpoints') vendor_repo""")
q("per_month", """select strftime(date_trunc('month', started), '%Y-%m') as ym, count(*) filter (where core) core,
       count(*) filter (where core and source = 'entire') entire, count(*) filter (where core and source = 'specstory') specstory,
       count(*) filter (where core and source = 'dataclaw') dataclaw, count(*) filter (where core and source = 'swe-chat-v2') swe_chat
       from c group by 1 order by 1""")
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
