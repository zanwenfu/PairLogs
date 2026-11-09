"""Read-only: a stratified sample of our own user-side rows for the human check of the typed mark (version 2026-10-11).

Per source: rows the pipeline marked typed, and user-role rows it marked not typed (one row per session at most,
copies of queued messages skipped). Writes parquet to argv[1].
"""
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
CONV = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"
OUT = sys.argv[1]
TYPED = {"entire": 12, "specstory": 12, "aider": 8, "dataclaw": 12, "openhands-feedback": 6, "swe-chat-v2": 10}
NOT_TYPED = {"entire": 6, "specstory": 4, "aider": 3, "dataclaw": 5, "openhands-feedback": 2, "swe-chat-v2": 4}

con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
quota = " or ".join([f"(source = '{s}' and typed and r <= {n})" for s, n in TYPED.items()]
                    + [f"(source = '{s}' and not typed and r <= {n})" for s, n in NOT_TYPED.items()])
con.execute(f"""copy (
  select source, session_key, turn_number, agent, typed, actor, kind, length(content) len, left(content, 1500) head
  from (
    select *, row_number() over (partition by source, typed order by hash(session_key || '#20261024')) r
    from (
      select source, session_key, turn_number, agent, typed_by_developer as typed, actor, kind, content,
             row_number() over (partition by session_key, typed_by_developer order by hash(turn_number * 7919)) per_session
      from {CONV}
      where role = 'user' and copy_of is null and content is not null and length(trim(content)) > 0)
    where per_session = 1)
  where {quota}
) to '{OUT}' (format parquet)""")
print(con.execute(f"select source, typed, count(*) from read_parquet('{OUT}') group by all order by 1, 2").fetchall())
