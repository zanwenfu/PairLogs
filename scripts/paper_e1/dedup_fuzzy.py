"""Read-only, after dedup_contain.py: for each copy whose text items its kept session holds less than 90% of, whether
the missing items are the kept's items rewritten (an export's own masking or formatting: 90%+ alike, on their first
2,000 characters, by difflib) or text the kept session does not have. Counts and session keys only (no text).

python dedup_fuzzy.py VERSION_DIR CONTAIN_JSON OUT PROCS   (env: ERRATA_STATE_ROOT; the ledger is opened read-only)"""
import difflib
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from collect import config, copied_sessions
from collect.model import canonical_agent

from dedup_contain import ReadOnlyLedger, items, read

CUT, ALIKE = 2000, 0.9


def fuzzy(a: Counter, b: Counter):
    """Of a's items (with repeats), how many b holds exactly, and how many more a 90%-alike item of b's stands for."""
    exact, alike, missing = 0, 0, 0
    pool = [t[1][:CUT] for t in b]
    for k, v in a.items():
        m = min(v, b[k])
        exact += m
        if v == m:
            continue
        x = k[1][:CUT]
        hit = False
        for y in pool:
            if abs(len(y) - len(x)) > 0.2 * max(len(x), len(y)):
                continue
            s = difflib.SequenceMatcher(None, x, y, autojunk=False)
            if s.real_quick_ratio() >= ALIKE and s.quick_ratio() >= ALIKE and s.ratio() >= ALIKE:
                hit = True
                break
        if hit:
            alike += v - m
        else:
            missing += v - m
    n = exact + alike + missing
    return {"n": n, "exact": exact, "alike": alike, "missing": missing}


def job(task):
    p, copy_row, kept_row = task
    a, _ = read(copy_row)
    b, _ = read(kept_row)
    if a is None or b is None:
        return {**p, "fuzzy": None}
    return {**p, "fuzzy": fuzzy(items(a)[0], items(b)[0])}


def main():
    V, IN, OUT, PROCS = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])
    d = json.load(open(IN))
    todo = [p for p in d["pairs"] if "unreadable" not in p and p.get("text_held") is not None and p["text_held"] < 0.9]
    need = {tuple(k.split(":", 1)) for p in todo for k in (p["copy"], p["kept"])}
    print(f"pairs {len(todo):,}; sessions {len(need):,}", flush=True)
    ledger = ReadOnlyLedger(config.load().ledger)
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
    tasks = [(p, rows.get(tuple(p["copy"].split(":", 1))), rows.get(tuple(p["kept"].split(":", 1)))) for p in todo]
    tasks = [t for t in tasks if t[1] is not None and t[2] is not None]
    with ProcessPoolExecutor(PROCS) as pool:
        results = list(pool.map(job, tasks, chunksize=2))
    by = {}
    for r in results:
        f = r["fuzzy"]
        key = f"{r['kind']} {r['pair']}"
        c = by.setdefault(key, Counter())
        if f is None or not f["n"]:
            c["unreadable"] += 1
            continue
        held = (f["exact"] + f["alike"]) / f["n"]
        c["held once rewrites count (90%+)" if held >= 0.9 else "partly (50-90%)" if held >= 0.5 else "not held (<50%)"] += 1
    for k, c in sorted(by.items()):
        print(k, dict(c), flush=True)
    OUT.write_text(json.dumps({"version": V.name, "summary": {k: dict(c) for k, c in by.items()}, "pairs": results},
                              indent=1))


if __name__ == "__main__":
    main()
