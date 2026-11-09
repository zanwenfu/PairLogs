"""Read-only: which typed rows of a version the current typed rules (code on PYTHONPATH) no longer read as typed,
told again as _retell tells a reader's typed row. Per opening (60 characters, digits and hex masked): rows, sessions,
the rule's verdict, one example; and the sessions left with no typed row. argv: VERSION_DIR OUT.jsonl"""
import collections
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyarrow.parquet as pq

from collect import events as E

mask = lambda t: re.sub(r"[0-9a-f]{6,}|\d+", "#", " ".join(t.split()))[:60]


def one(path):
    t = pq.read_table(path, columns=["session_key", "content", "copy_of", "kind"],
                      filters=[("typed_by_developer", "=", True)]).to_pylist()
    flips, typed = [], collections.Counter()
    for r in t:
        if r["copy_of"] is not None:
            continue
        typed[r["session_key"]] += 1
        if r["kind"] not in ("text", "queued") or not (r["content"] or "").strip():
            continue
        _, now, actor, kind, _ = E.tell(r["content"], template_anywhere=False)
        if not now:
            flips.append((r["session_key"], mask(r["content"]), actor, kind, r["content"][:400]))
    return flips, typed


def main():
    V, out = sys.argv[1:3]
    files = sorted(Path(V, "conversations").glob("*/*.parquet"))
    groups, typed = {}, collections.Counter()
    with ProcessPoolExecutor(16) as ex:
        for flips, t in ex.map(one, files):
            typed.update(t)
            for key, o, actor, kind, text in flips:
                g = groups.setdefault(o, {"opening": o, "rows": 0, "sessions": set(), "verdict": f"{actor}/{kind}",
                                          "example": text})
                g["rows"] += 1
                g["sessions"].add(key)
                g.setdefault("per", collections.Counter())[key] += 1
    per = collections.Counter()   # flipped rows per session
    for g in groups.values():
        for k, n in g["per"].items():
            per[k] += n
    with open(out, "w") as f:
        for o, g in sorted(groups.items(), key=lambda x: -x[1]["rows"]):
            f.write(json.dumps({**{k: v for k, v in g.items() if k != "per"}, "sessions": len(g["sessions"])},
                               ensure_ascii=False) + "\n")
    all_sessions = set().union(*[g["sessions"] for g in groups.values()]) if groups else set()
    print(json.dumps({"typed_rows_seen": sum(typed.values()), "typed_sessions": len(typed),
                      "flipped_rows": sum(g["rows"] for g in groups.values()), "openings": len(groups),
                      "sessions_touched": len(all_sessions),
                      "sessions_left_with_no_typed_row": sum(1 for k, n in per.items() if n >= typed[k])}, indent=1))


if __name__ == "__main__":
    main()
