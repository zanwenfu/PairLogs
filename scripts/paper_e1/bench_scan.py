"""Read-only, version 2026-10-11: kept sessions whose developer-typed text names a public benchmark task (for
pushback-review, 2026-10-26). Per pattern and source: sessions whose FIRST typed message matches, and sessions where
any typed message does. Writes the matches (key, source, agent, pattern, where, first 400 characters of the first
typed message, and 300 around the first match) to a local JSONL for reading by hand; prints the counts."""
import collections
import json
import re
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
out = sys.argv[1]
PATTERNS = {
    "SWE-bench instance id (owner__repo-1234)": r"\b[A-Za-z0-9][A-Za-z0-9_.-]*__[A-Za-z0-9][A-Za-z0-9_.-]*-\d{3,6}\b",
    "SWE-bench named": r"(?i)\bswe[- _]?bench\b|\bswebench\b",
    "SWE-bench style prompt (uploaded repository)": r"(?i)<uploaded_files>|I've uploaded a (python )?code repository",
    "Multi-SWE / SWE-Gym / SWE-smith": r"(?i)multi[- ]swe[- ]?bench|swe[- ]?gym|swe[- ]?smith",
    "Terminal-Bench": r"(?i)terminal[- _]?bench|\btbench\b|\bt-bench\b",
    "SWE-Lancer": r"(?i)swe[- _]?lancer",
    "HumanEval / MBPP": r"(?i)\bhuman[- _]?eval\b|\bmbpp\b",
    "Aider polyglot / Exercism": r"(?i)polyglot[- _]benchmark|aider[- _]polyglot|\bexercism\b",
    "LiveCodeBench / BigCodeBench / CodeContests": r"(?i)live[- ]?code[- ]?bench|big[- ]?code[- ]?bench|code[- ]?contests",
    "Other agent benchmarks (tau-bench, WebArena, GAIA, OSWorld, MLE-bench, RE-bench, KernelBench)":
        r"(?i)\btau[- _]?bench\b|τ-bench|\bweb[- ]?arena\b|\bgaia\b benchmark|\bos[- ]?world\b|mle[- ]?bench|re[- ]?bench\b|kernel[- ]?bench",
    "LeetCode / Advent of Code (fixed task, not a benchmark)": r"(?i)\bleetcode\b|advent of code|adventofcode",
}
RX = {k: re.compile(v) for k, v in PATTERNS.items()}
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
con.execute(f"""create temp table s as select session_key, source, agent from read_parquet('{V}/data/*/*.parquet',
  union_by_name=true) where duplicate_of is null""")
total = dict(con.execute("""select source, count(*) from s where session_key in (select distinct session_key from
  read_parquet('""" + V + """/conversations/*/*.parquet', union_by_name=true) where typed_by_developer and copy_of is null)
  group by 1""").fetchall())
meta = {k: (src, ag) for k, src, ag in con.execute("select session_key, source, agent from s").fetchall()}
cur = con.execute(f"""select session_key, turn_number, content from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
  where typed_by_developer and copy_of is null and content is not null order by session_key, turn_number""")
first, anyhit, firsttext = {}, collections.defaultdict(dict), {}
while True:
    rows = cur.fetchmany(50000)
    if not rows:
        break
    for key, turn, content in rows:
        if key not in meta:
            continue
        is_first = key not in firsttext
        if is_first:
            firsttext[key] = content
        for name, rx in RX.items():
            if name in anyhit[key]:
                continue
            m = rx.search(content)
            if m:
                a = max(0, m.start() - 150)
                anyhit[key][name] = {"first": is_first, "turn": turn, "match": m.group(0)[:80],
                                     "around": content[a:m.end() + 150]}
counts = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
with open(out, "w") as f:
    for key, hits in anyhit.items():
        if not hits:
            continue
        src, ag = meta[key]
        for name, h in hits.items():
            counts[name][src][1] += 1
            counts[name][src][0] += h["first"]
            f.write(json.dumps({"session_key": key, "source": src, "agent": ag, "pattern": name, **h,
                                "first_message": firsttext.get(key, "")[:400]}, ensure_ascii=False) + "\n")
print("kept sessions with typed text, by source:", json.dumps(total))
for name in PATTERNS:
    c = counts.get(name, {})
    print(f"{name}: first message {sum(v[0] for v in c.values())}, any typed message {sum(v[1] for v in c.values())}; "
          + ", ".join(f"{s} {v[0]}/{v[1]}" for s, v in sorted(c.items(), key=lambda x: -x[1][1])))
