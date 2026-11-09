"""What the release's name and e-mail masking leaves (how many names the masking misses,
2026-11-06). Read-only on the released tier A.

python names_left.py RELEASE_A OUT_DIR [--per-stratum 3000] [--seed e1-names7-2026-11-06] [--procs 8] [--cap 200000]

RELEASE_A: the released tier A (a-names/), as published. Message rows (main and helper-agent tables, not queued
messages' delivered copies, not empty) are drawn per stratum of who wrote them: what a person typed, a person's other
entries, the agent's text, tool calls and output, the harness, other agents. In each drawn row:
- names: the masking's own finder (collect.names.names_in: Presidio's PERSON spans, spaCy en_core_web_lg, score 0.85
  or more, shaped like a full name, not a technical word), run on the whole row (the masking runs it on typed rows
  only and masks what it finds wherever it occurs in the session; a name only in an agent's or a tool's text is left
  by design);
- e-mail-like strings: the masking's own pattern (collect.scan.EMAIL), less the addresses it keeps on purpose
  (no-reply, git@, example domains, file names); the copied datasets keep their publishers' own handling.
Writes candidates.jsonl (one line per distinct string per row: stratum, source, agent, the string, its kind, whether
it is on the masking's deny list, and the text around its first place, 300 characters each side) and draw7.json (rows
per stratum in the tier and in the draw, rows with a candidate, rows cut at --cap characters).
A person then reads the candidates (cards7): a private person's name or address, a public figure's, or not a person's.
"""
import argparse
import json
import os
import re
from collections import Counter
from multiprocessing import Pool

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("release_a")
ap.add_argument("out_dir")
ap.add_argument("--per-stratum", type=int, default=3000)
ap.add_argument("--seed", default="e1-names7-2026-11-06")
ap.add_argument("--procs", type=int, default=8)
ap.add_argument("--cap", type=int, default=200_000)
ap.add_argument("--limit-files", type=int, default=0, help="a dry run on the first N files of each table")
args = ap.parse_args()

STRATA = {
    "typed": "actor = 'developer' AND coalesce(typed_by_developer, false)",
    "person other": "actor = 'developer' AND NOT coalesce(typed_by_developer, false)",
    "agent": "actor = 'agent'",
    "tool": "actor = 'tool'",
    "harness": "actor = 'harness'",
    "peer": "actor = 'peer'",
}
A = args.release_a.rstrip("/")
files = []
for table in ("conversations", "conversations_subagents"):
    got = sorted(f"{A}/{table}/{s}/{f}" for s in sorted(os.listdir(f"{A}/{table}"))
                 for f in sorted(os.listdir(f"{A}/{table}/{s}")) if f.endswith(".parquet"))
    files += got[:args.limit_files] if args.limit_files else got
assert files, "no message files"
con = duckdb.connect()
con.execute(f"SET threads TO {args.procs}")
con.execute("SET preserve_insertion_order = false")
src = f"read_parquet({json.dumps(files)}, union_by_name=true, filename=true, file_row_number=true)".replace('"', "'")
base = f"""FROM {src} WHERE copy_of IS NULL AND content IS NOT NULL AND length(trim(content)) > 0"""
# one pass over the tier for the keys (no text kept), then the drawn rows' text in a second; a row is its file and
# its position there (a turn id can hold several rows: content blocks). The draw hashes each file's path as given
# (DuckDB's filename), so it repeats only with RELEASE_A at the same path as the run of 2026-11-06; the rows it drew
# are saved in candidates.jsonl (session_key, turn_id), and the person's cards and their scoring read that file.
case = "CASE " + " ".join(f"WHEN {w} THEN '{n}'" for n, w in STRATA.items()) + " ELSE NULL END"
con.execute(f"""CREATE TABLE k AS SELECT {case} AS stratum, coalesce(actor, '(none)') AS actor, source, session_key,
                turn_id, filename, file_row_number, md5('{args.seed}' || '|' || filename || '|' || file_row_number) AS h {base}""")
pop = {}
for n, src_, c in con.execute("SELECT stratum, source, count(*) FROM k WHERE stratum IS NOT NULL GROUP BY 1, 2").fetchall():
    pop.setdefault(n, {})[src_] = c
pop = {n: pop.get(n, {}) for n in STRATA}
other = con.execute("SELECT actor, count(*) FROM k WHERE stratum IS NULL GROUP BY 1").fetchall()
con.execute(f"""CREATE TABLE pick AS SELECT stratum, filename, file_row_number, h FROM k WHERE stratum IS NOT NULL
                QUALIFY row_number() OVER (PARTITION BY stratum ORDER BY h) <= {args.per_stratum}""")
dupe = con.execute("SELECT count(*) - count(DISTINCT (filename, file_row_number)) FROM pick").fetchone()[0]
assert dupe == 0, dupe   # one row per file position
rows = con.execute(f"""SELECT p.stratum, c.session_key, c.turn_id, c.source, c.agent, c.actor, c.kind, c.content, c.filename
                       FROM {src} c JOIN pick p ON c.filename = p.filename AND c.file_row_number = p.file_row_number
                       ORDER BY p.stratum, p.h""").fetchall()
n_pick = con.execute("SELECT count(*) FROM pick").fetchone()[0]
assert len(rows) == n_pick, (len(rows), n_pick)   # every drawn row read once
print("drawn", Counter(r[0] for r in rows), flush=True)

_FINDER = None


def _init():
    global _FINDER
    from collect.names import presidio_finder
    _FINDER = presidio_finder()


def look(r):
    from collect.names import names_in, deny_list
    from collect.scan import EMAIL, _keep_email
    stratum, key, tid, source, agent, actor, kind, content, fn = r
    cut = len(content) > args.cap
    text = content[:args.cap]
    out = []
    for span in sorted(names_in(text, _FINDER)):
        out.append(("name", span, text.find(span)))
    seen = set()
    for m in EMAIL.finditer(text):
        if _keep_email(m.group(1), m.group(2)) or m.group(0) in seen:   # local part, domain: as the masking keeps them
            continue
        seen.add(m.group(0))
        out.append(("email", m.group(0), m.start()))
    res = []
    for k, s, at in out:
        at = max(at, 0)
        res.append({"stratum": stratum, "source": source, "agent": agent, "actor": actor, "kind_row": kind,
                    "session_key": key, "turn_id": tid, "table": "helpers" if "subagents" in fn else "main",
                    "kind": k, "string": s, "on_deny_list": s in DENY, "at": at,
                    "before": text[max(0, at - 300):at], "after": text[at + len(s):at + len(s) + 300]})
    return stratum, cut, res


from collect.names import deny_list  # noqa: E402  (the deny list the masking used: names left on purpose)
DENY = deny_list()
with Pool(args.procs, initializer=_init) as pool:
    results = pool.map(look, rows, chunksize=20)
os.makedirs(args.out_dir, exist_ok=True)
with_cand, cut_rows, cands = Counter(), Counter(), []
for stratum, cut, res in results:
    cut_rows[stratum] += cut
    if res:
        with_cand[stratum] += 1
    cands += res
with open(os.path.join(args.out_dir, "candidates.jsonl"), "w") as f:
    for c in cands:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
meta = {"seed": args.seed, "per_stratum": args.per_stratum, "cap_chars": args.cap, "files": len(files),
        "deny_list_size": len(DENY),
        "strata": {s: {"rows_in_tier": sum(pop[s].values()), "rows_by_source": pop[s],
                       "drawn": sum(1 for r in rows if r[0] == s), "rows_with_candidate": with_cand[s],
                       "rows_cut": cut_rows[s],
                       "candidates": dict(Counter(c["kind"] for c in cands if c["stratum"] == s))} for s in STRATA},
        "rows_in_no_stratum": dict(other), "picked_keys": n_pick, "rows_read": len(rows),
        "duplicate_keys_in_pick": dupe}
json.dump(meta, open(os.path.join(args.out_dir, "draw7.json"), "w"), indent=1)
print(json.dumps(meta, indent=1))
