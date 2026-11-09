"""Where the values the scanner found only after masking stand: up to 2 places each, the 80 characters before and 40
after, readable only as words: a token holding a digit, a run of 20 or more letters, and an e-mail address are shown
by shape (letters a, digits 9), the value itself in «» by shape. Never a value.
argv: TIER VALUES.jsonl HITS.jsonl"""
import hashlib, json, re, sys
from collections import defaultdict
import pyarrow.parquet as pq
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
def safe(s):
    s = re.sub(r"[^\s@\"']+@[^\s@\"']+", lambda m: shape(m.group(0)), s)
    s = re.sub(r"[A-Za-z0-9_\-+/=.]*\d[A-Za-z0-9_\-+/=.]*", lambda m: shape(m.group(0)), s)
    return re.sub(r"[A-Za-z]{20,}", lambda m: shape(m.group(0)), s)
R = sys.argv[1].rstrip("/") + "/"
vals = {hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest(): v for v in (json.loads(l) for l in open(sys.argv[2]) if l.strip())}
where, det = defaultdict(set), {}
for l in open(sys.argv[3]):
    h = json.loads(l)
    if h["sha256"] in vals:
        where[h["published_file"]].add(h["sha256"]); det[h["sha256"]] = h["detector"] + "/" + h["decoder"]
def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values(): yield from strings(v)
    elif isinstance(x, list):
        for v in x: yield from strings(v)
order = sorted(vals)
for f, shas in sorted(where.items()):
    p = R + f
    src = (s for b in pq.ParquetFile(p).iter_batches(batch_size=128) for row in b.to_pylist() for s in strings(row)) \
        if p.endswith(".parquet") else open(p, encoding="utf-8", errors="surrogateescape")
    got = defaultdict(list); n = defaultdict(int)
    for text in src:
        for s in shas:
            v = vals[s]
            i = text.find(v)
            while i >= 0:
                n[s] += 1
                if len(got[s]) < 2:
                    got[s].append(safe(text[max(0, i - 80):i]) + "«" + shape(v)[:50] + "»" + safe(text[i + len(v):i + len(v) + 40]))
                i = text.find(v, i + 1)
    for s in sorted(shas, key=order.index):
        print(f"#{order.index(s):02d} {det[s]} {f.split('/')[0]}/{f.split('/')[1]} len {len(vals[s])} written {n[s]} times")
        for c in got[s]:
            print("    " + repr(c)[:330])
