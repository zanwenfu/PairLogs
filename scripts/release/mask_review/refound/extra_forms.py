"""The written forms of refound values that the 319-value list missed (11-05 evening), to mask with it: the two JDBC
strings JSON-unescaped (they run over a line break), the URI with its password unquoted, and the base64 runs whose
text holds the two Gemini keys (a database dump, masked whole). Writes OUT (one JSON string a line, mode 600); prints
shapes, lengths and digests only."""
import base64, binascii, hashlib, json, re, sys, urllib.parse
import pyarrow.parquet as pq
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
vals = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
R = sys.argv[2].rstrip("/") + "/"
out = sys.argv[3]
dig = lambda v: hashlib.sha256(v.encode()).hexdigest()
by_shape = lambda pat: [v for v in vals if re.fullmatch(pat, shape(v))]
jdbc = [v for v in vals if v.startswith("jdbc:") and "\\n" in v]
uri = by_shape(r"aaaa://aaaa:aaaa%9aaaaa@aaaa")
gem = [v for v in vals if len(v) == 39 and v.startswith("AIza")]
print("JDBC values with a written line break:", len(jdbc), "URI:", len(uri), "Gemini-format values:", len(gem))
extra = [json.loads(chr(34) + v + chr(34)) for v in jdbc] + [urllib.parse.unquote(v) for v in uri]
B64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values(): yield from strings(v)
    elif isinstance(x, list):
        for v in x: yield from strings(v)
runs = {}
p = R + "copied/openhands-feedback/OpenHands__openhands-feedback/data/train-00000-of-00001.parquet"
for b in pq.ParquetFile(p).iter_batches(batch_size=256):
    for row in b.to_pylist():
        for text in strings(row):
            if len(text) < 1000:
                continue
            for m in B64.finditer(text):
                run = m.group(0)
                for off in range(4):
                    piece = run[off:]; piece = piece[:len(piece) // 4 * 4]
                    try:
                        t = base64.b64decode(piece).decode("latin-1")
                    except (binascii.Error, ValueError):
                        continue
                    k = [g for g in gem if g in t]
                    if k:
                        runs[run] = runs.get(run, 0) + 1
                        print("  a run of", len(run), "characters holds", len(k), "Gemini-format values; digest", dig(run)[:16])
                        break
extra += sorted(runs)
for v in extra:
    print("extra:", len(v), "characters, shape", shape(v)[:60], "digest", dig(v)[:16], "line break inside:", "\n" in v)
import os
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    for v in extra:
        f.write(json.dumps(v) + "\n")
print("written", len(extra), "values")
# RE2 on the longest: one quoted literal of it, compiled and matched as the masker does
import pyarrow as pa, pyarrow.compute as pc
big = max(extra, key=len)
arr = pa.array(["x " + big + " y", "none"], pa.large_string())
c = pc.count_substring_regex(arr, "\\Q" + big + "\\E")
print("RE2 on the longest extra value:", c.to_pylist(), pc.replace_substring_regex(arr, "\\Q" + big + "\\E", "[REDACTED]").to_pylist()[0][:20])
