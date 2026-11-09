"""Read-only: what agents' logs file in the user's role, in our own data (version 2026-10-11), by agent and source.

Unit: one user-role row (a piece of a message) and one user-role entry (rows sharing a transcript entry); copies of
queued messages skipped; kept sessions only. Classes by the pipeline: typed by a person; a person's action
(developer actor, kind interrupt/plan/command/rejection); program (harness, agent, tool, peer). Writes JSON to argv[1].
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
CONV = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
OUT = sys.argv[1]
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


PRODUCT = """case
  when agent in ('Codex', 'Codex (scoped transcript export)') then 'Codex'
  when agent in ('Cursor', 'Cursor CLI') then 'Cursor'
  when agent in ('VS Code Copilot', 'copilotide', 'Copilot CLI') then 'GitHub Copilot'
  when agent in ('Pi', 'Oh My Pi') then 'Pi'
  when agent in ('Antigravity', 'Antigravity CLI') then 'Antigravity'
  when agent is null then '(not named)' else agent end"""
con.execute(f"""create temp table u as
select c.source, {PRODUCT} as product, c.session_key, c.source_entry_uuid, c.turn_number,
  case when c.typed_by_developer then 'typed by a person'
       when c.actor = 'developer' then 'person action'   -- a person's action that is not typed text: interrupts, plan approvals, commands, `!` shell input
       else 'program' end as cls, c.actor, c.kind
from {CONV} c
where c.role = 'user' and c.copy_of is null
  and c.session_key in (select session_key from {SESS} where duplicate_of is null)""")
for unit, sel in (("rows", "u"),
                  ("entries", """(select source, product, session_key,
                      case when bool_or(cls = 'typed by a person') then 'typed by a person'
                           when bool_or(cls = 'person action') then 'person action' else 'program' end as cls
                    from u group by source, product, session_key, coalesce(source_entry_uuid, turn_number::varchar))""")):
    q(f"{unit}_by_product", f"""select product, count(*) n,
          round(100.0 * avg((cls = 'typed by a person')::int), 1) typed,
          round(100.0 * avg((cls = 'person action')::int), 1) action,
          round(100.0 * avg((cls = 'program')::int), 1) program
        from {sel} x group by rollup(product) order by n desc limit 16""")
    q(f"{unit}_by_source", f"""select source, count(*) n,
          round(100.0 * avg((cls = 'typed by a person')::int), 1) typed,
          round(100.0 * avg((cls = 'person action')::int), 1) action,
          round(100.0 * avg((cls = 'program')::int), 1) program
        from {sel} x group by rollup(source) order by n desc""")
q("program_kinds", """select actor, kind, count(*) n from u where cls = 'program' group by all order by n desc limit 20""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
