"""The masked values TruffleHog still finds after the mask: counts by how it read them (decoder), by detector, by the
kind of file, by whether the hit is a connection string; counts only, never a value."""
import hashlib, json, sys
from collections import Counter
vals = {hashlib.sha256(json.loads(l).encode()).hexdigest() for l in open(sys.argv[1]) if l.strip()}
print("values given to mask:", len(vals))
hits = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
re_ = [h for h in hits if h["sha256"] in vals]
print("hits on them:", len(re_), "distinct values:", len({h["sha256"] for h in re_}))
by = lambda key: Counter(key(h) for h in {h["sha256"]: h for h in re_}.values())
print("by decoder (a value once, its first hit):", by(lambda h: h["decoder"]))
dec = {}
for h in re_:
    dec.setdefault(h["sha256"], set()).add(h["decoder"])
print("values found as written (PLAIN) in some hit:", sum("PLAIN" in s for s in dec.values()))
print("by detector:", by(lambda h: h["detector"]))
print("by file kind:", by(lambda h: h["published_file"].split("/")[0] + ("/" + h["published_file"].split("/")[1] if h["published_file"].startswith("copied") else "")))
print("placeholder password:", by(lambda h: h.get("placeholder_password")))
print("verified:", Counter(h.get("verified") for h in re_))
