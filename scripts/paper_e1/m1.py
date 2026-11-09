"""Read-only measurements on errata-data 2026-10-11 for the MSR data paper (run at Duke via Slurm).

1. SWE-chat v2's labelled user prompts: how many did a person type, by our tested rule (typed_by_developer on the
   same transcript entry), and SWE-chat's own pushback rate with and without the rest.
2. Our pushback label against SWE-chat's on the typed messages both labelled.
3. Sessions in no earlier public dataset, per source; licences of the collected sources.
Writes JSON to argv[1] and prints it.
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OURC = f"read_parquet(['{V}/conversations/entire/*.parquet', '{V}/conversations/swe-chat-v2/*.parquet'], union_by_name=true)"
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
PB = "('correction', 'rejection', 'failure_report')"
OUT = sys.argv[1]

con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    res[name] = rows
    print(f"== {name}: {len(rows)} rows", file=sys.stderr, flush=True)
    return rows


# --- 1. SWE-chat's labelled prompts, classified by our rows for the same transcript entry
con.execute(f"""
create temp table sc as
select session_id, source_entry_uuid as u, coalesce(content_block_index, 0) as b, agent, is_conversational,
       is_continuation, prompt_pushback as pp, (prompt_pushback in {PB}) as is_pb,
       left(content, 160) as head, length(content) as len
from read_parquet('{SC}')
where turn_type = 'user_prompt' and prompt_pushback is not null""")
q("sc_totals", """select agent, count(*) n, sum(is_pb::int) n_pb, count(distinct session_id) sessions,
                         sum((u is null)::int) no_uuid from sc group by all order by n desc""")
con.execute(f"""
create temp table our as
select session_id, source_entry_uuid as u,
       bool_or(typed_by_developer) as any_typed,
       bool_or(prompt_pushback is not null) as holds_label,
       string_agg(distinct kind, ',' order by kind) as kinds,
       string_agg(distinct actor, ',' order by actor) as actors,
       bool_or(pushback) as our_pb, bool_or(pushback is not null) as our_labelled
from {OURC}
where source_entry_uuid in (select distinct u from sc where u is not null)
group by all""")
con.execute("""
create temp table cls as
select sc.*, our.any_typed, our.holds_label, our.kinds, our.actors, our.our_pb, our.our_labelled,
       case when our.u is null then 'entry not in our rows'
            when our.any_typed then 'typed by a person'
            else 'not typed: ' || coalesce(our.actors, '?') end as cls
from sc left join our on sc.session_id = our.session_id and sc.u = our.u""")
q("cls_all", """select cls, count(*) n, sum(is_pb::int) n_pb, round(100.0 * sum(is_pb::int) / count(*), 1) pb_pct
                from cls group by all order by n desc""")
q("cls_claude_code", """select cls, count(*) n, sum(is_pb::int) n_pb, round(100.0 * sum(is_pb::int) / count(*), 1) pb_pct
                        from cls where agent ilike '%claude%' group by all order by n desc""")
q("cls_kinds", """select kinds, actors, count(*) n, sum(is_pb::int) n_pb from cls
                  where any_typed is not null and not any_typed group by all order by n desc limit 40""")
q("pb_rate_claude_code", """select
      count(*) n_all, round(100.0 * avg(is_pb::int), 1) pb_all,
      count(*) filter (where any_typed) n_typed, round(100.0 * avg(is_pb::int) filter (where any_typed), 1) pb_typed,
      count(*) filter (where any_typed is not null and not any_typed) n_not_typed,
      round(100.0 * avg(is_pb::int) filter (where any_typed is not null and not any_typed), 1) pb_not_typed,
      count(*) filter (where any_typed is null) n_unmatched,
      round(100.0 * avg(is_pb::int) filter (where any_typed is null), 1) pb_unmatched
    from cls where agent ilike '%claude%'""")
for c in ("not typed", "entry not in our rows"):
    q(f"sample_{c.replace(' ', '_')}", f"""select agent, pp, len, head from cls where cls like '{c}%'
                                          order by hash(session_id || coalesce(u, '') || b::varchar) limit 60""")

# --- 2. our pushback against SWE-chat's on typed messages both labelled (one row per entry)
q("agreement_typed", """select is_pb as swechat_pushback, our_pb as our_pushback, count(*) n
                        from cls where any_typed and our_labelled group by all order by 1, 2""")

# --- 3. novelty and licences
q("kept_by_source", f"""select source, count(*) filter (where duplicate_of is null) kept, count(*) all_rows,
                               count(distinct repo) filter (where duplicate_of is null) repos
                        from {SESS} group by all order by kept desc""")
q("dup_links", f"""select d.source as copy_source, k.source as kept_source, count(*) n
                   from {SESS} d join {SESS} k on d.duplicate_of = k.session_key
                   where d.duplicate_of is not null group by all order by n desc""")
q("new_sessions", f"""with s as (select session_key, source, duplicate_of from {SESS}),
    earlier as (select distinct duplicate_of k from s
                where duplicate_of is not null and source in ('swe-chat-v2', 'dataclaw', 'openhands-feedback'))
    select source, count(*) kept,
           count(*) filter (where session_key not in (select k from earlier)) not_in_earlier_dataset
    from s where duplicate_of is null and source in ('entire', 'specstory', 'aider') group by all""")
q("also_in_sample", f"""select source, also_in, count(*) n from {SESS} where duplicate_of is null and also_in is not null
                        group by all order by n desc limit 20""")
q("aider_top_repos", f"""select repo, count(*) n from {SESS} where source = 'aider' and duplicate_of is null
                         group by all order by n desc limit 5""")
q("licences", f"""select source, coalesce(repo_license, '(none)') lic, count(*) sessions, count(distinct repo) repos
                  from {SESS} where duplicate_of is null and source in ('entire', 'specstory', 'aider')
                  group by all order by source, sessions desc""")
q("started", f"""select source, min(started_at) first, max(started_at) last,
                        count(*) filter (where started_at >= '2026-10-01') since_oct1
                 from {SESS} where duplicate_of is null group by all""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if not k.startswith("sample_")}, indent=1, default=str))
