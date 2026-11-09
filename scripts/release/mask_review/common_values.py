"""The values the count pass would mask, by kind and by how often they occur; and where the most frequent stand,
each flagged value in the text replaced by its shape. Never prints a value."""
import hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow.parquet as pq
sys.path.insert(0, sys.argv[1] + "/scripts/release")
from sort_hits import kind
tier, per_path, hits_path, vals_path = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
TOP = int(sys.argv[6])
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
per = {r["sha256"]: r["occurrences"] for r in json.load(open(per_path))["by value"]}
vals = {sha(v): v for v in map(json.loads, open(vals_path))}
hits, finds, files, verified = {}, Counter(), defaultdict(set), set()
for l in open(hits_path):
    h = json.loads(l); s = h["sha256"]
    hits.setdefault(s, h); finds[s] += 1; files[s].add(h["published_file"])
    if h.get("verified"): verified.add(s)
band = lambda n: "0" if n == 0 else "1" if n == 1 else "2-10" if n <= 10 else "11-100" if n <= 100 else "101-1000" if n <= 1000 else ">1000"
tab, occ = defaultdict(Counter), Counter()
for s, n in per.items():
    k = kind(hits[s]); tab[k][band(n)] += 1; occ[k] += n
print(f"values to mask: {len(per)}; occurrences {sum(per.values())}; verified by TruffleHog: {len(verified & set(per))}")
for k in tab:
    print(f"  {k}: {sum(tab[k].values())} values, {occ[k]} occurrences; by occurrences {dict(sorted(tab[k].items()))}")
ratio = [s for s in per if per[s] > 10 * finds[s]]
print(f"over 10 times as often as flagged: {len(ratio)} values, {sum(per[s] for s in ratio)} occurrences; by kind {dict(Counter(kind(hits[s]) for s in ratio))}")
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
allv = sorted(vals.values(), key=len, reverse=True)
def hide(t):
    for v in allv:
        if len(v) >= 8 and v in t:
            t = t.replace(v, "«" + shape(v) + "»")
    return t
FREE = ("content", "tool_input_json", "command", "file_path", "pattern")
print("\nwhere the most frequent stand (60 characters before, 25 after):")
for s in sorted(per, key=lambda s: -per[s])[:TOP]:
    v, h = vals[s], hits[s]
    ctx = None
    for f in sorted(files[s]):
        p = tier / f
        if p.suffix != ".parquet" or not p.exists():
            if p.exists():
                t = p.read_text(errors="replace"); i = t.find(v)
                if i >= 0: ctx = t[max(0, i - 60):i + len(v) + 25]; break
            continue
        t = pq.read_table(p)
        cols = [c for c in FREE if c in t.schema.names]
        texts = [x for c in cols for x in t[c].to_pylist() if x and v in x]
        if not texts and "files" in t.schema.names:
            texts = [x["content"] for fl in t["files"].to_pylist() for x in (fl or []) if x.get("content") and v in x["content"]]
        if texts:
            i = texts[0].find(v); ctx = texts[0][max(0, i - 60):i + len(v) + 25]; break
    print(f"- {per[s]} times, {finds[s]} flagged, {h['detector']}, {kind(h)}: " + (repr(hide(ctx)) if ctx else "(not found in its flagged files)"))
