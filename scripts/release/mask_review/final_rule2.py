"""The final list of values to mask, from final_rule.py's list: (1) of the values kept only because they could be a key,
those with no digit are left (a word, a name, a path or a variable: a random key has digits), and so are those read by
hand as ids (sha256s given); (2) the forms the scan read them in that the text holds instead (encoded_forms.py); (3) the
password of every connection string, alone, where it is not a placeholder and is as random as a key (8+ distinct
characters, a digit; a connection string broken over lines occurs nowhere whole). A connection string with no password
in it is left. Writes the list (private, mode 600); prints counts only."""
import hashlib, json, os, re, sys
from collections import Counter
sys.path.insert(0, sys.argv[1] + "/scripts/release")
from second_scan import placeholder_password
mask_path, cls_path, enc_path, read_ids_path, out = sys.argv[2:7]   # [7]: KEPT.jsonl
sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
cls = json.load(open(cls_path))
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}|[0-9A-F]{40}|[0-9A-F]{64}")
KEYCHARS = re.compile(r"[A-Za-z0-9_\-+/=.]+")
read_ids = {l.strip() for l in open(read_ids_path) if l.strip()}
final, why = {}, Counter()
for v in map(json.loads, open(mask_path)):
    c = cls[sha(v)]
    could = c["kind"] == "near a service name" and c["class"] != "secret-like"
    if could and not re.search(r"\d", v):
        why["left: no digit (a word, name, path or variable)"] += 1; continue
    if could and sha(v) in read_ids:
        why["left: read by hand as an id"] += 1; continue
    if c["kind"] == "connection string" and not re.search(r"://[^:/@\s]*:[^@\s]+@|\b(?i:password|pwd)=[^;&\s]+", v):
        why["left: a connection string with no password in it"] += 1; continue
    final[v] = "kept"
    why["mask: " + ("a key format or connection string" if not could else "could be a key, with digits")] += 1
if len(sys.argv) > 7:   # the kept values alone, before the encoded forms and passwords: encoded_forms.py reads it
    fd = os.open(sys.argv[7], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fo:
        for v in sorted(final): fo.write(json.dumps(v) + "\n")
for e in map(json.loads, open(enc_path)):
    if len(e) >= 8 and e not in final:
        final[e] = "an encoded form"; why["mask: the form the text holds"] += 1
for v in list(final):
    if cls.get(sha(v), {}).get("kind") != "connection string": continue
    m = re.search(r"://[^:/@\s]*:([^@\s]+)@", v) or re.search(r"\b(?i:password|pwd)=([^;&\s]+)", v)
    if not m: continue
    p = m.group(1)
    if (len(p) >= 8 and len(set(p)) >= 8 and re.search(r"\d", p) and placeholder_password("x://u:" + p + "@h") is False
            and not re.search(r"[{}<>$*]", p) and p not in final):   # as random as a key: a word is no password to mask
        final[p] = "a connection string's password"; why["mask: a connection string's password, alone"] += 1
for r, n in sorted(why.items()): print(f"  {r}: {n}")
bad = [v for v in final if len(v) < 8 or "[REDACTED]" in v or len(re.sub(r"(?i)\[?redacted\]?", "", v)) < 8]
print(f"final: {len(final)} values; unsafe among them (refused by the masking tool): {len(bad)}")
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fo:
    for v in sorted(final):
        if v not in bad: fo.write(json.dumps(v) + "\n")
