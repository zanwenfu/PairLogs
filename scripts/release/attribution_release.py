"""Entire's own line attribution as a side table: for every kept Entire session whose checkpoint metadata.json holds
`initial_attribution` (Entire computes it when the checkpoint is committed: lines the agent wrote, lines a person
added, changed or removed, and the agent's share of the committed lines), one row with those numbers, read from the
version's files as published. Tier A's sessions go to TIER_A/attribution/entire.parquet, the others to
TIER_B/attribution/entire.parquet (numbers only, no text). Each tier's changelog records the count. Files are written
beside and moved over (the tiers are hard-linked copies). Prints and writes (SUMMARY.json) the counts and the split
the paper reports: of the sessions that committed lines, the share where the agent wrote 99% or more, none, or between.

python attribution_release.py VERSION_DIR TIER_A TIER_B SUMMARY.json [--write]
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

INTS = ("agent_lines", "agent_removed", "human_added", "human_modified", "human_removed", "total_committed",
        "total_lines_changed", "metric_version")
SCHEMA = pa.schema([("session_key", pa.string()), ("checkpoint_id", pa.string()), ("agent", pa.string()),
                    ("calculated_at", pa.string())] + [(c, pa.int64()) for c in INTS]
                   + [("agent_percentage", pa.float64())])


def num(v, kind):
    return kind(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def rows(version: Path) -> list[dict]:
    out = []
    for f in sorted((version / "data" / "entire").glob("*.parquet")):
        t = pq.read_table(f, columns=["session_key", "duplicate_of", "agent", "files"])
        for k, dup, agent, fl in zip(*[t[c].to_pylist() for c in ("session_key", "duplicate_of", "agent", "files")]):
            if dup is not None:
                continue
            metas = [x for x in fl or [] if Path(x["path"]).name == "metadata.json"]
            if len(metas) > 1:
                sys.exit(f"refused: {k} has {len(metas)} metadata.json files")
            try:
                meta = json.loads(metas[0]["content"] or "") if metas else None
            except ValueError:
                meta = None
            att = meta.get("initial_attribution") if isinstance(meta, dict) else None
            if not isinstance(att, dict) or num(att.get("total_committed"), int) is None:
                continue
            out.append({"session_key": k, "checkpoint_id": meta.get("checkpoint_id"), "agent": agent,
                        "calculated_at": att.get("calculated_at"),
                        **{c: num(att.get(c), int) for c in INTS},
                        "agent_percentage": num(att.get("agent_percentage"), float)})
    return out


def tier_keys(tier: Path) -> set:
    keys = set()
    for f in (tier / "data" / "entire").glob("*.parquet"):
        keys.update(pq.read_table(f, columns=["session_key"])["session_key"].to_pylist())
    return keys


def put(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".attr-tmp")
    write(tmp)
    os.replace(tmp, path)


def split(rs: list[dict]) -> dict:
    done = [r for r in rs if (r["total_committed"] or 0) > 0 and r["agent_percentage"] is not None]
    c = Counter("agent 99%+" if r["agent_percentage"] >= 99 else "agent none" if r["agent_percentage"] == 0
                else "between" for r in done)
    return {"sessions": len(rs), "committed lines": len(done),
            **{k: c[k] for k in ("agent 99%+", "agent none", "between")},
            **{f"pct {k}": round(100 * c[k] / len(done), 1) if done else None
               for k in ("agent 99%+", "agent none", "between")}}


def main() -> int:
    version, tier_a, tier_b, summary = map(Path, sys.argv[1:5])
    write = "--write" in sys.argv[5:]
    rs = rows(version)
    in_a = tier_keys(tier_a)
    a = [r for r in rs if r["session_key"] in in_a]
    b = [r for r in rs if r["session_key"] not in in_a]
    if len({r["session_key"] for r in rs}) != len(rs):
        sys.exit("refused: a session twice")
    got = {"all": split(rs), "tier A": split(a), "tier B": split(b)}
    summary.write_text(json.dumps(got, indent=1))
    print(json.dumps(got))
    if not write:
        return 0
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for tier, part in ((tier_a, a), (tier_b, b)):
        t = pa.Table.from_pylist(sorted(part, key=lambda r: r["session_key"]), schema=SCHEMA)
        put(tier / "attribution" / "entire.parquet", lambda tmp: pq.write_table(t, tmp, compression="zstd"))
        log = tier / "changelog.json"
        c = json.loads(log.read_text())
        c["attribution"] = {"table": "attribution/entire.parquet", "sessions": len(part),
                            "how": "Entire's own initial_attribution from each kept Entire session's checkpoint "
                                   "metadata.json, as published", "made_at": when}
        put(log, lambda tmp: tmp.write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n"))
    print(f"written: tier A {len(a)}, tier B {len(b)} sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
