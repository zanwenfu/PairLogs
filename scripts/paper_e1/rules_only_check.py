"""A known-answer test of the typed mark's text rules (the strict review of 11-06: the mark is checked by its author
alone; "hide those records and run only the fixed rules").

Where an agent records who wrote a user entry (Claude Code and Codex in Entire's logs), the released mark uses those
records with the text rules. SpecStory, Aider and DataClaw record no author: there the text rules decide alone. Here
every user-role row of Entire's kept Claude Code and Codex sessions (main conversation, not a queued message's
delivered copy) is told again by the text rules alone (collect.events.tell, the reader's own function), and compared
with the released mark. Read with care: the rules were written partly from these same logs, so this is the rules'
agreement with the records where both apply, not their accuracy on SpecStory's or Aider's own text; and the records'
mark is the reference, not a person's reading.

python rules_only_check.py VERSION_DIR OUT.json
Counts only, never text.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import duckdb

from collect import events as E

V, OUT = sys.argv[1:3]
con = duckdb.connect()
con.execute("SET threads=4")
agent = dict(con.execute(f"""SELECT session_key, agent FROM read_parquet('{V}/data/entire/*.parquet', union_by_name=true)
                             WHERE duplicate_of IS NULL AND agent IN ('Claude Code', 'Codex')""").fetchall())
assert agent, "no Claude Code or Codex session"
con.execute("CREATE TABLE k(session_key VARCHAR, agent VARCHAR)")
con.executemany("INSERT INTO k VALUES (?, ?)", list(agent.items()))
out = {"sessions": dict(Counter(agent.values())), "by_agent": {}}
cells = {a: Counter() for a in ("Claude Code", "Codex")}
why = {a: Counter() for a in ("Claude Code", "Codex")}
files = sorted(Path(V).glob("conversations/entire/*.parquet"))
for f in files:
    rows = con.execute(f"""SELECT k.agent, c.typed_by_developer, c.actor, c.kind, c.content
                           FROM read_parquet('{f}') c JOIN k USING (session_key)
                           WHERE c.role = 'user' AND c.copy_of IS NULL""").fetchall()
    for a, ref, actor, kind, content in rows:
        rule = bool(E.tell(content or "")[1])
        ref = bool(ref)
        cells[a][(rule, ref)] += 1
        if rule != ref:
            why[a][f"rules {'typed' if rule else 'not typed'}; records: {actor}/{kind}"] += 1
for a in cells:
    c = cells[a]
    tt, tf, ft, ff = c[(True, True)], c[(False, True)], c[(True, False)], c[(False, False)]
    n = tt + tf + ft + ff
    out["by_agent"][a] = {
        "user_rows": n, "both_typed": tt, "rules_not_typed_records_typed": tf, "rules_typed_records_not": ft,
        "both_not_typed": ff, "agreement_pct": round(100 * (tt + ff) / n, 2) if n else None,
        "rules_typed_precision_pct": round(100 * tt / (tt + ft), 2) if tt + ft else None,
        "rules_typed_recall_pct": round(100 * tt / (tt + tf), 2) if tt + tf else None,
        "typed_by_rules_over_records": round((tt + ft) / (tt + tf), 3) if tt + tf else None,
        "disagreements_by_records_actor_kind": dict(why[a].most_common(15))}
Path(OUT).write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out, indent=1))
