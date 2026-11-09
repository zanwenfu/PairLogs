"""Add the labels written beside the tiers (collect.tier_labels) to a release already written, and `also_in` to its
tier-B pointers (2026-11-03), changing nothing else there: tier B's message labels, SWE-chat's prompts, each tier's
repositories.parquet, the tiers' changelogs, and tier B's pointers.parquet rewritten with the column added.

argv: VERSION_DIR TIER_A TIER_B OWNERS.json SWE_PROMPTS.parquet BACKUP_DIR DATE
- Backs up both changelogs and the pointers first (into BACKUP_DIR, which must not exist).
- The pointers: made again by collect.tiers._pointers from the version; every column but `also_in` must equal the
  published file's, row for row, or nothing is written.
- Every other file in both tiers must keep its size and time (checked after).
- Then checks what it wrote against the version itself (duckdb): rows, sessions, labels, owners. Prints counts only.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from collect import tier_labels, tiers
from collect.pushback import text_id

TEXT = {"content", "tool_input_json", "command", "file_path", "pattern", "prompt_intent", "prompt_pushback", "language",
        "head"}


def stats(root: Path, skip: set[str]) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
            if p.is_file() and str(p.relative_to(root)) not in skip and "labels" not in p.relative_to(root).parts}


def main() -> int:
    version, a, b, owners, prompts, backup, on = sys.argv[1:8]
    version, a, b, backup = Path(version), Path(a), Path(b), Path(backup)
    on = date.fromisoformat(on)
    backup.mkdir(parents=True)   # refuses an existing one
    shutil.copy2(a / "changelog.json", backup / "a-changelog.json")
    shutil.copy2(b / "changelog.json", backup / "b-changelog.json")
    shutil.copy2(b / "pointers.parquet", backup / "b-pointers.parquet")
    print(f"backed up into {backup}", flush=True)
    skip = {"changelog.json", "pointers.parquet", "repositories.parquet"}
    before = {"a": stats(a, skip), "b": stats(b, skip)}

    # tier B's pointers with also_in: everything else as published
    old = pq.read_table(b / "pointers.parquet")
    kept, _ = tiers.tier_a_keys(tiers._sessions(version))
    new = tiers._pointers(version, kept)
    assert len(new) == len(old), (len(new), len(old))
    assert new["session_key"].to_pylist() == old["session_key"].to_pylist()
    for c in old.column_names:
        assert new[c].equals(old[c]), f"pointers column {c} differs"
    assert new.column_names == [*old.column_names, "also_in"], new.column_names
    tmp = b / "pointers.parquet.also-tmp"
    pq.write_table(new, tmp, compression="zstd")
    tmp.replace(b / "pointers.parquet")
    also = new["also_in"].to_pylist()
    also_counts = {"sessions": sum(1 for x in also if x), "places": sum(len(x) for x in also)}
    print(f"pointers: {len(new):,} rows, also_in {also_counts}", flush=True)

    notes = tier_labels.add(version, {"a": a, "b": b}, signals=json.loads(Path(owners).read_text()),
                            swe_prompts=Path(prompts), on=on, log=lambda m: print(m, flush=True))
    note = json.loads((b / "changelog.json").read_text())
    note["counts"]["also_in"] = also_counts
    note["also_in"] = ("each pointer's also_in lists every other place the session was found (source, source_id, "
                       "found_url), a part of it saved there under the same session id included (2026-11-03)")
    (b / "changelog.json").write_text(json.dumps(note, indent=1) + "\n")

    # nothing else rewritten
    after = {"a": stats(a, skip), "b": stats(b, skip)}
    assert after == before, "a file other than the new ones changed"
    print(f"unchanged: {len(before['a']):,} files in A, {len(before['b']):,} in B", flush=True)

    # checks against the version
    con = duckdb.connect()
    con.execute("SET threads=4; SET memory_limit='48GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
    con.execute(f"create temp table bkeys as select session_key from read_parquet('{b}/pointers.parquet')")
    bkeys = set(new["session_key"].to_pylist())
    for t, key in (("conversations", "l.session_key = v.session_key and l.turn_number = v.turn_number"),
                   ("conversations_subagents", "l.session_key = v.session_key and l.task_pk = v.task_pk "
                                               "and l.turn_number = v.turn_number")):
        files = sorted((b / "labels" / t).rglob("*.parquet"))
        cols = set().union(*(pq.read_schema(f).names for f in files))
        assert not cols & TEXT, cols & TEXT
        lab = f"read_parquet('{b}/labels/{t}/*/*.parquet', union_by_name=true)"
        ver = f"read_parquet('{version}/{t}/*/*.parquet', union_by_name=true)"
        n_lab, s_lab = con.execute(f"select count(*), count(distinct session_key) from {lab}").fetchone()
        n_ver, s_ver = con.execute(f"select count(*), count(distinct session_key) from {ver} "
                                   f"where session_key in (select session_key from bkeys)").fetchone()
        assert (n_lab, s_lab) == (n_ver, s_ver), (t, n_lab, s_lab, n_ver, s_ver)
        compare = ["typed_by_developer", "actor", "kind", "is_error"] + (["copy_of", "pushback"]
                                                                         if t == "conversations" else [])
        differ = " or ".join(f"l.{c} is distinct from v.{c}" for c in compare)
        joined, bad = con.execute(f"select count(*), count(*) filter (where {differ}) from {lab} l join {ver} v "
                                  f"on {key}").fetchone()
        assert joined == n_lab and bad == 0, (t, joined, n_lab, bad)
        pb = con.execute(f"select count(*) filter (where typed_by_developer and pushback is not null), "
                         f"count(*) filter (where pushback) from {lab}").fetchone() if t == "conversations" else None
        # text ids: every row of one label file per source, read again from the version's file of the same name
        checked = 0
        for f in [sorted((b / "labels" / t / src).glob("*.parquet"))[0] for src in
                  sorted(p.name for p in (b / "labels" / t).iterdir())]:
            lab_rows = pq.read_table(f, columns=["typed_by_developer", "text_id"]).to_pylist()
            v = pq.read_table(version / t / f.parent.name / f.name,
                              columns=["session_key", "typed_by_developer", "content"]).to_pylist()
            v = [r for r in v if r["session_key"] in bkeys]
            want = [text_id(r["content"]) if r["typed_by_developer"] else None for r in v]
            assert [r["text_id"] for r in lab_rows] == want, f"text ids differ in {f}"
            checked += len(lab_rows)
        nulls = con.execute(f"select count(*) from {lab} where text_id is not null and not "
                            f"coalesce(typed_by_developer, false)").fetchone()[0]
        assert nulls == 0, nulls
        print(f"{t}: {n_lab:,} rows of {s_lab:,} sessions = the version's for tier B; labels equal; "
              f"text ids equal on {checked:,} rows read again; typed rows with a pushback label, pushback: {pb}",
              flush=True)
    # every tier-B session that has rows has labels
    no_labels = con.execute(f"""select count(*) from (select distinct session_key from read_parquet(
        '{version}/conversations/*/*.parquet', union_by_name=true) where session_key in (select session_key from bkeys))
        where session_key not in (select distinct session_key from read_parquet(
        '{b}/labels/conversations/*/*.parquet', union_by_name=true))""").fetchone()[0]
    assert no_labels == 0, no_labels
    sp = pq.read_table(b / "labels" / "swe_chat_prompts.parquet")
    assert not set(sp.column_names) & TEXT and len(sp["turn_id"].unique()) == len(sp)
    print(f"SWE-chat prompts: {len(sp):,} rows, columns {sp.column_names}", flush=True)
    for name, tier in (("a", a), ("b", b)):
        r = pq.read_table(tier / "repositories.parquet")
        assert r.column_names == ["repo", "owner_type"]
        want = set(tier_labels._repos(tier, name))
        assert set(r["repo"].to_pylist()) == want and len(r) == len(want)
        kinds = {}
        for k in r["owner_type"].to_pylist():
            kinds[k] = kinds.get(k, 0) + 1
        print(f"tier {name.upper()} repositories: {len(r):,}, by owner type {dict(sorted(kinds.items()))}; "
              f"owners not looked up {notes[name]['owners']['owners_not_looked_up']}", flush=True)
    for name, tier in (("a", a), ("b", b)):
        labels = json.loads((tier / "changelog.json").read_text())["labels"]
        print(f"tier {name.upper()} changelog labels: {sorted(labels)}")
    print("ALL CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
