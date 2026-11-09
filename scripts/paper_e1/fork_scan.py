"""Read-only, version 2026-10-11: kept sessions that repeat another kept session's typed messages (forks, resumes or
copies the dedup keeps apart). Per kept session, its typed messages (copy_of null) as exact-text hashes in order.
(1) Messages of 40+ characters whose exact text is typed in 2+ kept sessions: the extra copies, by source and agent.
(2) Sessions whose first 3 typed messages equal another kept session's first 3: groups, sessions, by source and
agent, and how many of a session's typed messages are in its group's shared prefix. Writes groups locally."""
import collections
import hashlib
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
out = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
meta = {k: (s, a, r) for k, s, a, r in con.execute(f"""select session_key, source, agent, repo from
  read_parquet('{V}/data/*/*.parquet', union_by_name=true) where duplicate_of is null""").fetchall()}
seq = collections.defaultdict(list)
long_in = collections.defaultdict(set)
cur = con.execute(f"""select session_key, content from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
  where typed_by_developer and copy_of is null and content is not null order by session_key, turn_number""")
while True:
    rows = cur.fetchmany(50000)
    if not rows:
        break
    for key, content in rows:
        if key not in meta:
            continue
        h = hashlib.sha1(" ".join(content.split()).encode()).hexdigest()[:16]
        seq[key].append(h)
        if len(content.strip()) >= 40:
            long_in[h].add(key)
total_msgs = sum(len(v) for v in seq.values())
extra = collections.Counter()
for h, ks in long_in.items():
    if len(ks) > 1:
        for k in sorted(ks)[1:]:
            extra[(meta[k][0], meta[k][1])] += 1
print(f"kept sessions with typed messages {len(seq)}, typed messages {total_msgs}")
print(f"(1) 40+ char typed messages repeated in another kept session: {sum(extra.values())} extra copies "
      f"({100 * sum(extra.values()) / total_msgs:.1f}% of typed messages); top: {extra.most_common(8)}")
groups = collections.defaultdict(list)
for k, s in seq.items():
    if len(s) >= 3:
        groups[tuple(s[:3])].append(k)
multi = {g: ks for g, ks in groups.items() if len(ks) > 1}
in_groups = [k for ks in multi.values() for k in ks]
by = collections.Counter((meta[k][0], meta[k][1]) for k in in_groups)
shared_extra = 0
with open(out, "w") as f:
    for g, ks in sorted(multi.items(), key=lambda x: -len(x[1])):
        # the prefix every member shares
        n = 0
        while all(len(seq[k]) > n for k in ks) and len({seq[k][n] for k in ks}) == 1:
            n += 1
        shared_extra += n * (len(ks) - 1)
        f.write(json.dumps({"sessions": ks, "shared_prefix": n, "lengths": [len(seq[k]) for k in ks],
                            "source_agent": collections.Counter(f"{meta[k][0]}/{meta[k][1]}" for k in ks),
                            "repos": sorted({str(meta[k][2]) for k in ks})}) + "\n")
print(f"(2) groups sharing their first 3 typed messages: {len(multi)}; sessions in them {len(in_groups)} "
      f"({100 * len(in_groups) / len(seq):.1f}% of kept sessions with typed text); extra typed messages in shared "
      f"prefixes {shared_extra} ({100 * shared_extra / total_msgs:.1f}%)")
print("    by source/agent:", by.most_common(12))
print("    group sizes:", sorted(collections.Counter(len(ks) for ks in multi.values()).items())[:15])
