"""Tier A without the text of sessions whose repository withdrew its logs (2026-11-06).

Read again on 2026-11-06 (scripts/growth), 50 of the version's GitHub repositories gave no session at all: 34 deleted
or made private, 16 public but emptied by their owners (checkpoints removed, or moved to a private repository). Their
sessions keep no text in the release: a kept tier-A session from such a repository moves to tier B as a pointer (with
every label, no text), and every tier-A copy row from such a repository, or of a session that moves, is left out.
Tier B's sessions there stay pointers. Nothing else changes: every other row is the release's own, as checked.

python withdraw.py VERSION_DIR ORIGINAL_RELEASE NEW_RELEASE WITHDRAWN.tsv OWNERS.json REPORT.json

- NEW_RELEASE: a hard-linked copy of ORIGINAL_RELEASE (`cp -al`), both holding a-names/ (tier A) and b/ (tier B).
  Every file changed is written beside and moved over, so the original's files (the same inodes) never change; the
  original's files are checked against their size, time and inode at the end.
- WITHDRAWN.tsv: reason, owner/name, ... (growth/empty_repos.tsv); every repository listed is withdrawn.
- OWNERS.json: GitHub's profile facts by login, for each tier's repositories.parquet and owners note (as at release).
- Tier B's message labels for the moved sessions are made by collect.tier_labels.message_labels from VERSION_DIR, as
  the tiers step makes them; their pointers by collect.tiers._pointers, checked equal to the release's own pointers
  where both exist. The work labels and Entire's line attribution are split again by their own scripts after this.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from collect import tier_labels, tiers

TABLES = ("data", "conversations", "conversations_subagents")
TMP = ".withdraw-tmp"


def snapshot(root: Path) -> dict[str, list[int]]:
    return {str(p.relative_to(root)): [p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns]
            for p in sorted(root.rglob("*")) if p.is_file()}


def replace_table(path: Path, table: pa.Table) -> None:
    tmp = path.with_name(path.name + TMP)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def replace_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + TMP)
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    os.replace(tmp, path)


def keys_in(table: pa.Table, keys: set[str]) -> pa.Array:
    return pc.is_in(table["session_key"], value_set=pa.array(sorted(keys), type=table["session_key"].type))


def run(version: Path, orig: Path, new: Path, withdrawn: set[str], signals: dict, on: date, log=print) -> dict:
    version, orig, new = Path(version), Path(orig), Path(new)
    if orig.resolve() == new.resolve():
        raise ValueError("the new release must be a copy, not the original")
    before = snapshot(orig)
    copy = snapshot(new)
    if set(copy) != set(before) or any(copy[k][0] != before[k][0] for k in before):
        raise ValueError(f"{new} is not a hard-linked copy of {orig} (cp -al)")
    a, b = new / "a-names", new / "b"

    # 1. tier A's sessions: which move, which copy rows go
    rows = pa.concat_tables([pq.read_table(f, columns=["session_key", "source", "repo", "duplicate_of"])
                             for f in sorted(a.glob("data/*/*.parquet"))]).to_pylist()
    kept_a = {r["session_key"] for r in rows if r["duplicate_of"] is None}
    moved = {r["session_key"] for r in rows if r["duplicate_of"] is None and r["repo"] in withdrawn}
    copies_out = {r["session_key"] for r in rows if r["duplicate_of"] is not None
                  and (r["repo"] in withdrawn or r["duplicate_of"] in moved)}
    drop = moved | copies_out
    report = {"withdrawn_repositories_listed": len(withdrawn),
              "withdrawn_repositories_in_tier_a": len({r["repo"] for r in rows if r["repo"] in withdrawn}),
              "moved_sessions": len(moved), "copies_left_out": len(copies_out),
              "moved_by_source": {}, "rows_left_out": {}, "files_rewritten": {}, "files_removed": {}}
    for r in rows:
        if r["session_key"] in moved:
            report["moved_by_source"][r["source"]] = report["moved_by_source"].get(r["source"], 0) + 1
    log(f"withdraw: {len(moved):,} sessions move to tier B, {len(copies_out):,} copy rows left out")

    # 2. tier A's tables without them (only files that hold one are rewritten)
    for t in TABLES:
        out = rewritten = removed = 0
        for f in sorted(a.glob(f"{t}/*/*.parquet")):
            if not pc.any(keys_in(pq.read_table(f, columns=["session_key"]), drop)).as_py():
                continue
            table = pq.read_table(f)
            keep = table.filter(pc.invert(keys_in(table, drop)))
            out += len(table) - len(keep)
            if len(keep):
                replace_table(f, keep)
                rewritten += 1
            else:
                f.unlink()   # this link only: the original's file stays
                removed += 1
        report["rows_left_out"][t], report["files_rewritten"][t], report["files_removed"][t] = out, rewritten, removed
        log(f"withdraw: tier A {t}: {out:,} rows left out ({rewritten} files rewritten, {removed} removed)")
    kept_after = kept_a - moved

    # 3. tier B's pointers: the release's own rows as they are, the moved sessions' added as the tiers step makes them
    old = pq.read_table(b / "pointers.parquet")
    made = tiers._pointers(version, kept_after)
    made_keys = set(made["session_key"].to_pylist())
    old_keys = set(old["session_key"].to_pylist())
    if made_keys != old_keys | moved or old_keys & moved:
        raise ValueError(f"pointers made from the version: {len(made_keys - old_keys - moved)} unexpected, "
                         f"{len((old_keys | moved) - made_keys)} missing")
    made_old = {r["session_key"]: r for r in made.filter(keys_in(made, old_keys)).to_pylist()}
    report["pointers_made_differ_from_release"] = sum(1 for r in old.to_pylist() if made_old[r["session_key"]] != r)
    add = made.filter(keys_in(made, moved))
    if add.schema != old.schema:
        add = add.select(old.schema.names).cast(old.schema)
    pointers = pa.concat_tables([old, add])
    replace_table(b / "pointers.parquet", pointers)
    repos = sorted({r for r, s in zip(pointers["repo"].to_pylist(), pointers["source"].to_pylist())
                    if r and s in tiers.GITHUB_SOURCES})
    old_repos = (b / "repositories.txt").read_text().split()
    if not set(old_repos) <= set(repos):
        raise ValueError("a repository left tier B's list")
    tmp = b / ("repositories.txt" + TMP)
    tmp.write_text("".join(f"{r}\n" for r in repos))
    os.replace(tmp, b / "repositories.txt")
    log(f"withdraw: tier B pointers {len(old):,} + {len(add):,}; "
        f"{report['pointers_made_differ_from_release']} of the release's differ from the version's")

    # 4. tier B's message labels for the moved sessions, beside the release's own
    work = new / "withdraw-labels-tmp"
    if work.exists():
        raise FileExistsError(work)
    made_rows = tier_labels.message_labels(version, work, moved)
    added_labels = {}
    for f in sorted((work / "labels").rglob("*.parquet")):
        rel = f.relative_to(work)
        dst = b / rel
        got = pq.read_table(f)
        if dst.exists():
            have = pq.read_table(dst)
            if pc.any(keys_in(have, moved)).as_py():
                raise ValueError(f"{dst} already holds a moved session's labels")
            if got.schema != have.schema:
                got = got.select(have.schema.names).cast(have.schema)
            replace_table(dst, pa.concat_tables([have, got]))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
        added_labels[rel.parts[1]] = added_labels.get(rel.parts[1], 0) + len(got)
    shutil.rmtree(work)
    if added_labels != {k: v for k, v in made_rows.items() if v}:
        raise ValueError(f"labels added {added_labels} vs made {made_rows}")
    report["labels_added"] = added_labels
    log(f"withdraw: tier B message labels added {added_labels}")

    # 5. each tier's changelog: its counts again, and the note
    note = {"date": "2026-11-06",
            "rule": "the version's GitHub repositories were read again on 2026-11-06 with no token; a repository that "
                    "gave no session (deleted, made private, or emptied by its owner) keeps no text here: its tier-A "
                    "sessions moved to tier B as pointers, and tier-A copy rows from it, or of a session that moved, "
                    "were left out",
            "repositories": report["withdrawn_repositories_in_tier_a"], "sessions_moved_to_tier_b": len(moved),
            "copy_rows_left_out": len(copies_out)}
    cla = json.loads((a / "changelog.json").read_text())
    cla["counts"]["sessions"] = len(kept_after)
    cla["counts"]["copies"] = cla["counts"]["copies"] - len(copies_out)
    for t in ("conversations", "conversations_subagents"):
        cla["counts"]["rows"][t] -= report["rows_left_out"][t]
    cla["withdrawn"] = note
    replace_json(a / "changelog.json", cla)
    clb = json.loads((b / "changelog.json").read_text())
    by_source: dict[str, int] = {}
    for s in pointers["source"].to_pylist():
        by_source[s] = by_source.get(s, 0) + 1
    also = pointers["also_in"].to_pylist()
    clb["counts"] = {"sessions": len(pointers), "by_source": dict(sorted(by_source.items())), "repositories": len(repos),
                     "also_in": {"sessions": sum(1 for x in also if x), "places": sum(len(x) for x in also)}}
    for t, n in added_labels.items():
        clb["labels"]["message_labels"]["rows"][t] += n
    clb["withdrawn"] = note
    replace_json(b / "changelog.json", clb)

    # 6. each tier's repositories and owners, from the same lookup and date as the release
    tier_labels.owners_again({"a": a, "b": b}, signals, on, log=log)

    # 7. the original release: not one file changed
    after = snapshot(orig)
    if after != before:
        changed = [k for k in before if after.get(k) != before[k]]
        raise RuntimeError(f"the original release changed: {changed[:5]}")
    report["original_unchanged_files"] = len(before)
    report["tier_a_sessions"], report["tier_b_sessions"] = len(kept_after), len(pointers)
    return report


def main() -> int:
    version, orig, new, wd, owners, out = sys.argv[1:7]
    withdrawn = {line.split("\t")[1] for line in Path(wd).read_text().splitlines() if line.strip()}
    report = run(Path(version), Path(orig), Path(new), withdrawn, json.loads(Path(owners).read_text()),
                 date(2026, 11, 2), log=lambda m: print(m, flush=True))
    Path(out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
