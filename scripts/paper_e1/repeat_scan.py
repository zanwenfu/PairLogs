"""Read-only, version 2026-10-11: typed messages of 40+ characters repeated word for word in another kept session,
leaving out slash commands and refused-tool notes (fixed strings by nature). Counts the extra copies, how many sit in
sessions of the same repository, and prints the 25 most repeated texts (first 120 characters)."""
import collections
import hashlib

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
meta = {k: (s, a, r) for k, s, a, r in con.execute(f"""select session_key, source, agent, repo from
  read_parquet('{V}/data/*/*.parquet', union_by_name=true) where duplicate_of is null""").fetchall()}
where, text, total = collections.defaultdict(set), {}, 0
cur = con.execute(f"""select session_key, content from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
  where typed_by_developer and copy_of is null and content is not null""")
while True:
    rows = cur.fetchmany(50000)
    if not rows:
        break
    for key, content in rows:
        if key not in meta:
            continue
        total += 1
        c = " ".join(content.split())
        if len(c) < 40 or c.startswith(("<command-name>", "<command-message>", "The user doesn't want to proceed")):
            continue
        h = hashlib.sha1(c.encode()).hexdigest()[:16]
        where[h].add(key)
        text.setdefault(h, c[:120])
extra = sum(len(ks) - 1 for ks in where.values() if len(ks) > 1)
same_repo = sum(len(ks) - len({meta[k][2] for k in ks}) for ks in where.values() if len(ks) > 1)
print(f"typed messages {total}; repeated 40+ char texts (commands and refusals left out): {extra} extra copies "
      f"({100 * extra / total:.1f}%), of which within the same repository {same_repo}")
for h, ks in sorted(where.items(), key=lambda x: -len(x[1]))[:25]:
    print(f"{len(ks):5d} sessions, {len({meta[k][2] for k in ks}):4d} repos | {text[h]}")
