"""Draw the second tier-B rebuild test's sample (2026-11-04, the strict review: the first drew small repositories
only): a few LARGER whole repositories, stratified by source.

- Entire: 4 repositories whose checkpoints live in the repository itself (one elsewhere can hold thousands of other
  sessions), with 100 MB to 1.5 GB of tier-B session files: the one with the most sessions, and 3 more at random
  (seeded) among the rest.
- SpecStory: the 3 with the most tier-B sessions.
- Aider: the 1 with the most tier-B sessions, other than the one benchmark repository of 5,356 runs.
The fetch is the repositories' own data at Duke as the build fetched it (run2/data/raw/<source>/<owner>__<name>),
summed to check the cap. Writes OUT/repos.txt (for --only-file) and OUT/sample.parquet (the sampled pointers, no
text) and prints the draw.

python sample_large.py POINTERS.parquet RAW_DIR OUT_DIR [SEED] [--list]
"""
import random
import subprocess
import sys
from pathlib import Path

import duckdb

args = [a for a in sys.argv[1:] if a != "--list"]
LIST = "--list" in sys.argv
POINTERS, RAW, OUT = args[0], Path(args[1]), Path(args[2])
SEED = args[3] if len(args) > 3 else "rebuild-large-20261104"
BENCH = "paraemsi/Reducing-violations-paper-data"
MAX_FETCH = 6e9

c = duckdb.connect()
c.execute(f"create view p as select * from read_parquet('{POINTERS}')")


def repos(src):
    return c.execute("""
        select repo, count(*) as n, sum(list_sum(list_transform(files, lambda f: f.bytes))) as b,
               bool_and(split_part(checkpoint_pk, '#', 1) = repo) as same_place
        from p where source = ? and repo is not null group by repo order by n desc, repo""", [src]).fetchall()


def on_disk(src, repo):
    d = RAW / src / repo.replace("/", "__")
    if not d.exists():
        return None
    out = subprocess.run(["du", "-sb", str(d)], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


if LIST:
    for src in ("entire", "specstory", "aider"):
        print(src)
        for repo, n, b, same in repos(src)[:15]:
            print(f"   {n:5} sessions {b / 1e6:9.1f} MB  same place {same}  on disk {(on_disk(src, repo) or 0) / 1e6:9.1f} MB")
    sys.exit(0)

picked = []
ent = [r for r in repos("entire") if r[3] and 100e6 <= r[2] <= 1.5e9]
first, rest = ent[0], ent[1:]
random.Random(f"{SEED}:entire").shuffle(rest)
picked += [("entire",) + first] + [("entire",) + r for r in rest[:3]]
picked += [("specstory",) + r for r in repos("specstory")[:3]]
picked += [("aider",) + next(r for r in repos("aider") if r[0] != BENCH)]
fetch = 0
print(f"seed {SEED}; Entire eligible {len(ent)}")
for src, repo, n, b, same in picked:
    d = on_disk(src, repo)
    fetch += d or 0
    print(f"{src:9} {n:5} sessions, {b / 1e6:8.1f} MB of session files, {(d or 0) / 1e6:8.1f} MB fetched by the build")
print(f"{len(picked)} repositories, {sum(r[2] for r in picked)} sessions, {fetch / 1e9:.2f} GB fetched by the build")
if fetch > MAX_FETCH:
    sys.exit(f"refused: {fetch / 1e9:.2f} GB is over the {MAX_FETCH / 1e9:.0f} GB cap")
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "repos.txt").write_text("".join(f"{r}\n" for r in sorted({r for _, r, *_ in picked})))
c.execute("create table pick (source varchar, repo varchar)")
c.executemany("insert into pick values (?, ?)", [(s, r) for s, r, *_ in picked])
c.execute(f"copy (select p.* from p join pick using (source, repo)) to '{OUT / 'sample.parquet'}' (format parquet)")
print("wrote", OUT / "repos.txt", OUT / "sample.parquet")
