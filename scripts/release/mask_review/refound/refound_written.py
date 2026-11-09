"""For refound values with no written form found: the written substring that reads as the value after HTML, URL or
JSON unescaping, searched from each place the value's first characters stand. Shapes and counts only."""
import hashlib, html, json, re, sys, urllib.parse
from collections import defaultdict
import pyarrow.parquet as pq
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
R = sys.argv[3].rstrip("/") + "/"
idx = [int(x) for x in sys.argv[4].split(",")]
vals = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
by_sha = {hashlib.sha256(v.encode()).hexdigest(): v for v in vals}
want = defaultdict(set); det = {}
for l in open(sys.argv[2]):
    h = json.loads(l)
    if h["sha256"] in by_sha:
        want[h["published_file"]].add(h["sha256"]); det.setdefault(h["sha256"], set()).add(h["detector"])
order = sorted(det)
pick = {order[i] for i in idx}
def unjson(t):
    try:
        return json.loads(chr(34) + t + chr(34))
    except ValueError:
        return None
DEC = {"html": html.unescape, "url": urllib.parse.unquote, "json": unjson,
       "html+json": lambda t: html.unescape(unjson(t) or ""), "json+html": lambda t: unjson(html.unescape(t)) or ""}
def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values(): yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x: yield from strings(v)
for f, shas in sorted(want.items()):
    shas = [s for s in shas if s in pick]
    if not shas:
        continue
    p = R + f
    src = (s for b in pq.ParquetFile(p).iter_batches(batch_size=256) for row in b.to_pylist() for s in strings(row)) \
        if p.endswith(".parquet") else open(p, encoding="utf-8", errors="surrogateescape")
    found = defaultdict(set); starts = defaultdict(int)
    for text in src:
        for s in shas:
            v = by_sha[s]; head = v[:4]
            for m in re.finditer(re.escape(head), text):
                starts[s] += 1
                a = m.start()
                for b in range(a + 4, min(len(text), a + 8 * len(v)) + 1):
                    w = text[a:b]
                    for k, fn in DEC.items():
                        if fn(w) == v and w != v:
                            found[s].add((k, w))
    for s in shas:
        print(f"#{order.index(s):02d}", f, sorted(det[s]), "shape", shape(by_sha[s]), "places its first 4 characters stand:", starts[s])
        for k, w in sorted(found[s]):
            print("    written form via", k, "shape", shape(w), "digest", hashlib.sha256(w.encode()).hexdigest()[:16])
        if not found[s]:
            print("    no written form")
