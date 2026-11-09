"""Read-only, counts only (2026-11-04, the strict review's asks), on version 2026-11-02 and its release:
1. SWE-chat's own turn types for user-role turns, and its labelled prompts by who wrote them (our table): how many of
   the prompts we call program text SWE-chat itself marks otherwise (its labelled prompts are all `user_prompt`).
2. Names left in tool output in tier A: tool-result rows holding a git `Author: First Last <...>` line, and how many
   distinct such names (never printed).
3. The datasheet's numbers that had no source: sessions by start year and with none; tier A rows by actor; typed rows
   without our pushback label and how many of them hold no text; agent names.
python facts_1104.py VERSION_DIR TIER_A TIER_B OUT.json
"""
import json
import sys

import duckdb

V, A, B, OUT = sys.argv[1:5]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='48GB'")
res = {}


def q(name, sql, params=None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(name, json.dumps(res[name], default=str)[:600], flush=True)


SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
q("swechat_user_turn_types", f"""select turn_type, count(*) as n, count(prompt_pushback) as labelled
    from read_parquet('{SC}') where role = 'user' group by 1 order by 2 desc""")
q("swechat_labelled_by_writer_and_type", f"""select w.written_by, s.turn_type, count(*) as n
    from read_parquet('{SC}') s join read_parquet('{B}/labels/swe_chat_prompts.parquet') w using (turn_id)
    group by 1, 2 order by 3 desc""")

CONV_A = f"read_parquet('{A}/conversations/*/*.parquet', union_by_name=true)"
AUTH = r"Author: [A-Z][a-z]+(?: [A-Z][a-z]+)+ <"
q("tierA_git_author_lines", f"""select source, count(*) as rows_with_author_line,
        count(distinct regexp_extract(content, '{AUTH}')) as distinct_names
    from {CONV_A} where kind = 'tool_result' and copy_of is null and regexp_matches(content, '{AUTH}')
    group by 1 order by 2 desc""")
q("tierA_rows_by_actor", f"""select actor, count(*) as n from {CONV_A} group by 1 order by 2 desc""")

SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
q("kept_by_start_year", f"""select coalesce(cast(year(cast(started_at as timestamp)) as varchar), 'none') as year,
        count(*) as n from {SESS} where duplicate_of is null group by 1 order by 1""")
CONV_V = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"
q("typed_rows_labels", f"""select (copy_of is null) as kept, count(*) as typed_rows,
        count(*) filter (where pushback is null) as no_label,
        count(*) filter (where pushback is null and (content is null or length(trim(content)) = 0)) as no_label_no_text
    from {CONV_V} where typed_by_developer group by 1""")
q("agent_names", f"""select count(distinct agent) as names from {SESS} where duplicate_of is null and agent is not null""")
json.dump(res, open(OUT, "w"), indent=1, default=str)
print("end")
