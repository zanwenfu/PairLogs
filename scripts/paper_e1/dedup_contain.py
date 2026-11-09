"""Read-only: how much of each copy's conversation its kept session holds, for every near-copy and for exact copies by
pair of sources (every one in the small strata, a fixed-seed sample in the large), in a version. Both sides are read
from their own files as fetched, by the readers the build reads them by, as dedup_spot.py reads them. A copy's text
items are its typed messages and its agent's text replies (spaces collapsed); "held" is the share of them, counted
with repeats, that the kept session also has. Tool calls are counted apart (paths in them differ between exports).
Writes counts and session keys only (no text) to OUT.

python dedup_contain.py VERSION_DIR OUT PROCS [SEED]   (env: ERRATA_STATE_ROOT; the ledger is opened read-only)"""
import json
import random
import sqlite3
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyarrow.parquet as pq

from collect import build, config, conv_build, copied_sessions, events, formats
from collect.ledger import Ledger
from collect.model import canonical_agent

SAMPLE = {"entire~entire": 300, "dataclaw~dataclaw": 300, "entire~swe-chat-v2": 300}   # exact strata sampled; others whole


class ReadOnlyLedger(Ledger):
    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()
        self._db = sqlite3.connect(f"file:{path}?immutable=1", uri=True, check_same_thread=False)


def norm(s):
    return " ".join((s or "").split())


def read(r):
    if r is None:
        return None, "no stored row"
    files = build._raw_files(r)
    if not files or not all(p.is_file() for _, p in files):
        return None, "file missing"
    session = conv_build.session_fields(r["source"], r, files)
    try:
        reader = formats.READERS.get(r.get("_reader") or "") or formats.BY_SOURCE.get(r["source"])
        got = reader(files, session) if reader else events.for_session(r.get("agent"), files, session)
    except Exception as e:
        return None, type(e).__name__
    if not got or not got[0]:
        return None, "no rows"
    return got[0], None


def items(rs):
    text, tools = Counter(), Counter()
    for r in rs:
        if r.get("typed_by_developer") and r.get("copy_of") is None:
            t = norm(r["command"] if r.get("kind") == "command" and r.get("command") else r.get("content"))
            if t:
                text[("typed", t)] += 1
        elif r.get("actor") == "agent" and r.get("turn_type") == "assistant_response":
            t = norm(r.get("content"))
            if t:
                text[("agent", t)] += 1
        elif r.get("actor") == "agent" and r.get("turn_type") == "tool_use":
            tools[(r.get("tool_name") or "", norm(r.get("tool_input_json") or r.get("content")))] += 1
    return text, tools


def held(a: Counter, b: Counter):
    n = sum(a.values())
    return (round(sum(min(v, b[k]) for k, v in a.items()) / n, 4) if n else None), n


def job(task):
    kind, pair, copy_row, kept_row = task
    a, ea = read(copy_row)
    b, eb = read(kept_row)
    out = {"kind": kind, "pair": pair, "copy": f"{copy_row['source']}:{copy_row['source_id']}",
           "kept": f"{kept_row['source']}:{kept_row['source_id']}"}
    if a is None or b is None:
        out["unreadable"] = [ea, eb]
        return out
    (ta, oa), (tb, ob) = items(a), items(b)
    out["rows"] = [len(a), len(b)]
    out["text_held"], out["text_n"] = held(ta, tb)
    out["tools_held"], out["tools_n"] = held(oa, ob)
    out["kept_text_in_copy"], _ = held(tb, ta)
    return out


def cls(h):
    if h is None:
        return "no text"
    return "held (90%+)" if h >= 0.9 else "partly (50-90%)" if h >= 0.5 else "not held (<50%)"


def main():
    V, OUT, PROCS = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 20261103
    cols = ["session_key", "source", "source_id", "session_id", "dedup_key", "duplicate_of"]
    table = {}
    for f in sorted(V.glob("data/*/*.parquet")):
        for r in pq.read_table(f, columns=cols).to_pylist():
            table[r["session_key"]] = r
    strata = defaultdict(list)
    for d in table.values():
        k = table.get(d["duplicate_of"]) if d["duplicate_of"] else None
        if k is None:
            continue
        exact = bool(d["dedup_key"]) and d["dedup_key"] == k["dedup_key"]
        strata[("exact" if exact else "near", f"{k['source']}~{d['source']}")].append((d, k))
    rng = random.Random(seed)
    chosen = []
    for (kind, pair), pairs in sorted(strata.items()):
        pairs.sort(key=lambda p: p[0]["session_key"])
        n = SAMPLE.get(pair) if kind == "exact" else None
        chosen += [(kind, pair, len(pairs), p) for p in (rng.sample(pairs, min(n, len(pairs))) if n else pairs)]
    need = {(r["source"], r["source_id"]) for *_, p in chosen for r in p}
    print(f"pairs {len(chosen):,}; sessions to read {len(need):,}", flush=True)
    cfg = config.load()
    ledger = ReadOnlyLedger(cfg.ledger)
    rows = {}
    for source, source_id in need:
        if source in ("entire", "specstory", "aider"):
            got = ledger.get(source, source_id)
            if got:
                rows[(source, source_id)] = got
    copied_sessions._facts_job = lambda args: {}
    for source, got in copied_sessions.rows(ledger, None).items():
        for r in got:
            if (source, r["source_id"]) in need:
                rows[(source, r["source_id"])] = r
    for r in rows.values():
        r["agent"] = canonical_agent(r.get("agent"))
    print(f"rows found {len(rows):,} of {len(need):,}", flush=True)
    tasks = [(kind, pair, rows.get((d["source"], d["source_id"])), rows.get((k["source"], k["source_id"])))
             for kind, pair, _, (d, k) in chosen]
    tasks = [t for t in tasks if t[2] is not None and t[3] is not None]
    sizes = {(kind, pair): size for kind, pair, size, _ in chosen}
    results = []
    with ProcessPoolExecutor(PROCS) as pool:
        for i, got in enumerate(pool.map(job, tasks, chunksize=4), 1):
            results.append(got)
            if i % 500 == 0:
                print(f"read {i:,} of {len(tasks):,}", flush=True)
    summary = {}
    for (kind, pair), size in sorted(sizes.items()):
        rs = [r for r in results if r["kind"] == kind and r["pair"] == pair]
        c = Counter("unreadable" if "unreadable" in r else cls(r["text_held"]) for r in rs)
        summary[f"{kind} {pair}"] = {"population": size, "read": len(rs), "classes": dict(c)}
        print(kind, pair, "population", size, "read", len(rs), dict(c), flush=True)
    OUT.write_text(json.dumps({"version": V.name, "seed": seed, "sample": SAMPLE, "summary": summary,
                               "pairs": results}, indent=1))


if __name__ == "__main__":
    main()
