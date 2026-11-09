"""Read-only calibration, narrow version: PERSON spans that look like a full name, in typed messages only.

Sample: 1,500 typed messages and 1,500 agent text replies from tier A (version 2026-10-11 tiers). Reports how many rows
and characters would be masked per entity and score threshold, and writes 200 masked spans with context for a hand
read of precision. Writes JSON to argv[1].
"""
import json
import re
import random
import sys

import duckdb
from presidio_analyzer import AnalyzerEngine

A = "/path/to/pairlogs-work/release/2026-10-11/a"
OUT = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=4; SET memory_limit='24GB'")
rows = con.execute(f"""
    select 'typed' as what, source, session_key, turn_number, content
    from read_parquet('{A}/conversations/*/*.parquet', union_by_name=true)
    where typed_by_developer and copy_of is null and content is not null and length(content) between 5 and 20000
    order by hash(session_key || turn_number::varchar || '#p2') limit 6000""").fetchall()
print("sample", len(rows), file=sys.stderr, flush=True)

engine = AnalyzerEngine()
FULL_NAME = re.compile(r"^[A-Z][a-z\u00C0-\u017F'’]+(?:[ -][A-Z][a-z\u00C0-\u017F'’]+)+$")
found = []
for what, source, key, turn, text in rows:
    for r in engine.analyze(text=text, entities=["PERSON"], language="en"):
        if not FULL_NAME.match(text[r.start:r.end]):
            continue
        found.append(dict(what=what, source=source, key=key, turn=turn, entity=r.entity_type, score=round(r.score, 2),
                          span=text[r.start:r.end], context=text[max(0, r.start - 60):r.end + 60]))
chars = sum(len(t[4]) for t in rows)
res = {"rows": len(rows), "chars": chars, "by_threshold": {}}
for th in (0.3, 0.5, 0.7, 0.85):
    for ent in ("PERSON",):
        hit = [f for f in found if f["entity"] == ent and f["score"] >= th]
        res["by_threshold"][f"{ent}>={th}"] = {
            "spans": len(hit), "rows_touched": len({(f["key"], f["turn"]) for f in hit}),
            "masked_chars_per_1k": round(1000 * sum(len(f["span"]) for f in hit) / chars, 3)}
random.Random(20261025).shuffle(found)
res["sample_person"] = found[:200]
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res["by_threshold"], indent=1))
