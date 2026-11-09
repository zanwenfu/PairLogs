"""Read-only honest counts for the dataset paper (version 2026-10-11): scripted runs, products, repository owners,
concentration, the release tiers, and why 4,026 SWE-chat sessions are kept from SWE-chat's own copy.
Writes JSON to argv[1].
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
CONV = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"
OUT = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    try:
        cur = con.execute(sql)
    except Exception as e:   # one failed query must not lose the others
        res[name] = [{"error": str(e)[:300]}]
        print(f"== {name}: ERROR {e}", file=sys.stderr, flush=True)
        return
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"== {name}: {len(res[name])} rows", file=sys.stderr, flush=True)


con.execute(f"""create temp table s as
select session_key, source, repo, repo_license, agent, models, started_at, duplicate_of, n_user_messages,
       split_part(repo, '/', 1) as owner,
       (repo_license is not null and repo_license not in ('none', 'unrecognized', '')) as licensed
from {SESS} where duplicate_of is null""")
con.execute(f"""create temp table typed as
select session_key, count(*) n_typed, count(distinct content) n_distinct_typed
from {CONV} where typed_by_developer and copy_of is null group by all""")
con.execute("""create temp table s2 as
select s.*, coalesce(t.n_typed, 0) n_typed, coalesce(t.n_distinct_typed, 0) n_distinct_typed
from s left join typed t using (session_key)""")

q("by_source", """select source, count(*) sessions, count(distinct repo) repos, count(distinct owner) owners,
       sum((n_typed = 0)::int) no_typed_message, sum(licensed::int) licensed
       from s2 group by rollup(source) order by sessions desc""")
q("agents", "select agent, count(*) n from s2 group by all order by n desc")
q("top_repos", """select source, repo, count(*) n, round(100.0 * count(*) / sum(count(*)) over (partition by source), 1) pct_of_source
                  from s2 where repo is not null group by source, repo
                  qualify row_number() over (partition by source order by count(*) desc) <= 5
                  order by source, n desc""")
q("repos_100plus", """select source, count(*) repos, sum(n) sessions from
                      (select source, repo, count(*) n from s2 where repo is not null group by all having count(*) >= 100)
                      group by all""")
q("owners_total", "select count(distinct owner) owners, count(distinct repo) repos from s2 where repo is not null")
# scripted: a repository whose sessions mostly share the same first typed text is a script; flag repos where
# >= 20 sessions and the distinct-first-prompt ratio is low
con.execute(f"""create temp table firsts as
select session_key, first(content order by turn_number) first_typed
from {CONV} where typed_by_developer and copy_of is null and content is not null group by all""")
q("scripted_repos", """select s2.source, s2.repo, count(*) sessions, count(distinct left(f.first_typed, 60)) distinct_first60
                       from s2 join firsts f using (session_key) where s2.repo is not null
                       group by all having count(*) >= 20 and count(distinct left(f.first_typed, 60)) <= 0.2 * count(*)
                       order by sessions desc limit 30""")
# release tiers
q("tiers", """select source,
       count(*) filter (where licensed or source in ('dataclaw', 'openhands-feedback')) tier_a,
       count(*) filter (where source <> 'swe-chat-v2') tier_b,
       count(*) filter (where source = 'swe-chat-v2') swe_chat_rejoin
     from s2 group by rollup(source) order by tier_b desc""")
q("licence_unrecognized_repos", """select source, repo, count(*) n from s2 where repo_license = 'unrecognized'
                                   group by all order by n desc limit 40""")
# new sessions (not in SWE-chat, DataClaw or OpenHands) per tier
con.execute(f"""create temp table earlier as
select distinct duplicate_of k from {SESS}
where duplicate_of is not null and source in ('swe-chat-v2', 'dataclaw', 'openhands-feedback')""")
q("new_by_tier", """select source, count(*) new_sessions, sum(licensed::int) new_in_tier_a
                    from s2 where source in ('entire', 'specstory', 'aider') and session_key not in (select k from earlier)
                    group by rollup(source)""")
# SWE-chat sessions kept from SWE-chat's copy: is their repository in our Entire crawl at all?
q("swechat_kept_repos", """select (repo in (select distinct repo from s2 where source = 'entire')) as repo_in_our_crawl,
                                  count(*) sessions, count(distinct repo) repos
                           from s2 where source = 'swe-chat-v2' group by all""")
q("swechat_kept_repo_sample", """select repo, count(*) n from s2 where source = 'swe-chat-v2'
                                 and repo not in (select distinct repo from s2 where source = 'entire')
                                 group by all order by n desc limit 25""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str)[:20000])
