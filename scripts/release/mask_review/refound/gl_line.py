"""One line of a copied file, raw and decoded, in two versions: gitleaks findings with shapes and key-name context."""
import hashlib, itertools, re, sys, tempfile, shutil
from pathlib import Path
from collect import scan, conv_build
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
ok = {l.split()[0] for l in open(sys.argv[3]) if l.strip()}
N = int(sys.argv[4])
h = lambda s: hashlib.sha256(s.encode()).hexdigest()
def finds(text):
    w = Path(tempfile.mkdtemp(prefix="pairlogs-gl-"))
    d = Path(tempfile.mkdtemp(prefix="pairlogs-gl-in-")) / "conversations.jsonl"
    try:
        d.write_text(text)
        return [f for fl in scan.find_all([d], w, 1).values() for f in fl]
    finally:
        shutil.rmtree(w, ignore_errors=True); shutil.rmtree(d.parent, ignore_errors=True)
for label, p in (("masked", sys.argv[1]), ("before", sys.argv[2])):
    with open(p, encoding="utf-8", errors="surrogateescape") as fh:
        lines = list(itertools.islice(fh, N - 3, N + 2))   # lines N-2 .. N+2
    raw = lines[2]
    print(label, "raw line", N, "length", len(raw), "mask words", raw.count(scan.MASK), "sha", h(raw)[:12])
    fs = finds("".join(lines))
    print("  raw window findings:", [(f["rule"], f.get("line"), shape(f["secret"]), h(f["secret"]) in ok) for f in fs])
    for f in fs:
        L = "".join(lines).splitlines()[f["line"] - 1]
        i = L.find(f["secret"])
        ctx = L[max(0, i - 80):i] + "«" + shape(f["secret"]) + "»" + L[i + len(f["secret"]):i + len(f["secret"]) + 40]
        print("  context:", repr(re.sub(r"[A-Za-z0-9_\-]{20,}", lambda m: "«" + shape(m.group(0))[:12] + "…»", ctx)))
