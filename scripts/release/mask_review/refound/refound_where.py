"""Where the masked values TruffleHog still finds stand as written: in each file its hits name, every string value
(Parquet cells, nested; JSON lines as raw lines) searched for the value as written and with any escape a writer may
have used (scan.escape_tolerant). Prints per value an index, its detector, and counts and the shape of the written form
(letters a, digits 9, the rest kept), never the value."""
import hashlib, json, re, sys
from collections import defaultdict
import pyarrow.parquet as pq
from collect import scan
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
R = sys.argv[3].rstrip("/") + "/"
vals = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
by_sha = {hashlib.sha256(v.encode()).hexdigest(): v for v in vals}
want = defaultdict(set); det = {}
for l in open(sys.argv[2]):
    h = json.loads(l)
    if h["sha256"] in by_sha:
        want[h["published_file"]].add(h["sha256"]); det.setdefault(h["sha256"], set()).add(h["detector"] + "/" + h["decoder"])
order = sorted(det)
def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values(): yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x: yield from strings(v)
for f, shas in sorted(want.items()):
    pats = {s: (by_sha[s], scan.escape_tolerant(by_sha[s]), max(re.findall(r"[A-Za-z0-9]+", by_sha[s]), key=len)) for s in shas}
    lit = defaultdict(int); tol = defaultdict(int); form = {}
    p = R + f
    src = (s for b in pq.ParquetFile(p).iter_batches(batch_size=256) for row in b.to_pylist() for s in strings(row)) \
        if p.endswith(".parquet") else open(p, encoding="utf-8", errors="surrogateescape")
    for text in src:
        for s, (v, pat, run) in pats.items():
            if v in text:
                lit[s] += 1
            if run in text or "%" in text or "\\u" in text:
                m = pat.search(text)
                if m:
                    tol[s] += 1
                    form.setdefault(s, (shape(m.group(0))[:90], shape(text[max(0, m.start() - 40):m.start()])))
    for s in sorted(shas, key=order.index):
        print(f"#{order.index(s):02d}", f.split("/")[0], f.split("/")[1], sorted(det[s]), "len", len(by_sha[s]),
              "as written", lit[s], "with escapes", tol[s], "form", repr(form.get(s, ("-",))[0]), "before", repr(form.get(s, ("", "-"))[1]), flush=True)
