"""Read-only: m5's matching of SWE-chat v2's labelled user prompts to who wrote them (VERSION_DIR), kept per prompt
(no text) and read for the paper's use case: SWE-chat's own published measures on all prompts and on typed ones
only (prompts, pushback per turn and per prompt, intents, prompts per session), the agent gap with intervals that
resample repositories (2,000 draws), without the 5 largest repositories, and under our own pushback label.
argv: OUT.json PROMPTS.parquet. m5's own description follows.

m5: the firm version of "who wrote SWE-chat v2's labelled user prompts" (version 2026-10-11).

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
import os
import sys

import duckdb

V = os.environ.get("VERSION_DIR", "/path/to/pairlogs-data/2026-10-11")
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OURC = (f"read_parquet(['{V}/conversations/entire/*.parquet', '{V}/conversations/swe-chat-v2/*.parquet', "
        f"'{V}/conversations_subagents/entire/*.parquet', '{V}/conversations_subagents/swe-chat-v2/*.parquet'], "
        "union_by_name=true, filename=true)")
PB = "('correction', 'rejection', 'failure_report')"
K1 = "left(trim(lower(regexp_replace(left({c}, 600), '\\s+', ' ', 'g'))), 80)"
TS = "regexp_replace({c}, '^\\s*<timestamp>[^<]{{0,200}}</timestamp>\\s*', '')"
NORM = ("lower(regexp_replace(regexp_replace(left({c}, {n}), '\\[REDACTED\\]|<[A-Z][A-Z_]{{2,}}>', '', 'g'), "
        "'[\\s\\p{{P}}\\p{{S}}]+', '', 'g'))")
OUT, KEEP = sys.argv[1], sys.argv[2]
RULES = [
    ("program", "codex environment or instructions", r"^(<environment_context>|# agents\.md instructions|<instructions>|<user_instructions>|<permissions instructions>|<collaboration_mode>|<recommended_plugins>|<skills?>|<skills_instructions>|<apps_instructions>|# in app browser:)"),
    ("program", "a transcript or tool output", r"^(\[\d+\] (tool|assistant|user|developer|system)\b|\{ \"command\": \[|🏁 script executed|planned action json|>>> |the codex agent has requested|reviewed codex session id|assess the exact planned action|some conversation entries were omitted|the next image is untrusted)"),
    ("person action", "an image or file attachment", r"^(<image\b|</image>|\[image|# files mentioned by the user)"),
    ("person action", "an interrupt", r"^(<turn_aborted>|\[request interrupted)"),
    ("program", "claude code harness", r"^(<command-name>|<command-message>|<local-command|caveat: the messages below|this session is being continued|<system-reminder>|<bash-(input|stdout|stderr)>|<ide_|<task-notification>|base directory for this skill|implement the following plan|another claude session sent a message|<teammate-message)"),
    ("program", "another leading xml tag", r"^<[a-z_][a-z0-9_-]*( [^>]*)?>"),
]

con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
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
select row_number() over () as id, session_id, repo_id, prompt_intent, turn_number,
       conversation_turn_number as cturn, agent,
       prompt_pushback as pp, (prompt_pushback in {PB}) as is_pb,
       {K1.format(c=TS.format(c='content'))} as k1, left({NORM.format(c=TS.format(c='content'), n=2000)}, 60) as k2,
       length(content) as len, left(content, 1500) as head
from read_parquet('{SC}') where turn_type = 'user_prompt' and prompt_pushback is not null""")
con.execute(f"""
create temp table our as
select session_id, role, typed_by_developer as typed, actor, kind, pushback as our_pb,
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
       bool_or({person_action('o')}) as action, string_agg(distinct o.actor || ':' || o.kind, ',') as what,
       bool_or(o.our_pb) filter (where o.typed) as our_pb
from sc join our o on o.session_id = sc.session_id and (o.k1 = sc.k1 or (length(sc.k2) >= 8 and o.k2 = sc.k2))
where o.role = 'user' and not o.sub group by sc.id""")
# s3: contained in a longer user-role row
con.execute(f"""
create temp table m3 as
select sc.id, bool_or(o.typed) as typed, bool_or({person_action('o')}) as action,
       string_agg(distinct o.actor || ':' || o.kind, ',') as what,
       bool_or(o.our_pb) filter (where o.typed) as our_pb
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
  coalesce(m12.what, m3.what, m4.what, {rule_why}) as what,
  coalesce(m12.our_pb, m3.our_pb) as our_pb
from sc left join m12 on sc.id = m12.id left join m3 on sc.id = m3.id left join m4 on sc.id = m4.id""")
# ---- m16: kept per prompt (keys and labels, no text), then the use case's measures ----
con.execute(f"""copy (select id, session_id, repo_id, agent, coalesce(cturn, turn_number) as t, cls, is_pb, pp,
                             prompt_intent, our_pb from cls) to '{KEEP}' (format parquet)""")
TYPED = "cls = 'typed by a person'"
q("prompts", f"""select count(*) n_all, count(*) filter (where {TYPED}) n_typed, count(distinct session_id) sessions,
                        count(distinct session_id) filter (where {TYPED}) sessions_with_typed from cls""")
con.execute("""create temp table turns as
select session_id, any_value(repo_id) repo_id, coalesce(cturn, turn_number) t, any_value(agent) agent,
       bool_or(cls = 'typed by a person') typed, bool_or(cls = 'program') and not bool_or(cls <> 'program') program,
       bool_or(is_pb) is_pb, bool_or(is_pb) filter (where cls = 'typed by a person') is_pb_typed,
       bool_or(our_pb) filter (where cls = 'typed by a person') our_pb
from cls group by session_id, coalesce(cturn, turn_number)""")
q("pushback", f"""select 'per prompt' unit, count(*) n_all, round(100.0 * avg(is_pb::int), 1) pb_all,
                         count(*) filter (where {TYPED}) n_typed,
                         round(100.0 * avg(is_pb::int) filter (where {TYPED}), 1) pb_typed from cls
                  union all
                  select 'per turn', count(*), round(100.0 * avg(is_pb::int), 1), count(*) filter (where typed),
                         round(100.0 * avg(is_pb_typed::int) filter (where typed), 1) from turns""")
q("intent", f"""select coalesce(prompt_intent, '(none)') intent, count(*) n_all,
                       round(100.0 * count(*) / sum(count(*)) over (), 1) pct_all,
                       count(*) filter (where {TYPED}) n_typed,
                       round(100.0 * count(*) filter (where {TYPED}) / sum(count(*) filter (where {TYPED})) over (), 1) pct_typed
                from cls group by 1 order by 2 desc""")
q("per_session", f"""select count(*) sessions, round(avg(n), 2) mean_all, median(n) median_all, round(avg(nt), 2) mean_typed,
                            median(nt) median_typed, round(100.0 * avg((nt = 0)::int), 1) pct_no_typed
                     from (select session_id, count(*) n, count(*) filter (where {TYPED}) nt from cls group by 1)""")

import random

AGENTS = ("Claude Code", "Codex")


def per_repo(sql):
    out = {}
    for repo, agent, *v in con.execute(sql).fetchall():
        out.setdefault(repo, {})[agent] = v
    return out


def rates(rows, repos):
    """Per agent, summed over the given repositories (with repeats): pushback on all, on typed, our label on typed;
    program share; and the gaps Claude Code minus Codex."""
    tot = {a: [0] * 8 for a in AGENTS}
    for r in repos:
        for a, v in rows.get(r, {}).items():
            tot[a] = [x + (y or 0) for x, y in zip(tot[a], v)]
    got = {}
    for a, (n, pb, nt, pbt, no, ob, npg, _) in tot.items():
        got[a] = {"pb_all": 100 * pb / n if n else None, "pb_typed": 100 * pbt / nt if nt else None,
                  "ours_typed": 100 * ob / no if no else None, "program": 100 * npg / n if n else None,
                  "n": n, "n_typed": nt}
    for k in ("pb_all", "pb_typed", "ours_typed"):
        x, y = got["Claude Code"][k], got["Codex"][k]
        got[f"gap_{k}"] = None if x is None or y is None else x - y
    return got


def study(name, sql):
    rows = per_repo(sql)
    repos = sorted(rows, key=str)
    point = rates(rows, repos)
    rng = random.Random(20261102)
    boot = [rates(rows, [rng.choice(repos) for _ in repos]) for _ in range(2000)]

    def ci(get):
        xs = sorted(v for v in (get(b) for b in boot) if v is not None)
        return [round(xs[int(0.025 * len(xs))], 1), round(xs[int(0.975 * len(xs)) - 1], 1)] if xs else None
    out = {"repos": len(repos)}
    for a in AGENTS:
        out[a] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in point[a].items()}
        for k in ("pb_all", "pb_typed", "ours_typed", "program"):
            out[a][k + "_ci"] = ci(lambda b, a=a, k=k: b[a][k])
    for k in ("gap_pb_all", "gap_pb_typed", "gap_ours_typed"):
        out[k] = None if point[k] is None else round(point[k], 1)
        out[k + "_ci"] = ci(lambda b, k=k: b[k])
    top = [r for r, _ in sorted(((r, sum(v[0] for v in rows[r].values())) for r in repos), key=lambda x: -x[1])[:5]]
    without = rates(rows, [r for r in repos if r not in top])
    out["without_top5"] = {a: {k: (round(v, 1) if isinstance(v, float) else v) for k, v in without[a].items()}
                           for a in AGENTS}
    out["without_top5"].update({k: (round(without[k], 1) if without[k] is not None else None)
                                for k in ("gap_pb_all", "gap_pb_typed", "gap_ours_typed")})
    out["top5_share_of_prompts"] = {a: round(100 * sum((rows[r].get(a) or [0])[0] for r in top)
                                             / max(1, point[a]["n"]), 1) for a in AGENTS}
    res[name] = out
    print(f"== {name}: done", file=sys.stderr, flush=True)


study("agent_gap_per_prompt", f"""
  select repo_id, agent, count(*), sum(is_pb::int), count(*) filter (where {TYPED}),
         sum(is_pb::int) filter (where {TYPED}), count(*) filter (where {TYPED} and our_pb is not null),
         sum(our_pb::int) filter (where {TYPED} and our_pb is not null), count(*) filter (where cls = 'program'), 0
  from cls where agent in {AGENTS} group by 1, 2""")
study("agent_gap_per_turn", f"""
  select repo_id, agent, count(*), sum(is_pb::int), count(*) filter (where typed), sum(is_pb_typed::int) filter (where typed),
         count(*) filter (where typed and our_pb is not null), sum(our_pb::int) filter (where typed and our_pb is not null),
         count(*) filter (where program), 0
  from turns where agent in {AGENTS} group by 1, 2""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
