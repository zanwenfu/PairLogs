"""Checks of a withdrawal (withdraw.py, then the work labels and line attribution split again by their own scripts)
against the release it was made from. Counts only, never text; exits 1 on any failure.

python verify_withdraw.py ORIGINAL_RELEASE NEW_RELEASE WITHDRAWN.tsv ORIGINAL_SNAPSHOT.json OUT.json

- the original release: every file as it was before the job (inode, size, time: ORIGINAL_SNAPSHOT.json, taken first);
- tier A: what left is exactly the withdrawn repositories' kept sessions and the copy rows from them or of them; every
  other row of every table (sessions, messages, helpers' messages, work labels, attribution) is the original's, in
  the original's order; a file with no row left out is still the original's file (the same inode);
- tier B: the original's pointers and labels as they were, first, then the moved sessions' only; the labels added
  are as many as the moved sessions' message rows in tier A and carry the same marks (typed_by_developer, actor,
  kind, pushback) row by row; the work labels and attribution rows that moved are tier A's own;
- every kept session once, in one tier; each changelog's counts and each repositories table match the files.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ORIG, NEW, WD, SNAP, OUT = sys.argv[1:6]
O, N = Path(ORIG), Path(NEW)
withdrawn = {line.split("\t")[1] for line in Path(WD).read_text().splitlines() if line.strip()}
fail: list[str] = []
got: dict = {}


def check(ok: bool, what: str) -> None:
    if not ok:
        fail.append(what)
        print("FAIL", what, flush=True)


def read(path: Path, columns=None) -> pa.Table:
    return pq.read_table(path, columns=columns)


def rows_of(paths, columns=None) -> list[dict]:
    out = []
    for p in paths:
        out += read(p, columns).to_pylist()
    return out


def isin(t: pa.Table, keys: set[str]) -> pa.Array:
    return pc.is_in(t["session_key"], value_set=pa.array(sorted(keys), type=t["session_key"].type))


# -- the original release did not change -----------------------------------------------------------------------------
snap = json.loads(Path(SNAP).read_text())
now = {str(p.relative_to(O)): [p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns]
       for p in sorted(O.rglob("*")) if p.is_file()}
check(now == snap, f"original release unchanged ({len(snap)} files)")
got["original_files"] = len(snap)

# -- tier A ----------------------------------------------------------------------------------------------------------
oa, na, ob, nb = O / "a-names", N / "a-names", O / "b", N / "b"
old_s = rows_of(sorted(oa.glob("data/*/*.parquet")), ["session_key", "repo", "duplicate_of"])
new_s = rows_of(sorted(na.glob("data/*/*.parquet")), ["session_key", "repo", "duplicate_of"])
old_keys, new_keys = {r["session_key"] for r in old_s}, {r["session_key"] for r in new_s}
drop = old_keys - new_keys
check(new_keys <= old_keys, "tier A gained no session row")
moved = {r["session_key"] for r in old_s if r["session_key"] in drop and r["duplicate_of"] is None}
copies = drop - moved
check(all(r["repo"] in withdrawn for r in old_s if r["session_key"] in moved), "every moved session is from a withdrawn repository")
check(all(r["repo"] in withdrawn or r["duplicate_of"] in moved for r in old_s if r["session_key"] in copies),
      "every copy row left out is from a withdrawn repository or of a moved session")
check(not any(r["repo"] in withdrawn or r["duplicate_of"] in moved for r in new_s), "tier A holds nothing from them")
got["tier_a"] = {"moved": len(moved), "copies_left_out": len(copies),
                 "kept_before": sum(1 for r in old_s if r["duplicate_of"] is None),
                 "kept_after": sum(1 for r in new_s if r["duplicate_of"] is None)}
rewritten = set()
for t in ("data", "conversations", "conversations_subagents"):
    removed = 0
    for f in sorted(oa.glob(f"{t}/*/*.parquet")):
        rel = f.relative_to(oa)
        g = na / rel
        if not pc.any(isin(read(f, ["session_key"]), drop)).as_py():
            check(g.exists() and g.stat().st_ino == f.stat().st_ino, f"{rel}: untouched file still the original's")
            continue
        old = read(f)
        exp = old.filter(pc.invert(isin(old, drop)))
        removed += len(old) - len(exp)
        rewritten.add(str(rel))
        if len(exp):
            check(g.exists() and read(g).equals(exp), f"{rel}: the original's rows less those left out, in order")
        else:
            check(not g.exists(), f"{rel}: emptied and removed")
    extra = {str(p.relative_to(na)) for p in na.glob(f"{t}/*/*.parquet")} - \
            {str(p.relative_to(oa)) for p in oa.glob(f"{t}/*/*.parquet")}
    check(not extra, f"tier A {t}: no new file")
    got["tier_a"][f"{t}_rows_left_out"] = removed
got["tier_a"]["files_rewritten_or_removed"] = len(rewritten)
MARKS = ("session_key", "task_pk", "turn_number", "typed_by_developer", "actor", "kind", "pushback")
moved_conv = {}
for t in ("conversations", "conversations_subagents"):
    moved_conv[t] = []
    for f in sorted(oa.glob(f"{t}/*/*.parquet")):
        k = read(f, ["session_key"])
        if pc.any(isin(k, moved)).as_py():
            names = pq.read_schema(f).names
            tab = read(f, [c for c in MARKS if c in names])
            moved_conv[t] += tab.filter(isin(tab, moved)).to_pylist()


def split_side(rel: str, key: str = "session_key") -> None:
    """A side table (one row per session): tier A's new rows are its old less the moved; tier B's new are its old
    and the moved sessions' rows from tier A, as sets of rows."""
    oA, nA, oB, nB = (read(p / rel).to_pylist() if (p / rel).exists() else [] for p in (oa, na, ob, nb))
    norm = lambda rs: sorted(json.dumps(r, sort_keys=True, default=str) for r in rs)
    check(norm(nA) == norm([r for r in oA if r[key] not in moved]), f"{rel}: tier A less the moved")
    check(norm(nB) == norm(oB + [r for r in oA if r[key] in moved]), f"{rel}: tier B plus the moved, as tier A had them")
    got.setdefault("side_tables", {})[rel] = {"moved_rows": sum(1 for r in oA if r[key] in moved),
                                              "tier_a": len(nA), "tier_b": len(nB)}


split_side("work_labels/sessions.parquet")
split_side("attribution/entire.parquet")

# -- tier B ----------------------------------------------------------------------------------------------------------
op, np_ = read(ob / "pointers.parquet"), read(nb / "pointers.parquet")
check(np_.slice(0, len(op)).equals(op), "tier B: the original's pointers first, as they were")
added = set(np_.slice(len(op))["session_key"].to_pylist())
check(added == moved and len(np_) == len(op) + len(moved), "tier B: then the moved sessions' pointers, once each")
check(np_.schema.names == op.schema.names, "tier B pointers: the same columns (no text)")
lab_added = Counter()
for t, keyc in (("conversations", ("session_key", "turn_number")),
                ("conversations_subagents", ("session_key", "task_pk", "turn_number"))):
    new_rows = []
    for g in sorted(nb.glob(f"labels/{t}/*/*.parquet")):
        f = ob / g.relative_to(nb)
        new = read(g)
        n_old = 0
        if f.exists():
            old = read(f)
            n_old = len(old)
            check(new.slice(0, n_old).equals(old), f"{g.relative_to(nb)}: the original's labels first, as they were")
        rest = new.slice(n_old).to_pylist()
        check(all(r["session_key"] in moved for r in rest), f"{g.relative_to(nb)}: added labels are moved sessions'")
        new_rows += rest
    lab_added[t] = len(new_rows)
    a_rows = {tuple(r[k] for k in keyc): r for r in moved_conv[t]}
    l_rows = {tuple(r[k] for k in keyc): r for r in new_rows}
    check(set(a_rows) == set(l_rows), f"labels {t}: one label row for each of tier A's rows of the moved sessions")
    same = sum(1 for k in set(a_rows) & set(l_rows)
               if all(a_rows[k].get(c) == l_rows[k].get(c) for c in ("typed_by_developer", "actor", "kind", "pushback")))
    check(same == len(a_rows), f"labels {t}: the same marks as tier A's rows ({same} of {len(a_rows)})")
    got.setdefault("labels_added", {})[t] = {"rows": len(new_rows), "same_marks_as_tier_a": same}

# -- every kept session once; changelogs and repositories match the files --------------------------------------------
a_kept = {r["session_key"] for r in new_s if r["duplicate_of"] is None}
b_kept = set(np_["session_key"].to_pylist())
o_all = {r["session_key"] for r in old_s if r["duplicate_of"] is None} | set(op["session_key"].to_pylist())
check(not (a_kept & b_kept) and (a_kept | b_kept) == o_all, "every kept session once, in one tier, none lost")
got["kept"] = {"tier_a": len(a_kept), "tier_b": len(b_kept), "all": len(a_kept | b_kept)}
ca, cb = json.loads((na / "changelog.json").read_text()), json.loads((nb / "changelog.json").read_text())
check(ca["counts"]["sessions"] == len(a_kept) and ca["counts"]["copies"] == len(new_s) - len(a_kept),
      "tier A changelog: sessions and copies")
for t in ("conversations", "conversations_subagents"):
    n = sum(pq.ParquetFile(f).metadata.num_rows for f in na.glob(f"{t}/*/*.parquet"))
    check(ca["counts"]["rows"][t] == n, f"tier A changelog: {t} rows")
    m = sum(pq.ParquetFile(f).metadata.num_rows for f in nb.glob(f"labels/{t}/*/*.parquet"))
    check(cb["labels"]["message_labels"]["rows"][t] == m, f"tier B changelog: {t} label rows")
check(cb["counts"]["sessions"] == len(np_), "tier B changelog: sessions")
repos_a = sorted({r["repo"] for r in new_s if r["repo"]})
repos_b = sorted({r for r in np_["repo"].to_pylist() if r})
ra, rb = read(na / "repositories.parquet").to_pylist(), read(nb / "repositories.parquet").to_pylist()
check([r["repo"] for r in ra] == repos_a and [r["repo"] for r in rb] == repos_b, "repositories tables: each tier's own")
types = {r["repo"]: r["owner_type"] for r in read(oa / "repositories.parquet").to_pylist() +
         read(ob / "repositories.parquet").to_pylist()}
check(all(types.get(r["repo"]) == r["owner_type"] for r in ra + rb), "owner types as the original release had them")
txt = (nb / "repositories.txt").read_text().split()
check(set((ob / "repositories.txt").read_text().split()) <= set(txt), "tier B repositories.txt: none lost")

got["pass"] = not fail
got["failures"] = fail
Path(OUT).write_text(json.dumps(got, indent=1) + "\n")
print(json.dumps(got, indent=1))
sys.exit(0 if not fail else 1)
