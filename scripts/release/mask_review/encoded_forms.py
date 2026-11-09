"""For each value to mask that occurs nowhere as written: TruffleHog's decoder for it, and which encoded form (JSON
escaped once or twice, slashes escaped, ASCII escapes) its flagged file holds. Writes the encoded forms found (private,
mode 600). Never prints a value."""
import hashlib, json, os, sys
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
cls_path, hits_path, mask_path, tier, out = sys.argv[1:6]
tier = Path(tier)
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
cls = json.load(open(cls_path))
mask = [json.loads(l) for l in open(mask_path)]
files, dec = defaultdict(set), defaultdict(Counter)
for l in open(hits_path):
    h = json.loads(l); files[h["sha256"]].add(h["published_file"]); dec[h["sha256"]][h.get("decoder")] += 1
def forms(v):
    j1 = json.dumps(v, ensure_ascii=False)[1:-1]
    j1a = json.dumps(v)[1:-1]
    j2 = json.dumps(j1, ensure_ascii=False)[1:-1]
    j3 = json.dumps(j2, ensure_ascii=False)[1:-1]
    out = {"json once": j1, "json once ascii": j1a, "json twice": j2, "json three times": j3,
           "slashes escaped": v.replace("/", "\\/"), "json once, slashes escaped": j1.replace("/", "\\/")}
    u = v
    for n in (1, 2, 3):   # the scan reads cells JSON-encoded: its value may carry escapes the text does not
        try:
            u2 = json.loads('"' + u + '"')
        except ValueError:
            break
        if u2 == u: break
        out[f"unescaped {n}x"] = u = u2
    t = v.rstrip("\\")
    if t != v: out["a trailing backslash cut"] = t
    import re as _re
    runs = sorted(_re.findall(r"[A-Za-z0-9]{16,}", v), key=len, reverse=True)
    if runs and runs[0] != v: out["its longest run of letters and digits (16+)"] = runs[0]
    return out
def texts(p):
    if p.suffix != ".parquet":
        return [p.read_text(errors="replace")]
    t = pq.read_table(p); out = []
    for f in t.schema:
        if pa.types.is_string(f.type) or pa.types.is_large_string(f.type):
            out += [x for x in t[f.name].to_pylist() if x]
    if "files" in t.schema.names:
        out += [x["content"] for fl in t["files"].to_pylist() for x in (fl or []) if x.get("content")]
    return out
zero = [v for v in mask if cls[sha(v)]["occurrences"] == 0]
cache, got, found, nofound = {}, Counter(), set(), []
print("decoders of the not-reached values:", dict(sum((dec[sha(v)] for v in zero), Counter())))
for v in zero:
    hit = None
    for f in sorted(files[sha(v)]):
        p = tier / f
        if not p.exists(): continue
        if f not in cache: cache[f] = "\n".join(texts(p))
        T = cache[f]
        if v in T: hit = "as written"; break
        for name, e in forms(v).items():
            if e != v and e in T:
                hit = name; found.add(e); break
        if hit: break
    got[hit or "no form found"] += 1
    if not hit:
        nofound.append(v)
print("forms found:", dict(got))
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fo:
    for e in sorted(found): fo.write(json.dumps(e) + "\n")
print(f"encoded forms written: {len(found)}")

import re
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
print("no form found (shape, its files, decoder, kind):")
for v in nofound:
    s_ = sha(v)
    print(f"  len {len(v)} {shape(v)[:60]!r} {sorted(files[s_])[:2]} {dict(dec[s_])} {cls[s_]['kind']}")
