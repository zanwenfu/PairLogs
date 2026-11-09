"""Read-only, the firm version of "who wrote SWE-chat v2's labelled user prompts" (version 2026-10-11).

Each labelled prompt row is matched to our rows of the same session, in order, taking the first step that matches:
  s1 the first 80 characters (lower-cased, spaces collapsed), our user-role rows;
  s2 the first 60 letters and digits of any script, masking placeholders ([REDACTED], <PERSON>, ...) removed first;
  s3 the same 60 contained anywhere in one of our user-role rows (a block of a longer message);
  s4 s1 or s2 against our rows of any role, helper-agent rows included;
  s5 fixed start-of-text rules.
Classes: typed by a person; a person's action or attachment (interrupts, plan approvals, commands, images); program
text; unknown. Reported per row (SWE-chat's unit) and per turn, by agent, with SWE-chat's own pushback rate.
Writes JSON to argv[1]; writes a stratified sample for the human check to argv[2] (parquet).
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OURC = (f"read_parquet(['{V}/conversations/entire/*.parquet', '{V}/conversations/swe-chat-v2/*.parquet', "
        f"'{V}/conversations_subagents/entire/*.parquet', '{V}/conversations_subagents/swe-chat-v2/*.parquet'], "
        "union_by_name=true, filename=true)")
PB = "('correction', 'rejection', 'failure_report')"
K1 = "left(trim(lower(regexp_replace(left({c}, 600), '\\s+', ' ', 'g'))), 80)"
TS = "regexp_replace({c}, '^\\s*<timestamp>[^<]{{0,200}}</timestamp>\\s*', '')"
NORM = ("lower(regexp_replace(regexp_replace(left({c}, {n}), '\\[REDACTED\\]|<[A-Z][A-Z_]{{2,}}>', '', 'g'), "
        "'[\\s\\p{{P}}\\p{{S}}]+', '', 'g'))")
OUT, SAMPLE = sys.argv[1], sys.argv[2]
RULES = [
    ("program", "codex environment or instructions", r"^(<environment_context>|# agents\.md instructions|<instructions>|<user_instructions>|<permissions instructions>|<collaboration_mode>|<recommended_plugins>|<skills?>|<skills_instructions>|<apps_instructions>|# in app browser:)"),
    ("program", "a transcript or tool output", r"^(\[\d+\] (tool|assistant|user|developer|system)\b|\{ \"command\": \[|🏁 script executed|planned action json|>>> |the codex agent has requested|reviewed codex session id|assess the exact planned action|some conversation entries were omitted|the next image is untrusted)"),
    ("person action", "an image or file attachment", r"^(<image\b|</image>|\[image|# files mentioned by the user)"),
    ("person action", "an interrupt", r"^(<turn_aborted>|\[request interrupted)"),
    ("program", "claude code harness", r"^(<command-name>|<command-message>|<local-command|caveat: the messages below|this session is being continued|<system-reminder>|<bash-(input|stdout|stderr)>|<ide_|<task-notification>|base directory for this skill|implement the following plan|another claude session sent a message|<teammate-message)"),
    ("program", "another leading xml tag", r"^<[a-z_][a-z0-9_-]*( [^>]*)?>"),
]

con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    try:
        cur = con.execute(sql)
    except Exception as e:
        res[name] = [{"error": str(e)[:300]}]
        print(f"== {name}: ERROR {e}", file=sys.stderr, flush=True)
        return
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"== {name}: {len(res[name])} rows", file=sys.stderr, flush=True)


con.execute(f"""
create temp table sc as
select row_number() over () as id, session_id, turn_number, conversation_turn_number as cturn, agent,
       prompt_pushback as pp, (prompt_pushback in {PB}) as is_pb,
       {K1.format(c=TS.format(c='content'))} as k1, left({NORM.format(c=TS.format(c='content'), n=2000)}, 60) as k2,
       length(content) as len, left(content, 1500) as head
from read_parquet('{SC}') where turn_type = 'user_prompt' and prompt_pushback is not null""")
con.execute(f"""
create temp table our as
select session_id, role, typed_by_developer as typed, actor, kind,
       {K1.format(c=TS.format(c='content'))} as k1, left({NORM.format(c=TS.format(c='content'), n=2000)}, 60) as k2,
       case when role = 'user' then {NORM.format(c=TS.format(c='content'), n=20000)} end as full_norm,
       (filename like '%subagents%') as sub
from {OURC}
where content is not null and length(content) > 0 and session_id in (select distinct session_id from sc)""")
print("tables built", file=sys.stderr, flush=True)


def person_action(alias):
    return f"({alias}.actor = 'developer' and {alias}.kind in ('interrupt', 'plan', 'command', 'rejection'))"


# s1, s2: user-role rows, exact keys
con.execute(f"""
create temp table m12 as
select sc.id, min(case when o.k1 = sc.k1 then 1 else 2 end) as step, bool_or(o.typed) as typed,
       bool_or({person_action('o')}) as action, string_agg(distinct o.actor || ':' || o.kind, ',') as what
from sc join our o on o.session_id = sc.session_id and (o.k1 = sc.k1 or (length(sc.k2) >= 8 and o.k2 = sc.k2))
where o.role = 'user' and not o.sub group by sc.id""")
# s3: contained in a longer user-role row
con.execute(f"""
create temp table m3 as
select sc.id, bool_or(o.typed) as typed, bool_or({person_action('o')}) as action,
       string_agg(distinct o.actor || ':' || o.kind, ',') as what
from sc join our o on o.session_id = sc.session_id
where sc.id not in (select id from m12) and length(sc.k2) >= 20 and o.role = 'user' and not o.sub
  and contains(o.full_norm, sc.k2) group by sc.id""")
# s4: any role, helper agents included
con.execute(f"""
create temp table m4 as
select sc.id, string_agg(distinct o.role || ':' || o.actor || ':' || o.kind || case when o.sub then ':sub' else '' end, ',') as what
from sc join our o on o.session_id = sc.session_id and (o.k1 = sc.k1 or (length(sc.k2) >= 8 and o.k2 = sc.k2))
where sc.id not in (select id from m12) and sc.id not in (select id from m3) group by sc.id""")
rule_cls = "case " + " ".join(f"when regexp_matches(sc.k1, '{rx.replace(chr(39), chr(39) * 2)}') then '{c}'"
                              for c, _, rx in RULES) + " else 'unknown' end"
rule_why = "case " + " ".join(f"when regexp_matches(sc.k1, '{rx.replace(chr(39), chr(39) * 2)}') then '{w}'"
                              for _, w, rx in RULES) + " else null end"
con.execute(f"""
create temp table cls as
select sc.*,
  case when m12.id is not null then 's' || m12.step when m3.id is not null then 's3'
       when m4.id is not null then 's4' else 's5' end as step,
  case when coalesce(m12.typed, m3.typed) then 'typed by a person'
       when coalesce(m12.action, m3.action) then 'person action'
       when m12.id is not null or m3.id is not null or m4.id is not null then 'program'
       else {rule_cls} end as cls,
  coalesce(m12.what, m3.what, m4.what, {rule_why}) as what
from sc left join m12 on sc.id = m12.id left join m3 on sc.id = m3.id left join m4 on sc.id = m4.id""")
q("by_class", """select cls, count(*) n, round(100.0 * count(*) / sum(count(*)) over (), 1) pct,
                        round(100.0 * avg(is_pb::int), 1) pb_pct from cls group by all order by n desc""")
q("by_step", "select step, cls, count(*) n from cls group by all order by step, n desc")
q("by_what", "select cls, what, count(*) n from cls where cls <> 'typed by a person' group by all order by n desc limit 40")
q("by_agent", """select agent, count(*) n,
        round(100.0 * avg((cls = 'typed by a person')::int), 1) typed,
        round(100.0 * avg((cls = 'person action')::int), 1) action,
        round(100.0 * avg((cls = 'program')::int), 1) program,
        round(100.0 * avg((cls = 'unknown')::int), 1) unknown,
        round(100.0 * avg(is_pb::int), 1) pb_all,
        round(100.0 * avg(is_pb::int) filter (where cls = 'typed by a person'), 1) pb_typed
     from cls group by rollup(agent) order by n desc limit 12""")
# per turn: a turn is typed if any of its rows is typed, else unknown if any is unknown, else a person's action if any,
# else program; pushback if any of its rows is pushback
con.execute("""
create temp table turns as
select session_id, coalesce(cturn, turn_number) t, any_value(agent) agent,
  case when bool_or(cls = 'typed by a person') then 'typed by a person' when bool_or(cls = 'unknown') then 'unknown'
       when bool_or(cls = 'person action') then 'person action' else 'program' end as cls,
  bool_or(is_pb) as is_pb
from cls group by all""")
q("turns_by_agent", """select agent, count(*) n,
        round(100.0 * avg((cls = 'typed by a person')::int), 1) typed,
        round(100.0 * avg((cls = 'person action')::int), 1) action,
        round(100.0 * avg((cls = 'program')::int), 1) program,
        round(100.0 * avg((cls = 'unknown')::int), 1) unknown,
        round(100.0 * avg(is_pb::int), 1) pb_all,
        round(100.0 * avg(is_pb::int) filter (where cls = 'typed by a person'), 1) pb_typed
     from turns group by rollup(agent) order by n desc limit 12""")
q("sample_unknown", """select agent, pp, len, left(head, 140) head from cls where cls = 'unknown'
                       order by hash(id * 104729) limit 100""")
# stratified sample for the human check: 30 typed, 15 person action, 30 program, 25 unknown
con.execute(f"""copy (
  select * from (
    select id, session_id, agent, cls, what, pp, len, head,
           row_number() over (partition by cls order by hash(id * 2654435761)) r
    from cls)
  where (cls = 'typed by a person' and r <= 30) or (cls = 'person action' and r <= 15)
     or (cls = 'program' and r <= 30) or (cls = 'unknown' and r <= 25)
) to '{SAMPLE}' (format parquet)""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if not k.startswith("sample_")}, indent=1, default=str))
