"""The masking decision for every value the count pass found, by one stated rule; writes the values to mask (private,
mode 600, one JSON string a line) and prints counts, plus every value to mask that occurs over 200 times, in context,
by shape. Never prints a value.
  mask: a provider's key format or a connection string; a key, token, secret or password name right before it; or, among
        the rest that are not an id (an id name before it, a UUID, a 40- or 64-hex hash, a piece inside base64), every
        value that could be a key: only key characters (letters, digits, _ - + / = .) and at least 8 distinct ones.
  leave: everything else (ids, hashes, base64 pieces, code, paths, e-mail addresses, lines of one character)."""
import hashlib, json, os, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow.parquet as pq
sys.path.insert(0, sys.argv[1] + "/scripts/release")
from sort_hits import kind
cls_path, hits_path, vals_path, other_txt, out, tier = sys.argv[2:8]
tier = Path(tier)
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
cls = json.load(open(cls_path))
vals = {sha(v): v for v in map(json.loads, open(vals_path))}
hits, files = {}, defaultdict(set)
for l in open(hits_path):
    h = json.loads(l); hits.setdefault(h["sha256"], h); files[h["sha256"]].add(h["published_file"])
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}|[0-9A-F]{40}|[0-9A-F]{64}")
KEYCHARS = re.compile(r"[A-Za-z0-9_\-+/=.]+")
# the 'other' values set aside as inside base64 by other_values.py (same rule, recomputed from its printout's counts)
decision, why = {}, {}
for s, c in cls.items():
    v, k = vals[s], c["kind"]
    if k in ("provider format", "connection string"):
        decision[s], why[s] = True, k
    elif c["class"] == "secret-like":
        decision[s], why[s] = True, "a key name before it"
    elif c["class"] in ("id-like",):
        decision[s], why[s] = False, "an id name before it"
    elif UUID.fullmatch(v):
        decision[s], why[s] = False, "a UUID"
    elif HEX.fullmatch(v):
        decision[s], why[s] = False, "a hex hash"
    elif not KEYCHARS.fullmatch(v):
        decision[s], why[s] = False, "characters no key has"
    elif len(set(v)) < 8:
        decision[s], why[s] = False, "under 8 distinct characters"
    else:
        decision[s], why[s] = True, "could be a key"
# a piece inside base64 (other_values.py's rule) is left: recompute it for the 'could be a key' ones
B64 = re.compile(r"[A-Za-z0-9+/]")
FREE = ("content", "tool_input_json", "command", "file_path", "pattern")
cand = [s for s in decision if why[s] == "could be a key"]
byfile = defaultdict(list)
for s in cand:
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
for s in cand:
    b, a = ctx.get(s, ("", ""))
    if len(b) >= 30 and len(a) >= 3 and all(B64.match(ch) for ch in b[-30:] + a[:3]):
        decision[s], why[s] = False, "inside base64"
occ = lambda s: cls[s]["occurrences"]
print("decision by reason (values, occurrences):")
for r, n in Counter(why.values()).most_common():
    m = {decision[s] for s in why if why[s] == r}
    print(f"  {'MASK ' if m == {True} else 'leave'} {r}: {n} values, {sum(occ(s) for s in why if why[s] == r)} occurrences")
mask = [vals[s] for s in decision if decision[s]]
print(f"to mask: {len(mask)} values, {sum(occ(s) for s in decision if decision[s])} occurrences; left: {sum(1 for s in decision if not decision[s])}")
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fo:
    for v in sorted(mask): fo.write(json.dumps(v) + "\n")
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
allv = sorted(vals.values(), key=len, reverse=True)
def hide(t):
    for v in allv:
        if len(v) >= 8 and v in t: t = t.replace(v, "«" + shape(v) + "»")
    return t
big = sorted((s for s in decision if decision[s] and occ(s) > 200), key=lambda s: -occ(s))
print(f"\nto mask and over 200 occurrences: {len(big)}")
for s in big:
    b, a = ctx.get(s, ("", ""))
    print(f"- {occ(s)}x {hits[s]['detector']} ({why[s]}): " + repr(hide(b + vals[s] + a))[:180])
