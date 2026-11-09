"""For every value the count pass would mask: its first place in its flagged file, the 50 characters before it, and a
class from those characters: secret-like (a key, token, secret or password name right before), id-like (an id, uuid,
turn, session ... name right before), or other. Writes the classes privately (sha256 -> class, kind, occurrences); prints
counts and samples with every flagged value replaced by its shape. Never prints a value."""
import hashlib, json, os, random, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow.parquet as pq
sys.path.insert(0, sys.argv[1] + "/scripts/release")
from sort_hits import kind
tier, per_path, hits_path, vals_path, out = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
per = {r["sha256"]: r["occurrences"] for r in json.load(open(per_path))["by value"]}
vals = {sha(v): v for v in map(json.loads, open(vals_path))}
hits, files = {}, defaultdict(set)
for l in open(hits_path):
    h = json.loads(l); hits.setdefault(h["sha256"], h); files[h["sha256"]].add(h["published_file"])
SECRET = re.compile(r"(?i)(token|secret|passw(or)?d|pwd|api[_-]?key|apikey|auth(orization)?|bearer|credential|private[_-]?key|access[_-]?key|client[_-]?secret|webhook|dsn)[\"'\]]?\s*[:=]\s*[\"'`]?\s*(bearer\s+)?$")
IDLIKE = re.compile(r"(?i)(_id|\bid|uuid|turn|session|request|trace|workspace|thread|message|call|run|project|org|account|tenant|item)[\"'\]]?\s*[:=]\s*[\"'`]?$|(mcp__|/|:)$")
FREE = ("content", "tool_input_json", "command", "file_path", "pattern")
byfile = defaultdict(list)
for s in per:
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
            if i >= 0:
                ctx[s] = (x[max(0, i - 50):i], x[i + len(v):i + len(v) + 20]); break
cls = {}
for s in per:
    if s not in ctx: cls[s] = "not found"; continue
    before = ctx[s][0].replace("\\\"", "\"")
    cls[s] = "secret-like" if SECRET.search(before) else "id-like" if IDLIKE.search(before) else "other"
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fo:
    json.dump({s: {"class": cls[s], "kind": kind(hits[s]), "occurrences": per[s]} for s in per}, fo)
tab = defaultdict(Counter); occ = defaultdict(Counter)
for s in per:
    tab[kind(hits[s])][cls[s]] += 1; occ[kind(hits[s])][cls[s]] += per[s]
for k in tab:
    print(f"{k}: " + ", ".join(f"{c} {tab[k][c]} values ({occ[k][c]} occurrences)" for c in ("secret-like", "id-like", "other", "not found") if tab[k][c]))
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
allv = sorted(vals.values(), key=len, reverse=True)
def hide(t):
    for v in allv:
        if len(v) >= 8 and v in t: t = t.replace(v, "«" + shape(v) + "»")
    return t
rnd = random.Random("value-contexts-20261105")
for c, n in (("secret-like", 25), ("other", 40), ("id-like", 15)):
    pool = sorted(s for s in per if cls[s] == c and kind(hits[s]) == "near a service name")
    print(f"\n== {c}, near a service name: {n} of {len(pool)} at random")
    for s in rnd.sample(pool, min(n, len(pool))):
        b, a = ctx[s]
        print(f"- {per[s]}x {hits[s]['detector']}: " + repr(hide(b + vals[s] + a))[:170])
