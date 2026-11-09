"""Read-only, a version (argv: OUT.jsonl VERSION_DIR): every opening (first 60 characters, whitespace squeezed, digits and hex masked) of a
kept typed message shared by 10+ sessions, slash commands left out (typed by the rules), with per opening: sessions,
messages, by source, by agent, the share of its sessions with only that one typed message, and two examples (first
700 characters) from different sessions. Writes JSONL locally for reading by hand."""
import collections
import json
import re
import sys

import duckdb

V = sys.argv[2]
out = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
meta = {k: (s, a) for k, s, a in con.execute(f"""select session_key, source, agent from read_parquet('{V}/data/*/*.parquet',
  union_by_name=true) where duplicate_of is null""").fetchall()}
mask = lambda t: re.sub(r"[0-9a-f]{6,}|\d+", "#", " ".join(t.split()))[:60]
sessions, msgs, ex, ntyped = collections.defaultdict(set), collections.Counter(), collections.defaultdict(list), collections.Counter()
cur = con.execute(f"""select session_key, content from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
  where typed_by_developer and copy_of is null and content is not null""")
while True:
    rows = cur.fetchmany(50000)
    if not rows:
        break
    for key, content in rows:
        if key not in meta:
            continue
        ntyped[key] += 1
        o = mask(content)
        if len(o) < 25 or o.startswith(("<command-name>", "<command-message>")):
            continue
        if key not in sessions[o] and len(ex[o]) < 2:
            ex[o].append(content[:700])
        sessions[o].add(key)
        msgs[o] += 1
with open(out, "w") as f:
    for o, ks in sorted(sessions.items(), key=lambda x: -len(x[1])):
        if len(ks) < 10:
            break
        f.write(json.dumps({"opening": o, "sessions": len(ks), "messages": msgs[o],
                            "by_source": collections.Counter(meta[k][0] for k in ks),
                            "by_agent": collections.Counter(str(meta[k][1]) for k in ks),
                            "single_typed": round(sum(ntyped[k] == 1 for k in ks) / len(ks), 2),
                            "examples": ex[o]}, ensure_ascii=False) + "\n")
print(sum(1 for o, ks in sessions.items() if len(ks) >= 10), "openings shared by 10+ sessions")
