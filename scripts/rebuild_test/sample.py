"""Draw the tier-B rebuild test's sample: whole repositories, stratified by source, small enough to fetch quickly.

Each source's repositories are drawn at random (seeded) among those whose kept tier-B sessions are few and small and,
for Entire, whose checkpoints live in the repository itself (a checkpoint repository elsewhere can hold thousands of
other sessions), until the source's target of sessions is reached. Writes OUT/repos.txt (for --only-file) and
OUT/sample.parquet (the sampled pointers, no text) and prints the draw.

python sample.py POINTERS.parquet OUT_DIR [SEED]
"""
import random
import sys
from pathlib import Path

import duckdb

POINTERS, OUT = sys.argv[1], Path(sys.argv[2])
SEED = sys.argv[3] if len(sys.argv) > 3 else "rebuild-20261104"
TARGET = {"entire": 90, "specstory": 70, "aider": 40}           # sessions per source, about 200 in all
LIMITS = {"entire": (2, 20, 20e6), "specstory": (2, 20, 5e6), "aider": (1, 15, 5e6)}  # sessions min/max, bytes max

c = duckdb.connect()
c.sql(f"create view p as select * from read_parquet('{POINTERS}')")
picked, total_bytes = [], 0
for src, target in TARGET.items():
    lo, hi, max_bytes = LIMITS[src]
    same_place = "and bool_and(split_part(checkpoint_pk, '#', 1) = repo)" if src == "entire" else ""
    rows = c.sql(f"""select repo, count(*) n, sum(list_sum(list_transform(files, lambda f: f.bytes))) b
                     from p where source = '{src}' and repo is not null group by repo
                     having count(*) between {lo} and {hi} and b <= {max_bytes} {same_place} order by repo""").fetchall()
    rng = random.Random(f"{SEED}:{src}")
    rng.shuffle(rows)
    got = 0
    for repo, n, b in rows:
        if got >= target:
            break
        picked.append((src, repo))
        got += n
        total_bytes += b
    print(f"{src}: {len(rows):,} repositories eligible; drew {sum(1 for s, _ in picked if s == src)} holding {got} sessions")
OUT.mkdir(parents=True, exist_ok=True)
repos = sorted({r for _, r in picked})
(OUT / "repos.txt").write_text("".join(f"{r}\n" for r in repos))
c.sql("create table pick (source varchar, repo varchar)")
c.executemany("insert into pick values (?, ?)", picked)
c.sql(f"""copy (select p.* from p join pick using (source, repo)) to '{OUT / "sample.parquet"}' (format parquet)""")
n = c.sql(f"select count(*) from read_parquet('{OUT / 'sample.parquet'}')").fetchall()[0][0]
print(f"seed {SEED}: {len(repos)} repositories, {n} sessions, {total_bytes / 1e6:.1f} MB of session files")
