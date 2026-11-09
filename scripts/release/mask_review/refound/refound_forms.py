"""The refound values found in no written form by refound_where.py: their JSON- and HTML-unescaped forms searched in
the strings of the files their hits name; for a hit read through base64, the base64 runs that decode to text holding
it. Shapes and counts only, never a value."""
import base64, binascii, hashlib, html, json, re, sys
from collections import defaultdict
import pyarrow.parquet as pq
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
KEEP = {"key", "api", "apikey", "api_key", "token", "secret", "password", "passwd", "pwd", "user", "username", "host",
        "jdbc", "mongodb", "postgres", "postgresql", "mysql", "gemini", "google", "env", "export", "nexmo", "vonage"}
keep = lambda s: re.sub(r"[A-Za-z0-9_]+", lambda m: m.group(0) if m.group(0).lower() in KEEP else shape(m.group(0)), s)
R = sys.argv[3].rstrip("/") + "/"
idx = [int(x) for x in sys.argv[4].split(",")]
vals = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
by_sha = {hashlib.sha256(v.encode()).hexdigest(): v for v in vals}
want = defaultdict(set); det = {}
for l in open(sys.argv[2]):
    h = json.loads(l)
    if h["sha256"] in by_sha:
        want[h["published_file"]].add(h["sha256"]); det.setdefault(h["sha256"], set()).add(h["detector"] + "/" + h["decoder"])
order = sorted(det)
pick = {order[i] for i in idx}
def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values(): yield from strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x: yield from strings(v)
def unjson(v):
    try:
        return json.loads(chr(34) + v + chr(34))
    except ValueError:
        return None
B64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
for f, shas in sorted(want.items()):
    shas = [s for s in shas if s in pick]
    if not shas:
        continue
    p = R + f
    src = (s for b in pq.ParquetFile(p).iter_batches(batch_size=256) for row in b.to_pylist() for s in strings(row)) \
        if p.endswith(".parquet") else open(p, encoding="utf-8", errors="surrogateescape")
    forms = {s: {"value": by_sha[s], "json-unescaped": unjson(by_sha[s]), "html-unescaped": html.unescape(by_sha[s])} for s in shas}
    hit = defaultdict(int); ctx = {}; b64 = defaultdict(int); b64ctx = {}
    for text in src:
        for s in shas:
            for k, v in forms[s].items():
                if v and v in text:
                    hit[(s, k)] += 1
                    i = text.find(v); ctx.setdefault((s, k), (keep(text[max(0, i - 50):i]), shape(v)[:60], keep(text[i + len(v):i + len(v) + 20])))
            if "BASE64" in " ".join(det[s]):
                for m in B64.finditer(text):
                    run = m.group(0)
                    for off in range(4):
                        piece = run[off:]; piece = piece[:len(piece) // 4 * 4]
                        try:
                            raw = base64.b64decode(piece)
                        except (binascii.Error, ValueError):
                            continue
                        t = raw.decode("latin-1")
                        if by_sha[s] in t:
                            b64[s] += 1
                            i = t.find(by_sha[s])
                            printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in raw) / max(1, len(raw))
                            b64ctx.setdefault(s, (len(run), round(printable, 2), keep(t[max(0, i - 60):i]), keep(t[i + len(by_sha[s]):i + len(by_sha[s]) + 30]), keep(text[max(0, m.start() - 60):m.start()])))
                            break
    for s in shas:
        print(f"#{order.index(s):02d}", f, sorted(det[s]), "len", len(by_sha[s]), "shape", shape(by_sha[s])[:70])
        for k in forms[s]:
            if hit[(s, k)]:
                print("   ", k, hit[(s, k)], "context", repr(ctx[(s, k)])[:300])
        if s in b64ctx:
            print("    in base64 runs:", b64[s], "run length, printable share, decoded before, after, text before the run:", repr(b64ctx[s])[:400])
        if not any(hit[(s, k)] for k in forms[s]) and s not in b64ctx:
            print("    no form found")
