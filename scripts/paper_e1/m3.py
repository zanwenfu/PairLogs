"""Read-only: what SWE-chat v2's labelled "user prompts" are in our rows, matching against EVERY row of the session.

Step 1 as m2 (user-role rows: typed / written by a program). Step 2: prompts left unmatched are matched against our
rows of any role (agent text, tool calls and output, notices). Step 3: the rest are sorted by fixed start-of-text
rules (program-made markers), else 'still unmatched'. Per agent, with SWE-chat's own pushback rate per class.
Writes JSON to argv[1].
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OURC = f"read_parquet(['{V}/conversations/entire/*.parquet', '{V}/conversations/swe-chat-v2/*.parquet'], union_by_name=true)"
PB = "('correction', 'rejection', 'failure_report')"
KEY = "left(trim(lower(regexp_replace(left({c}, 600), '\\s+', ' ', 'g'))), 80)"
OUT = sys.argv[1]
# start-of-text markers of text a program wrote (agent tools, harness, other agents); lower-cased, spaces collapsed
RULES = [
    ("codex environment/instructions", r"^(<environment_context>|# agents\.md instructions|<instructions>|<user_instructions>|<permissions instructions>|<collaboration_mode>|<recommended_plugins>|<skills?>|<skills_instructions>|<apps_instructions>)"),
    ("tool call or output in a transcript", r"^(\[\d+\] (tool|assistant|user|developer|system)\b|\{ \"command\": \[|🏁 script executed|planned action json|>>> |the codex agent has requested|reviewed codex session id|assess the exact planned action|some conversation entries were omitted)"),
    ("image or file attachment", r"^(<image\b|</image>|\[image|# files mentioned by the user)"),
    ("interrupt or abort marker", r"^(<turn_aborted>|\[request interrupted)"),
    ("claude code harness", r"^(<command-name>|<command-message>|<local-command|caveat: the messages below|this session is being continued|<system-reminder>|<bash-(input|stdout|stderr)>|<ide_|<task-notification>|base directory for this skill|implement the following plan|another claude session sent a message|<teammate-message)"),
    ("other leading xml tag", r"^<[a-z_][a-z0-9_-]*( [^>]*)?>"),
]

con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"== {name}: {len(res[name])} rows", file=sys.stderr, flush=True)


con.execute(f"""
create temp table sc as
select row_number() over () as id, session_id, agent, prompt_pushback as pp, (prompt_pushback in {PB}) as is_pb,
       {KEY.format(c='content')} as key, length(content) as len, left(content, 160) as head
from read_parquet('{SC}') where turn_type = 'user_prompt' and prompt_pushback is not null""")
con.execute(f"""
create temp table ourall as
select session_id, {KEY.format(c='content')} as key, role,
       bool_or(typed_by_developer) as typed, string_agg(distinct actor, ',' order by actor) as actors,
       string_agg(distinct kind, ',' order by kind) as kinds
from {OURC}
where content is not null and length(content) > 0 and session_id in (select distinct session_id from sc)
group by all""")
con.execute("""
create temp table u1 as   -- step 1: user-role rows
select sc.id, bool_or(o.typed) as typed from sc join ourall o on sc.session_id = o.session_id and sc.key = o.key
where o.role = 'user' group by sc.id""")
con.execute("""
create temp table u2 as   -- step 2: any other row
select sc.id, string_agg(distinct o.role || ':' || o.actors || ':' || o.kinds, ' | ') as what
from sc join ourall o on sc.session_id = o.session_id and sc.key = o.key
where sc.id not in (select id from u1) group by sc.id""")
rule_case = "case " + " ".join(f"when regexp_matches(sc.key, '{rx.replace(chr(39), chr(39) * 2)}') then '{name}'"
                               for name, rx in RULES) + " else 'still unmatched' end"
con.execute(f"""
create temp table cls as
select sc.*, case when u1.typed then 'typed by a person'
                  when u1.id is not null then 'program: user-role row not typed'
                  when u2.id is not null then 'program: our agent/tool/other row'
                  else 'rule: ' || {rule_case} end as cls, u2.what
from sc left join u1 on sc.id = u1.id left join u2 on sc.id = u2.id""")
q("cls_all", """select cls, count(*) n, round(100.0 * count(*) / sum(count(*)) over (), 1) pct,
                       round(100.0 * avg(is_pb::int), 1) pb_pct from cls group by all order by n desc""")
q("cls_cc_codex", """select agent, cls, count(*) n, round(100.0 * avg(is_pb::int), 1) pb_pct from cls
                     where agent in ('Claude Code', 'Codex') group by all order by agent, n desc""")
q("step2_what", """select what, count(*) n from cls where cls = 'program: our agent/tool/other row'
                   group by all order by n desc limit 25""")
q("summary_by_agent", """select agent, count(*) n,
        round(100.0 * avg((cls = 'typed by a person')::int), 1) typed_pct,
        round(100.0 * avg((cls like 'program:%' or (cls like 'rule:%' and cls <> 'rule: still unmatched'))::int), 1) program_pct,
        round(100.0 * avg((cls = 'rule: still unmatched')::int), 1) unknown_pct,
        round(100.0 * avg(is_pb::int), 1) pb_all,
        round(100.0 * avg(is_pb::int) filter (where cls = 'typed by a person'), 1) pb_typed
     from cls group by rollup(agent) order by n desc limit 10""")
q("sample_still_unmatched", """select agent, pp, len, head from cls where cls = 'rule: still unmatched'
                               order by hash(id * 104729) limit 100""")
q("sample_step2", """select agent, pp, what, head from cls where cls = 'program: our agent/tool/other row'
                     order by hash(id * 7919) limit 40""")
q("sample_other_xml", """select agent, pp, head from cls where cls = 'rule: other leading xml tag'
                         order by hash(id * 31) limit 40""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if not k.startswith("sample_")}, indent=1, default=str))
