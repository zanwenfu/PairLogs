"""The 'other' values (no key name, no id name right before): set aside by shape the ones that cannot be a secret
(a UUID, a 40- or 64-character hex hash, a piece inside a run of base64), and print every remaining one in context with
each flagged value replaced by its shape. Never prints a value."""
import hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow.parquet as pq
tier, cls_path, hits_path, vals_path = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
LIMIT = int(sys.argv[5])
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
cls = json.load(open(cls_path))
vals = {sha(v): v for v in map(json.loads, open(vals_path))}
hits, files = {}, defaultdict(set)
for l in open(hits_path):
    h = json.loads(l); hits.setdefault(h["sha256"], h); files[h["sha256"]].add(h["published_file"])
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}|[0-9A-F]{40}|[0-9A-F]{64}")
B64 = re.compile(r"[A-Za-z0-9+/]")
FREE = ("content", "tool_input_json", "command", "file_path", "pattern")
other = [s for s, c in cls.items() if c["class"] == "other"]
byfile = defaultdict(list)
for s in other:
    fs = sorted(f for f in files[s] if f != "?")
    if fs: byfile[fs[0]].append(s)
ctx = {}
for f, ss in byfile.items():
    p = tier / f
    if not p.exists(): continue
    if p.suffix == ".parquet":
        t = pq.read_table(p)
        texts = [x for c in FREE if c in t.schema.names for x in t[c].to_pylist() if x]
        if "files" in t.schema.names:
            texts += [x["content"] for fl in t["files"].to_pylist() for x in (fl or []) if x.get("content")]
    else:
        texts = [p.read_text(errors="replace")]
    for s in ss:
        v = vals[s]
        for x in texts:
            i = x.find(v)
            if i >= 0: ctx[s] = (x[max(0, i - 60):i], x[i + len(v):i + len(v) + 30]); break
why = {}
for s in other:
    v = vals[s]
    b, a = ctx.get(s, ("", ""))
    if UUID.fullmatch(v): why[s] = "a UUID"
    elif HEX.fullmatch(v): why[s] = "a hex hash (40 or 64)"
    elif b[-3:] and a[:3] and all(B64.match(c) for c in b[-3:] + a[:3]) and sum(1 for c in b[-60:] if c in "+/") >= 2: why[s] = "inside base64"
    else: why[s] = "to read"
print("other values:", dict(Counter(why.values())))
print("occurrences by group:", {g: sum(cls[s]["occurrences"] for s in other if why[s] == g) for g in set(why.values())})
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
allv = sorted(vals.values(), key=len, reverse=True)
def hide(t):
    for v in allv:
        if len(v) >= 8 and v in t: t = t.replace(v, "«" + shape(v) + "»")
    return t
left = sorted((s for s in other if why[s] == "to read"), key=lambda s: (hits[s]["detector"], -cls[s]["occurrences"]))
print(f"\n== to read: {len(left)} (showing {min(LIMIT, len(left))})")
for s in left[:LIMIT]:
    b, a = ctx.get(s, ("", ""))
    print(f"- {cls[s]['occurrences']}x {hits[s]['detector']} len {len(vals[s])}: " + (repr(hide(b + vals[s] + a))[:190] if s in ctx else "(not found)"))
