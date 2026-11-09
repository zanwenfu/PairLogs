"""Read-only: what SWE-chat v2's name masking (Presidio) replaced in its user prompts, seen against our copy of the same
messages, read from the raw transcripts (version 2026-10-11).

Counts its placeholders in labelled user prompts; for prompts holding <PRESIDIO_ANONYMIZED_PERSON>, finds our row of the same session and
transcript entry, aligns the two texts, and lists what each <PRESIDIO_ANONYMIZED_PERSON> stood for. Writes JSON to argv[1].
"""
import collections
import difflib
import json
import re
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OURC = f"read_parquet(['{V}/conversations/entire/*.parquet'], union_by_name=true)"
OUT = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}
res["placeholders"] = con.execute(f"""
  select tag, count(*) n from (
    select unnest(regexp_extract_all(content, '<[A-Z][A-Z_]{{2,}}>')) as tag
    from read_parquet('{SC}') where turn_type in ('user_prompt', 'assistant_response'))
  group by tag order by n desc limit 15""").fetchall()
pairs = con.execute(f"""
  with sc as (select session_id, source_entry_uuid u, content from read_parquet('{SC}')
              where turn_type in ('user_prompt', 'assistant_response') and content like '%<PRESIDIO_ANONYMIZED_PERSON>%' and source_entry_uuid is not null),
       ours as (select session_id, source_entry_uuid u, content from {OURC}
                where source_entry_uuid in (select u from sc) and content is not null)
  select sc.content, ours.content from sc join ours using (session_id, u)
  order by hash(sc.u) limit 3000""").fetchall()
stood = collections.Counter()
aligned = 0
for theirs, ours in pairs:
    if theirs.count("<PRESIDIO_ANONYMIZED_PERSON>") == 0:
        continue
    sm = difflib.SequenceMatcher(None, theirs, ours, autojunk=False)
    got = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace" and "<PRESIDIO_ANONYMIZED_PERSON>" in theirs[i1:i2]:
            got.append(ours[j1:j2].strip())
    if got:
        aligned += 1
        stood.update(g for g in got if 0 < len(g) <= 60)
res["prompts_with_person_matched"] = len(pairs)
res["prompts_aligned"] = aligned
res["what_person_stood_for"] = stood.most_common(150)
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k != "what_person_stood_for"}, indent=1, default=str))
print(res["what_person_stood_for"][:80])
