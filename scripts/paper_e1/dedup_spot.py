"""Read-only spot check of a version's duplicate choice: a fixed-seed sample of exact copies (same dedup key as the
kept session) by pair of sources, and of near-copies (a different key) by source. Each copy and its kept session are
read again from their own files as fetched, by the readers the build reads them by (never from the dedup keys or the
build's summaries), and their rows compared in order: role, text and tool input, spaces collapsed, a masked stretch
on one side agreeing with any text on the other (conv_build._masked_agrees: SWE-chat's copy was seeded masked).

Verdicts: identical; prefix (one is the other's opening: a snapshot of a session that grew); typed-same (the
developer's typed messages are the same run, in order, or one's an opening of the other's, but other rows differ);
different; unreadable. Writes counts, shapes and session keys only (no text) to OUT; prints counts.

python dedup_spot.py VERSION_DIR OUT [SEED]   (env: ERRATA_STATE_ROOT; the ledger is opened read-only)"""
import json
import math
import random
import sqlite3
import sys
import threading
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from collect import build, config, conv_build, copied_sessions, events, formats, scan
from collect.ledger import Ledger
from collect.model import canonical_agent

V, OUT = Path(sys.argv[1]), Path(sys.argv[2])
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 20261103
EXACT = {"entire~swe-chat-v2": 25, "entire~entire": 25, "dataclaw~dataclaw": 20, "specstory~specstory": 15, "rest": 15}
NEAR = {"dataclaw": 15, "entire": 15, "specstory": 12, "openhands-feedback": 5, "rest": 3}


class ReadOnlyLedger(Ledger):
    def __init__(self, path: Path):   # immutable: no lock, no journal, nothing written beside the file
        self.path, self._lock = path, threading.Lock()
        self._db = sqlite3.connect(f"file:{path}?immutable=1", uri=True, check_same_thread=False)


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if not n:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(p, 4), round(c - h, 4), round(c + h, 4))


cols = ["session_key", "source", "source_id", "session_id", "dedup_key", "duplicate_of", "repo", "agent",
        "n_conversation_rows", "bytes"]
table = {}
for f in sorted(V.glob("data/*/*.parquet")):
    for r in pq.read_table(f, columns=cols).to_pylist():
        table[r["session_key"]] = r
copies = [r for r in table.values() if r["duplicate_of"]]
print(f"rows {len(table):,}; copies {len(copies):,}", flush=True)

kinds = Counter()
exact_by, near_by = defaultdict(list), defaultdict(list)
for d in copies:
    k = table.get(d["duplicate_of"])
    if k is None:
        kinds["kept row missing"] += 1
        continue
    pair = f"{k['source']}~{d['source']}"
    if d["dedup_key"] and d["dedup_key"] == k["dedup_key"]:
        kinds[f"exact {pair}"] += 1
        exact_by[pair if pair in EXACT else "rest"].append((d, k))
    else:
        same_id = bool(d["session_id"]) and d["session_id"] == k["session_id"]
        kinds[f"{'same-id' if same_id else 'near'} {pair}"] += 1
        near_by[d["source"] if d["source"] in NEAR else "rest"].append((d, k))
print("copies by kind:", dict(sorted(kinds.items())), flush=True)

rng = random.Random(SEED)
sample = []
for stratum, n in EXACT.items():
    pool = sorted(exact_by.get(stratum, []), key=lambda p: p[0]["session_key"])
    sample += [("exact", stratum, len(pool), p) for p in rng.sample(pool, min(n, len(pool)))]
for stratum, n in NEAR.items():
    pool = sorted(near_by.get(stratum, []), key=lambda p: p[0]["session_key"])
    sample += [("near", stratum, len(pool), p) for p in rng.sample(pool, min(n, len(pool)))]
need = {(r["source"], r["source_id"]) for _, _, _, pair in sample for r in pair}
print(f"sampled pairs {len(sample)}; sessions to read {len(need)}", flush=True)

# The sessions as the build reads them: collected rows from the ledger; copied datasets' sessions from their files.
cfg = config.load()
ledger = ReadOnlyLedger(cfg.ledger)
rows = {}
for source, source_id in need:
    if source in ("entire", "specstory", "aider"):
        got = ledger.get(source, source_id)
        if got:
            rows[(source, source_id)] = got
copied_sessions._facts_job = lambda args: {}   # counts are not wanted here: SWE-chat's rows without parsing all 18k
for source, got in copied_sessions.rows(ledger, None).items():
    for r in got:
        if (source, r["source_id"]) in need:
            rows[(source, r["source_id"])] = r
for r in rows.values():
    r["agent"] = canonical_agent(r.get("agent"))
print(f"rows found {len(rows)} of {len(need)}", flush=True)
shape = {}
for r in rows.values():   # one file entry's shape per source, to show what is read
    shape.setdefault(r["source"], {"files": [sorted(f) for f in (r.get("files") or [])[:1]],
                                   "read": [n for n, _ in (r.get("_read") or [])[:1]], "reader": r.get("_reader")})
print("file shapes:", json.dumps(shape)[:1500], flush=True)


def read(key):
    r = rows.get(key)
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
        return None, f"{type(e).__name__}"
    if not got or not got[0]:
        return None, "no rows"
    return got[0], None


def norm(s):
    return " ".join((s or "").split())


def item(r):
    return (r.get("role") or "", norm(r.get("content")), norm(r.get("tool_input_json")))


def same(a, b):
    if a[0] != b[0]:
        return False
    for x, y in zip(a[1:], b[1:]):
        if x == y:
            continue
        if scan.MASK in x and conv_build._masked_agrees(x, y):
            continue
        if scan.MASK in y and conv_build._masked_agrees(y, x):
            continue
        return False
    return True


def typed(rs):
    return [norm(r["command"] if r.get("kind") == "command" and r.get("command") else r.get("content"))
            for r in rs if r.get("typed_by_developer") and r.get("copy_of") is None]


def compare(a, b):
    A, B = [item(r) for r in a], [item(r) for r in b]
    n = min(len(A), len(B))
    first = next((i for i in range(n) if not same(A[i], B[i])), None)
    out = {"rows": [len(A), len(B)]}
    ta, tb = typed(a), typed(b)
    m = min(len(ta), len(tb))
    t_open = all(same(("", ta[i], ""), ("", tb[i], "")) for i in range(m))
    out["typed"] = [len(ta), len(tb)]
    if first is None:
        out["verdict"] = "identical" if len(A) == len(B) else "prefix"
        return out
    out["first_diff"] = first
    out["at"] = [{"role": x.get("role"), "kind": x.get("kind"), "actor": x.get("actor"), "turn_type": x.get("turn_type"),
                  "chars": len(x.get("content") or ""), "tool_chars": len(x.get("tool_input_json") or "")}
                 for x in (a[first], b[first])]
    texts = lambda rs: {norm(r.get("content")) for r in rs if r.get("actor") == "agent" and r.get("turn_type") == "assistant_response" and norm(r.get("content"))}
    sa, sb = texts(a), texts(b)
    out["agent_texts"] = [len(sa), len(sb), len(sa & sb)]
    out["verdict"] = "typed-same" if m and t_open else "different"
    return out


results, tally = [], defaultdict(Counter)
for kind, stratum, size, (d, k) in sample:
    a, ea = read((d["source"], d["source_id"]))
    b, eb = read((k["source"], k["source_id"]))
    rec = {"kind": kind, "stratum": stratum, "stratum_size": size, "copy": d["session_key"], "kept": k["session_key"],
           "agents": [d["agent"], k["agent"]]}
    if a is None or b is None:
        rec.update(verdict="unreadable", why=[ea, eb])
    else:
        rec.update(compare(a, b))
    results.append(rec)
    tally[(kind, stratum)][rec["verdict"]] += 1
    print(kind, stratum, rec["verdict"], rec.get("rows"), rec.get("typed"), rec.get("first_diff"), rec.get("why"), flush=True)

summary = {}
for kind in ("exact", "near"):
    good = ("identical", "prefix") if kind == "exact" else ("identical", "prefix", "typed-same")
    per, num, den, wsum, wtot = {}, 0, 0, 0.0, 0
    for (k2, stratum), c in tally.items():
        if k2 != kind:
            continue
        n = sum(v for x, v in c.items() if x != "unreadable")
        ok = sum(c[x] for x in good)
        size = next(r["stratum_size"] for r in results if r["kind"] == kind and r["stratum"] == stratum)
        per[stratum] = {"verdicts": dict(c), "read": n, "good": ok, "wilson": wilson(ok, n), "population": size,
                        "identical": c["identical"]}
        num, den = num + ok, den + n
        if n:
            wsum, wtot = wsum + size * ok / n, wtot + size
    summary[kind] = {"good_means": good, "strata": per, "all": wilson(num, den), "good": num, "read": den,
                     "weighted_by_population": round(wsum / wtot, 4) if wtot else None}
OUT.write_text(json.dumps({"version": V.name, "seed": SEED, "copies_by_kind": dict(kinds), "summary": summary,
                           "pairs": results}, indent=1))
print(json.dumps(summary, indent=1))
