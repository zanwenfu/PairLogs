"""Where the values to mask that masking does not reach stand: per value, the published file TruffleHog named and every
string column (or file content) of it that holds the value. Never prints a value."""
import hashlib, json, sys
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
cls_path, hits_path, mask_path, tier = sys.argv[1:5]
tier = Path(tier)
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
cls = json.load(open(cls_path))
mask = [json.loads(l) for l in open(mask_path)]
files = defaultdict(set)
for l in open(hits_path):
    h = json.loads(l); files[h["sha256"]].add(h["published_file"])
zero = [v for v in mask if cls[sha(v)]["occurrences"] == 0]
print(f"to mask but not reached: {len(zero)}")
where = Counter()
for v in zero:
    for f in sorted(files[sha(v)]):
        p = tier / f
        if not p.exists():
            where[(f.split("/")[0], "file not in the tier: " + f[:60])] += 1; continue
        if p.suffix != ".parquet":
            where[(f.split("/")[0], "a copied file (" + ("holds it" if v in p.read_text(errors="replace") else "does not hold it") + ")")] += 1; continue
        t = pq.read_table(p)
        found = []
        for fld in t.schema:
            if pa.types.is_string(fld.type) or pa.types.is_large_string(fld.type):
                if any(x and v in x for x in t[fld.name].to_pylist()):
                    found.append(fld.name)
        if "files" in t.schema.names:
            for fl in t["files"].to_pylist():
                for x in fl or []:
                    if x.get("content") and v in x["content"]: found.append("files.content"); break
                    if x.get("path") and v in x["path"]: found.append("files.path"); break
        where[(str(Path(f).parent), ",".join(sorted(set(found))) or "no column holds it")] += 1
for (d, c), n in where.most_common():
    print(f"  {n} values: {d} -> {c}")
