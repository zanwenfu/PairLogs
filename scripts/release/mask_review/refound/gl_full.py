"""A whole raw file through the check scanner, in two versions: each finding not on the not-secrets list, with its
shape and where its text sits in its line (what precedes and follows, shaped)."""
import hashlib, re, sys, tempfile, shutil
from pathlib import Path
from collect import scan
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
ok = {l.split()[0] for l in open(sys.argv[3]) if l.strip()}
h = lambda s: hashlib.sha256(s.encode()).hexdigest()
def finds(p):
    w = Path(tempfile.mkdtemp(prefix="pairlogs-gl-"))
    try:
        return [f for fl in scan.find_all([Path(p)], w, 1).values() for f in fl]
    finally:
        shutil.rmtree(w, ignore_errors=True)
for label, p in (("masked", sys.argv[1]), ("before", sys.argv[2])):
    fs = [f for f in finds(p) if h(f["secret"]) not in ok]
    print(label, "findings not on the list:", len(fs), flush=True)
    for f in fs:
        print("  ", f["rule"], "line", f.get("line"), "shape", shape(f["secret"]), "match shape", shape(f.get("match", ""))[:60])
        with open(p, encoding="utf-8", errors="surrogateescape") as fh:
            for n, L in enumerate(fh, 1):
                if n == f.get("line"):
                    break
        k = [m.start() for m in re.finditer(re.escape(f["secret"]), L)]
        print("   occurrences in its line:", len(k))
        for i in k[:3]:
            before, after = L[max(0, i - 60):i], L[i + len(f["secret"]):i + len(f["secret"]) + 30]
            keep = lambda s: re.sub(r"[A-Za-z0-9]{6,}", lambda m: m.group(0) if m.group(0).lower() in ("token", "secret", "apikey", "api_key", "password", "access", "credential", "credentials", "authorization", "bearer") else shape(m.group(0)), s)
            print("   ", repr(keep(before)), "«" + shape(f["secret"]) + "»", repr(shape(after)))
