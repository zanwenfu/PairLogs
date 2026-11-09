"""The connection strings to mask that occur nowhere as written: each found by its longest piece (6+ letters or digits)
in its flagged file's texts, shown in context with every piece of it replaced by its shape. Never prints a value."""
import hashlib, json, re, sys
from collections import defaultdict
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
cls_path, hits_path, mask_path, tier = sys.argv[1:5]
tier = Path(tier)
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
cls = json.load(open(cls_path))
files = defaultdict(set)
for l in open(hits_path):
    h = json.loads(l); files[h["sha256"]].add(h["published_file"])
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
def texts(p):
    t = pq.read_table(p); out = []
    for f in t.schema:
        if pa.types.is_string(f.type) or pa.types.is_large_string(f.type):
            out += [x for x in t[f.name].to_pylist() if x]
    if "files" in t.schema.names:
        out += [x["content"] for fl in t["files"].to_pylist() for x in (fl or []) if x.get("content")]
    return out
for v in map(json.loads, open(mask_path)):
    s = sha(v)
    if cls[s]["occurrences"] != 0 or cls[s]["kind"] != "connection string": continue
    pieces = sorted(set(re.findall(r"[A-Za-z0-9_%.-]{6,}", v)), key=len, reverse=True)
    shown = None
    for f in sorted(files[s]):
        p = tier / f
        if not p.exists() or p.suffix != ".parquet": continue
        T = "\n".join(texts(p))
        for piece in pieces:
            i = T.find(piece)
            if i >= 0:
                c = T[max(0, i - 70):i + len(piece) + 60]
                for q in pieces: c = c.replace(q, "«" + shape(q) + "»")
                shown = (f, piece, c); break
        if shown: break
    print(f"- len {len(v)}: " + (f"{shown[0]}: {shown[2]!r}"[:260] if shown else "no piece found"))
