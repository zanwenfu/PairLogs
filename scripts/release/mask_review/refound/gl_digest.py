"""The flagged text of the one finding left (a path piece cut where gitleaks splits a long line): candidates by the
finding's match shape in raw line N, each with its count in the line, shape and digest (never the text)."""
import hashlib, re, sys
from collections import Counter
shape = lambda v: re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))
N, want = int(sys.argv[2]), int(sys.argv[3])
with open(sys.argv[1], encoding="utf-8", errors="surrogateescape") as fh:
    for n, L in enumerate(fh, 1):
        if n == N:
            break
c = Counter(m.group(2) for m in re.finditer(r"([A-Za-z]{6}_[A-Za-z]{3}\.[A-Za-z]{2}, )([A-Za-z]{10}/[A-Za-z]{3})(?=[A-Za-z])", L))
print("candidates by the match shape:", len(c))
for v, k in c.most_common():
    whole = len(re.findall(re.escape(v), L))
    print(" ", shape(v), "after the match shape", k, "in the line", whole, "has a digit", bool(re.search(r"\d", v)),
          "digest", hashlib.sha256(v.encode()).hexdigest(), "<- the finding" if whole == want else "")
