"""The helper-brief fix checked file by file: each fixed folder (a hard-linked copy of the original, then rewritten by
`collect briefs`, the work labels and SWE-chat's prompts made again) against its original.

- Every file is the original's very file (same inode) unless it is one the fix may write: a message table, a session
  table, the changelog, the work labels, tier B's SWE-chat prompts. No file is added or lost.
- A message table changes only the brief rows (and copies of them): typed_by_developer true -> false, actor -> peer,
  pushback and text_id -> empty; nothing else in any row or column. Every brief row the folder holds typed is changed.
- A session table changes only n_user_messages, lowered by the non-copy rows changed in the folder, and
  source_facts.typed_fingerprint, set to the digest made again from the fixed version (or removed); nothing else.
- The changelog gains helper_briefs and, in tier B, SWE-chat prompts' new counts; nothing else changes.
- The work labels lose exactly the sessions left with no typed message; nothing else changes.
- SWE-chat's prompts: the same prompts; the changes, counted by (written_by, pushback) before and after.
- The digest itself: made again from the original version for sessions the fix leaves, it must equal the stored one.

Prints a summary and writes it as JSON; exits 1 on any departure.

python verify_fix.py ROWS.jsonl OUT.json NAME=ORIGINAL:FIXED [NAME=ORIGINAL:FIXED ...]   (the version's pair first)
"""

from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from collect import briefs

CHANGED_COLS = {"typed_by_developer", "actor", "pushback", "text_id"}
problems: list[str] = []


def bad(msg: str) -> None:
    if len(problems) < 200:
        problems.append(msg)
    print("PROBLEM:", msg, flush=True)


def files(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()}


def kind(rel: str) -> str:
    parts = Path(rel).parts
    if parts[0] == "conversations" or parts[:2] == ("labels", "conversations"):
        return "message"
    if parts[0] == "data" or rel == "pointers.parquet":
        return "session"
    if rel == "changelog.json":
        return "changelog"
    if parts[0] == "work_labels":
        return "work_labels"
    if rel == "labels/swe_chat_prompts.parquet":
        return "swe_prompts"
    return "other"


def message(a: Path, b: Path, target: set, got: Counter, lowered: Counter, rel: str) -> None:
    ta, tb = pq.read_table(a), pq.read_table(b)
    if not ta.schema.equals(tb.schema) or ta.num_rows != tb.num_rows:
        return bad(f"{rel}: schema or row count changed")
    for c in ta.schema.names:
        if c not in CHANGED_COLS and not ta[c].equals(tb[c]):
            bad(f"{rel}: column {c} changed")
    keys = ta["session_key"].to_pylist()
    turns = ta["turn_number"].to_pylist()
    copy_of = ta["copy_of"].to_pylist() if "copy_of" in ta.schema.names else [None] * len(keys)
    cols = [c for c in CHANGED_COLS if c in ta.schema.names]
    old = {c: ta[c].to_pylist() for c in cols}
    new = {c: tb[c].to_pylist() for c in cols}
    for i, (k, n, cp) in enumerate(zip(keys, turns, copy_of)):
        is_target = (k, n) in target or (cp is not None and (k, cp) in target)
        differs = any(old[c][i] != new[c][i] for c in cols)
        if is_target and old["typed_by_developer"][i]:
            want = {"typed_by_developer": False, "actor": "peer", "pushback": None, "text_id": None}
            if any(new[c][i] != want[c] for c in cols):
                bad(f"{rel}: brief row {k} {n} not marked as wanted")
            got["rows changed"] += 1
            got[f"pushback label cleared: {old['pushback'][i]}" if "pushback" in cols else "rows without pushback"] += 1
            got[f"actor was {old['actor'][i]}"] += 1
            if cp is None:
                lowered[k] += 1
            else:
                got["copies changed"] += 1
        elif differs:
            bad(f"{rel}: row {k} {n} changed though not a brief")


def session(a: Path, b: Path, lowered: Counter, fps: dict, got: Counter, rel: str) -> None:
    ta, tb = pq.read_table(a), pq.read_table(b)
    if not ta.schema.equals(tb.schema) or ta.num_rows != tb.num_rows:
        return bad(f"{rel}: schema or row count changed")
    for c in ta.schema.names:
        if c not in ("n_user_messages", "source_facts") and not ta[c].equals(tb[c]):
            bad(f"{rel}: column {c} changed")
    keys = ta["session_key"].to_pylist()
    if "n_user_messages" in ta.schema.names:
        for k, x, y in zip(keys, ta["n_user_messages"].to_pylist(), tb["n_user_messages"].to_pylist()):
            want = x if x is None or not lowered.get(k) else max(0, x - lowered[k])
            if y != want:
                bad(f"{rel}: {k} n_user_messages {x} -> {y}, wanted {want}")
            elif y != x:
                got["n_user_messages lowered"] += 1
                got["sessions left with none typed"] += (y == 0)
    if "source_facts" in ta.schema.names:
        for k, x, y in zip(keys, ta["source_facts"].to_pylist(), tb["source_facts"].to_pylist()):
            if x == y:
                if k in fps and (json.loads(x or "{}").get("typed_fingerprint") != fps[k]):
                    bad(f"{rel}: {k} fingerprint not made again")
                continue
            fx, fy = json.loads(x or "{}"), json.loads(y or "{}")
            fx.pop("typed_fingerprint", None)
            fy_fp = fy.pop("typed_fingerprint", None)
            if fx != fy or k not in fps or fy_fp != fps[k]:
                bad(f"{rel}: {k} source_facts changed beyond the fingerprint")
            got["fingerprints removed" if fy_fp is None else "fingerprints changed"] += 1


def changelog(a: Path, b: Path, got: Counter, rel: str) -> None:
    ca, cb = json.loads(a.read_text()), json.loads(b.read_text())
    hb = cb.pop("helper_briefs", None)
    if hb is None:
        bad(f"{rel}: no helper_briefs")
    else:
        got["changelog helper_briefs rows"] = hb.get("rows", 0)
    sa = (ca.get("labels") or {}).get("swe_chat_prompts")
    sb = (cb.get("labels") or {}).get("swe_chat_prompts")
    if sa and sb:
        for k in ("prompts", "written_by"):
            sa.pop(k, None), sb.pop(k, None)
    if ca != cb:
        bad(f"{rel}: changed beyond helper_briefs and SWE-chat prompts' counts")


def work_labels(a: Path, b: Path, typed_after: set, got: Counter, rel: str) -> None:
    if rel.endswith("sessions.parquet"):
        ra = pq.read_table(a).to_pylist()
        rb = pq.read_table(b).to_pylist()
        kb = {r["session_key"] for r in rb}
        dropped = [r for r in ra if r["session_key"] not in kb]
        if [r for r in ra if r["session_key"] in kb] != rb:
            bad(f"{rel}: rows kept are not the original rows, in order")
        if any(r["session_key"] in typed_after for r in dropped):
            bad(f"{rel}: a session still typed was dropped")
        if any(r["session_key"] not in typed_after for r in rb):
            bad(f"{rel}: a session with nothing typed kept its labels")
        got["work-label sessions dropped"] += len(dropped)
        got["work-label sessions kept"] += len(rb)
    elif rel.endswith(".parquet"):
        if not pq.read_table(a).equals(pq.read_table(b)):
            bad(f"{rel}: changed")
    else:
        ma, mb = json.loads(a.read_text()), json.loads(b.read_text())
        ma.pop("sessions"), mb.pop("sessions")
        if ma != mb:
            bad(f"{rel}: changed beyond its session count")


def swe_prompts(a: Path, b: Path, got: Counter, rel: str) -> None:
    ta, tb = pq.read_table(a).to_pylist(), pq.read_table(b).to_pylist()
    if [r["turn_id"] for r in ta] != [r["turn_id"] for r in tb]:
        return bad(f"{rel}: the prompts or their order changed")
    for x, y in zip(ta, tb):
        if x != y:
            if (x["session_id"], x["turn_number"]) != (y["session_id"], y["turn_number"]):
                bad(f"{rel}: {x['turn_id']} keys changed")
            got[f"SWE-chat prompt {x['written_by']}/{x['pushback']} -> {y['written_by']}/{y['pushback']}"] += 1


def main() -> int:
    rows_file, out = Path(sys.argv[1]), Path(sys.argv[2])
    pairs = [(p.split("=", 1)[0], *map(Path, p.split("=", 1)[1].split(":"))) for p in sys.argv[3:]]
    rows = [json.loads(line) for line in open(rows_file)]
    target = {(r["session_key"], r["turn_number"]) for r in rows}
    sessions = {k for k, _ in target}
    version0, version = pairs[0][1], pairs[0][2]
    fps = briefs.fingerprints(version, sessions)
    typed_after = set()
    for p in sorted((version / "conversations").rglob("*.parquet")):
        t = pq.read_table(p, columns=["session_key", "typed_by_developer", "copy_of"])
        typed_after |= {k for k, ty, c in zip(t["session_key"].to_pylist(), t["typed_by_developer"].to_pylist(),
                                              t["copy_of"].to_pylist()) if ty and c is None}
    summary = {"rows asked": len(rows), "sessions asked": len(sessions)}
    for name, orig, fixed in pairs:
        fa, fb = files(orig), files(fixed)
        if set(fa) != set(fb):
            bad(f"{name}: files added {sorted(set(fb) - set(fa))[:5]}, lost {sorted(set(fa) - set(fb))[:5]}")
        got, lowered = Counter(), Counter()
        changed = sorted(rel for rel in set(fa) & set(fb) if os.stat(fa[rel]).st_ino != os.stat(fb[rel]).st_ino)
        by_kind = Counter(kind(rel) for rel in changed)
        for rel in changed:
            if kind(rel) == "message":
                message(fa[rel], fb[rel], target, got, lowered, f"{name}:{rel}")
        for rel in changed:
            k = kind(rel)
            if k == "session":
                session(fa[rel], fb[rel], lowered, fps, got, f"{name}:{rel}")
            elif k == "changelog":
                changelog(fa[rel], fb[rel], got, f"{name}:{rel}")
            elif k == "work_labels":
                work_labels(fa[rel], fb[rel], typed_after, got, f"{name}:{rel}")
            elif k == "swe_prompts":
                swe_prompts(fa[rel], fb[rel], got, f"{name}:{rel}")
            elif k == "other":
                bad(f"{name}: {rel} changed, which the fix never writes")
        summary[name] = {"files": len(fb), "files changed": len(changed), "by kind": dict(by_kind), **dict(got)}
        print(name, json.dumps(summary[name]), flush=True)
    # every row asked (helper sessions' typed rows, delivered copies among them) changed once in the version (and the
    # delivery), once over the two tiers
    for name, _, _ in pairs:
        n = summary[name].get("rows changed", 0)
        if name in ("version", "delivery") and n != len(rows):
            bad(f"{name}: {n} rows changed, wanted {len(rows)}")
    tiers = sum(summary[n].get("rows changed", 0) for n, _, _ in pairs if n.startswith("tier"))
    if any(n.startswith("tier") for n, _, _ in pairs) and tiers != len(rows):
        bad(f"tiers: {tiers} rows changed over both, wanted {len(rows)}")
    # the fixed version's digests are the ones made again, files changed or not
    for p in sorted((version / "data").rglob("*.parquet")):
        t = pq.read_table(p, columns=["session_key", "source_facts"])
        for k, f in zip(t["session_key"].to_pylist(), t["source_facts"].to_pylist()):
            if k in fps and json.loads(f or "{}").get("typed_fingerprint") != fps[k]:
                bad(f"version: {k} digest is not the one made again")
    # the digest made again from the original version, for sessions the fix leaves: it must equal the stored one
    stored = {}
    for p in sorted((version0 / "data").rglob("*.parquet")):
        t = pq.read_table(p, columns=["session_key", "source_facts"])
        for k, f in zip(t["session_key"].to_pylist(), t["source_facts"].to_pylist()):
            fp = json.loads(f or "{}").get("typed_fingerprint")
            if fp and k not in sessions:
                stored[k] = fp
    sample = random.Random(20261104).sample(sorted(stored), min(3000, len(stored)))
    again = briefs.fingerprints(version0, set(sample))
    same = sum(again[k] == stored[k] for k in sample)
    summary["fingerprint made again, sessions the fix leaves"] = {"sampled": len(sample), "equal": same}
    print("fingerprint check:", same, "of", len(sample), "equal", flush=True)
    if same != len(sample):
        bad(f"fingerprint made again differs on {len(sample) - same} of {len(sample)} sessions the fix leaves")
    summary["problems"] = problems
    out.write_text(json.dumps(summary, indent=1))
    print("PASS" if not problems else f"FAIL: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
